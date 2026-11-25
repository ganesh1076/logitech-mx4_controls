#!/usr/bin/env python3
"""
MX Control - a Logitech Options+-style configurator for Ubuntu.

The GUI edits your settings, generates /etc/logid.cfg and restarts the
logiops daemon (logid), which talks HID++ to the mouse. Pointer speed,
acceleration and natural scrolling go through GNOME's own settings and
apply instantly.

Usage:
    mx_control.py                 launch the GUI
    mx_control.py --print-config  print the logid.cfg your saved settings produce
"""
import copy
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading

APP_ID = "io.github.mxcontrol.MXControl"
CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "mx-control")
STATE_FILE = os.path.join(CONF_DIR, "settings.json")
PROFILE_DIR = os.path.join(CONF_DIR, "profiles")
STAGED_CFG = os.path.join(CONF_DIR, "logid.cfg")
SYSTEM_CFG = "/etc/logid.cfg"


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------
def kp(*keys):
    return {"type": "Keypress", "keys": list(keys)}


ACTIONS = [
    ("default", "Default (unchanged)", None),
    ("disabled", "Disabled", {"type": "None"}),
    ("gestures", "Gestures (see Gestures section)", "GESTURES"),
    ("custom", "Custom shortcut…", "CUSTOM"),
    ("left_click", "Left click", kp("BTN_LEFT")),
    ("middle_click", "Middle click", kp("BTN_MIDDLE")),
    ("right_click", "Right click", kp("BTN_RIGHT")),
    ("back", "Back", kp("BTN_SIDE")),
    ("forward", "Forward", kp("BTN_EXTRA")),
    ("copy", "Copy", kp("KEY_LEFTCTRL", "KEY_C")),
    ("cut", "Cut", kp("KEY_LEFTCTRL", "KEY_X")),
    ("paste", "Paste", kp("KEY_LEFTCTRL", "KEY_V")),
    ("undo", "Undo", kp("KEY_LEFTCTRL", "KEY_Z")),
    ("redo", "Redo", kp("KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_Z")),
    ("select_all", "Select all", kp("KEY_LEFTCTRL", "KEY_A")),
    ("overview", "Activities overview", kp("KEY_LEFTMETA")),
    ("app_grid", "Show applications", kp("KEY_LEFTMETA", "KEY_A")),
    ("show_desktop", "Show desktop", kp("KEY_LEFTMETA", "KEY_D")),
    ("ws_prev", "Previous workspace", kp("KEY_LEFTMETA", "KEY_PAGEUP")),
    ("ws_next", "Next workspace", kp("KEY_LEFTMETA", "KEY_PAGEDOWN")),
    ("switch_app", "Switch applications", kp("KEY_LEFTALT", "KEY_TAB")),
    ("tab_prev", "Previous tab", kp("KEY_LEFTCTRL", "KEY_PAGEUP")),
    ("tab_next", "Next tab", kp("KEY_LEFTCTRL", "KEY_PAGEDOWN")),
    ("tab_close", "Close tab", kp("KEY_LEFTCTRL", "KEY_W")),
    ("tab_reopen", "Reopen closed tab", kp("KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_T")),
    ("zoom_in", "Zoom in", kp("KEY_LEFTCTRL", "KEY_EQUAL")),
    ("zoom_out", "Zoom out", kp("KEY_LEFTCTRL", "KEY_MINUS")),
    ("zoom_reset", "Reset zoom", kp("KEY_LEFTCTRL", "KEY_0")),
    ("screenshot", "Screenshot", kp("KEY_SYSRQ")),
    ("lock", "Lock screen", kp("KEY_LEFTMETA", "KEY_L")),
    ("vol_up", "Volume up", kp("KEY_VOLUMEUP")),
    ("vol_down", "Volume down", kp("KEY_VOLUMEDOWN")),
    ("mute", "Mute", kp("KEY_MUTE")),
    ("play_pause", "Play / pause", kp("KEY_PLAYPAUSE")),
    ("next_track", "Next track", kp("KEY_NEXTSONG")),
    ("prev_track", "Previous track", kp("KEY_PREVIOUSSONG")),
    ("bright_up", "Brightness up", kp("KEY_BRIGHTNESSUP")),
    ("bright_down", "Brightness down", kp("KEY_BRIGHTNESSDOWN")),
    ("toggle_smartshift", "Toggle SmartShift (ratchet / free-spin)", {"type": "ToggleSmartShift"}),
    ("toggle_hires", "Toggle hi-res scrolling", {"type": "ToggleHiresScroll"}),
    ("cycle_dpi", "Cycle DPI presets", "CYCLE_DPI"),
    ("next_host", "Easy-Switch: next computer", {"type": "ChangeHost", "host": "next"}),
]
ACTION_MAP = {a[0]: a for a in ACTIONS}
# Gestures and thumb wheel can't nest gestures; "default" means "do nothing" there.
SUB_CHOICES = [("default", "Nothing", None)] + [a for a in ACTIONS if a[0] not in ("default", "gestures")]

BUTTONS = [  # key, label, default action
    ("middle", "Middle button (wheel click)", "default"),
    ("back", "Back button", "default"),
    ("forward", "Forward button", "default"),
    ("gesture", "Gesture button (thumb)", "gestures"),
    ("modeshift", "Mode-shift button (behind wheel)", "default"),
    ("haptic", "Haptic Sense panel / extra button", "default"),
]
GESTURE_DIRS = [
    ("None", "Tap (press and release)"),
    ("Up", "Hold + move up"),
    ("Down", "Hold + move down"),
    ("Left", "Hold + move left"),
    ("Right", "Hold + move right"),
]


def _a(action, custom=""):
    return {"action": action, "custom": custom}


DEFAULTS = {
    "device_name": "MX Master 4",
    # Control IDs confirmed on the MX Master 4 (0xc3 = gesture button, 0x1a0 = Haptic Sense panel).
    # MX Master 3/3S use the same IDs but have no 0x1a0. Verify yours with `sudo logid -v`.
    "cids": {"middle": "0x52", "back": "0x53", "forward": "0x56",
             "gesture": "0xc3", "modeshift": "0xc4", "haptic": "0x1a0"},
    "dpi": 1000,
    "cycle_dpis": "800, 1200, 1600, 2400",
    "smartshift_on": True,
    "smartshift_threshold": 20,
    "smartshift_torque": 50,
    "hires": True,
    "invert_scroll": False,
    "thumb_divert": False,
    "thumb_invert": False,
    "thumb_interval": 2,
    "thumb": {"left": _a("tab_prev"), "right": _a("tab_next"), "tap": _a("default")},
    "buttons": {k: _a(d) for k, _, d in BUTTONS},
    "gestures": {"None": _a("overview"), "Up": _a("app_grid"), "Down": _a("show_desktop"),
                 "Left": _a("ws_prev"), "Right": _a("ws_next")},
    "gesture_threshold": 50,
}

# --------------------------------------------------------------------------
# Shortcut parsing ("Ctrl+Shift+T" -> evdev key names)
# --------------------------------------------------------------------------
KEY_ALIASES = {
    "ctrl": "KEY_LEFTCTRL", "control": "KEY_LEFTCTRL", "rctrl": "KEY_RIGHTCTRL",
    "shift": "KEY_LEFTSHIFT", "rshift": "KEY_RIGHTSHIFT",
    "alt": "KEY_LEFTALT", "altgr": "KEY_RIGHTALT",
    "super": "KEY_LEFTMETA", "meta": "KEY_LEFTMETA", "win": "KEY_LEFTMETA", "cmd": "KEY_LEFTMETA",
    "tab": "KEY_TAB", "enter": "KEY_ENTER", "return": "KEY_ENTER",
    "esc": "KEY_ESC", "escape": "KEY_ESC", "space": "KEY_SPACE",
    "backspace": "KEY_BACKSPACE", "delete": "KEY_DELETE", "del": "KEY_DELETE",
    "insert": "KEY_INSERT", "home": "KEY_HOME", "end": "KEY_END",
    "pageup": "KEY_PAGEUP", "pgup": "KEY_PAGEUP", "pagedown": "KEY_PAGEDOWN", "pgdn": "KEY_PAGEDOWN",
    "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT",
    "print": "KEY_SYSRQ", "printscreen": "KEY_SYSRQ",
    "minus": "KEY_MINUS", "-": "KEY_MINUS", "plus": "KEY_EQUAL", "equal": "KEY_EQUAL", "=": "KEY_EQUAL",
    "comma": "KEY_COMMA", ",": "KEY_COMMA", "period": "KEY_DOT", ".": "KEY_DOT",
    "slash": "KEY_SLASH", "/": "KEY_SLASH", "semicolon": "KEY_SEMICOLON",
    "leftclick": "BTN_LEFT", "rightclick": "BTN_RIGHT", "middleclick": "BTN_MIDDLE",
}


def parse_shortcut(text):
    keys, bad = [], []
    for tok in (t.strip() for t in text.split("+")):
        if not tok:
            continue
        low, up = tok.lower(), tok.upper()
        if up.startswith(("KEY_", "BTN_")):
            keys.append(up)
        elif low in KEY_ALIASES:
            keys.append(KEY_ALIASES[low])
        elif re.fullmatch(r"[a-z0-9]", low):
            keys.append("KEY_" + up)
        elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", low):
            keys.append("KEY_" + up)
        else:
            bad.append(tok)
    return keys, bad


def parse_cid(text):
    t = (text or "").strip()
    if not t:
        return None
    try:
        return Hex(int(t, 0) if t.lower().startswith("0x") else int(t, 16))
    except ValueError:
        return None


def parse_dpis(text):
    vals = [min(8000, max(200, int(v))) for v in re.findall(r"\d+", text or "")]
    return vals or [800, 1600]


# --------------------------------------------------------------------------
# logid.cfg generation (libconfig syntax)
# --------------------------------------------------------------------------
class Hex(int):
    """Integer printed in hex in the config (for button CIDs)."""


def lc(v, ind=0):
    pad, end = "    " * (ind + 1), "    " * ind
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Hex):
        return hex(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, dict):
        body = "".join(f"{pad}{k}: {lc(x, ind + 1)};\n" for k, x in v.items())
        return "{\n" + body + end + "}"
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            return "(\n" + ",\n".join(pad + lc(x, ind + 1) for x in v) + "\n" + end + ")"
        return "[" + ", ".join(lc(x, ind) for x in v) + "]"
    raise TypeError(type(v))


def resolve_action(entry, s, warn, where):
    aid, custom = entry.get("action", "default"), entry.get("custom", "")
    spec = ACTION_MAP.get(aid, ACTION_MAP["default"])[2]
    if spec is None:
        return None
    if spec == "CUSTOM":
        keys, bad = parse_shortcut(custom)
        if bad:
            warn.append(f"{where}: unknown key(s) {', '.join(bad)}")
        if not keys:
            warn.append(f"{where}: custom shortcut is empty - left unchanged")
            return None
        return kp(*keys)
    if spec == "CYCLE_DPI":
        return {"type": "CycleDPI", "dpis": parse_dpis(s["cycle_dpis"])}
    if spec == "GESTURES":
        gestures = []
        for d, _ in GESTURE_DIRS:
            a = resolve_action(s["gestures"][d], s, warn, f"Gesture {d}")
            if a is None or a.get("type") == "Gestures":
                continue
            g = {"direction": d, "mode": "OnRelease"}
            if d != "None":
                g["threshold"] = int(s["gesture_threshold"])
            g["action"] = a
            gestures.append(g)
        return {"type": "Gestures", "gestures": gestures} if gestures else {"type": "None"}
    return copy.deepcopy(spec)


def build_config(s):
    warn = []
    dev = {"name": (s["device_name"] or "MX Master 4").strip()}
    dev["dpi"] = int(s["dpi"])
    dev["smartshift"] = {"on": bool(s["smartshift_on"]),
                         "threshold": int(s["smartshift_threshold"]),
                         "torque": int(s["smartshift_torque"])}
    dev["hiresscroll"] = {"hires": bool(s["hires"]), "invert": bool(s["invert_scroll"]), "target": False}

    tw = {"divert": bool(s["thumb_divert"]), "invert": bool(s["thumb_invert"])}
    if s["thumb_divert"]:
        for side in ("left", "right"):
            a = resolve_action(s["thumb"][side], s, warn, f"Thumb wheel {side}")
            if a:
                tw[side] = {"mode": "OnInterval", "interval": int(s["thumb_interval"]), "action": a}
        tap = resolve_action(s["thumb"]["tap"], s, warn, "Thumb wheel tap")
        if tap:
            tw["tap"] = tap
    dev["thumbwheel"] = tw

    buttons = []
    for key, label, _ in BUTTONS:
        entry = s["buttons"][key]
        cid = parse_cid(s["cids"].get(key, ""))
        if cid is None:
            if entry["action"] != "default":
                warn.append(f"{label}: no button ID (CID) set on the Device page - skipped")
            continue
        a = resolve_action(entry, s, warn, label)
        if a:
            buttons.append({"cid": cid, "action": a})
    if buttons:
        dev["buttons"] = buttons

    header = ("# Generated by MX Control - edits here are overwritten on the next Apply.\n"
              "# Reference: https://github.com/PixlOne/logiops/wiki/Configuration\n\n")
    return header + "devices: " + lc([dev]) + ";\n", warn


# --------------------------------------------------------------------------
# State, profiles, system helpers
# --------------------------------------------------------------------------
def deep_merge(base, over):
    r = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k in r and isinstance(r[k], dict) and isinstance(v, dict):
            r[k] = deep_merge(r[k], v)
        else:
            r[k] = v
    return r


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def load_state():
    return deep_merge(DEFAULTS, load_json(STATE_FILE))


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def clean_name(name):
    return re.sub(r"[^\w\- ]", "", name).strip()


def list_profiles():
    return sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(PROFILE_DIR, "*.json")))


def read_batteries():
    out = []
    for d in sorted(glob.glob("/sys/class/power_supply/hidpp_battery_*")):
        def rd(n):
            try:
                with open(os.path.join(d, n)) as f:
                    return f.read().strip()
            except OSError:
                return ""
        cap, lvl = rd("capacity"), rd("capacity_level")
        out.append((rd("model_name") or "Logitech device", f"{cap}%" if cap else (lvl or "unknown"), rd("status")))
    return out


def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)


def service_state():
    if not shutil.which("logid"):
        return "logiops is not installed (run install.sh)"
    return run(["systemctl", "is-active", "logid"])[1] or "unknown"


def solaar_running():
    return run(["pgrep", "-f", "solaar"])[0] == 0


def detect_device_names():
    names = []
    _, out, _ = run(["journalctl", "-u", "logid", "-b", "--no-pager", "-o", "cat", "-n", "1000"])
    names += re.findall(r"\[INFO\]\s+(.+?) detected", out)
    names += [b[0] for b in read_batteries()]
    seen, uniq = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
def run_gui():
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gio, GLib, Gtk

    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 4):
        print("MX Control needs libadwaita 1.4+ (Ubuntu 24.04 or newer).", file=sys.stderr)
        return 1

    CENTER = Gtk.Align.CENTER

    class App:
        def __init__(self):
            self.s = load_state()
            self.win = None
            self.app = Adw.Application(application_id=APP_ID)
            self.app.connect("activate", self.on_activate)

        # ---------- window ----------
        def on_activate(self, app):
            if self.win:
                self.win.present()
                return
            self.win = Adw.ApplicationWindow(application=app, title="MX Control")
            self.win.set_default_size(760, 860)
            self.toasts = Adw.ToastOverlay()
            self.tv = Adw.ToolbarView()
            self.header = Adw.HeaderBar()
            self.tv.add_top_bar(self.header)

            apply_btn = Gtk.Button(label="Apply")
            apply_btn.add_css_class("suggested-action")
            apply_btn.connect("clicked", lambda *_: self.apply())
            self.header.pack_end(apply_btn)

            prev = Gtk.Button(icon_name="document-properties-symbolic", tooltip_text="Preview generated logid.cfg")
            prev.connect("clicked", lambda *_: self.text_window("logid.cfg preview", build_config(self.s)[0]))
            self.header.pack_start(prev)

            reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text="Reset all settings to defaults")
            reset.connect("clicked", lambda *_: self.reset())
            self.header.pack_start(reset)

            self.toasts.set_child(self.tv)
            self.win.set_content(self.toasts)
            self.build()
            self.win.present()
            GLib.timeout_add_seconds(30, self.tick)

        def build(self, page_name=None):
            stack = Adw.ViewStack()
            for name, title, icon, fn in [
                ("buttons", "Buttons", "input-mouse-symbolic", self.page_buttons),
                ("scroll", "Scrolling", "object-flip-vertical-symbolic", self.page_scroll),
                ("pointer", "Pointer", "find-location-symbolic", self.page_pointer),
                ("device", "Device", "preferences-system-symbolic", self.page_device),
            ]:
                stack.add_titled_with_icon(fn(), name, title, icon)
            self.header.set_title_widget(Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE))
            self.tv.set_content(stack)
            if page_name:
                stack.set_visible_child_name(page_name)

        def toast(self, msg):
            self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(msg), timeout=4))

        # ---------- state helpers ----------
        def get(self, path):
            d = self.s
            for k in path:
                d = d[k]
            return d

        def set(self, path, v):
            d = self.s
            for k in path[:-1]:
                d = d[k]
            d[path[-1]] = v

        # ---------- row helpers ----------
        def group(self, page, title, desc=None):
            g = Adw.PreferencesGroup(title=title)
            if desc:
                g.set_description(desc)
            page.add(g)
            return g

        def switch(self, g, title, subtitle, path):
            r = Adw.SwitchRow(title=title, subtitle=subtitle or "")
            r.set_active(bool(self.get(path)))
            r.connect("notify::active", lambda w, _p: self.set(path, w.get_active()))
            g.add(r)
            return r

        def spin(self, g, title, subtitle, path, lo, hi, step):
            r = Adw.SpinRow.new_with_range(lo, hi, step)
            r.set_title(title)
            r.set_subtitle(subtitle or "")
            r.set_value(self.get(path))
            r.connect("notify::value", lambda w, _p: self.set(path, int(w.get_value())))
            g.add(r)
            return r

        def entry(self, g, title, path):
            r = Adw.EntryRow(title=title)
            r.set_text(str(self.get(path)))
            r.connect("changed", lambda w: self.set(path, w.get_text()))
            g.add(r)
            return r

        def action_picker(self, g, title, subtitle, path, choices):
            ids = [c[0] for c in choices]
            combo = Adw.ComboRow(title=title, subtitle=subtitle or "",
                                 model=Gtk.StringList.new([c[1] for c in choices]))
            combo.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
            combo.set_enable_search(True)
            cur = self.get(path + ("action",))
            combo.set_selected(ids.index(cur) if cur in ids else 0)

            ent = Adw.EntryRow(title=f"{title}: shortcut, e.g. Ctrl+Shift+T")
            ent.set_text(self.get(path + ("custom",)))
            ent.set_visible(cur == "custom")
            ent.connect("changed", lambda w: self.set(path + ("custom",), w.get_text()))

            def on_sel(w, _p):
                aid = ids[w.get_selected()]
                self.set(path + ("action",), aid)
                ent.set_visible(aid == "custom")

            combo.connect("notify::selected", on_sel)
            g.add(combo)
            g.add(ent)
            return combo

        # ---------- pages ----------
        def page_buttons(self):
            page = Adw.PreferencesPage()
            g = self.group(page, "Buttons", "Choose what each button does. "
                           "“Default” keeps the mouse’s built-in behaviour.")
            for key, label, _ in BUTTONS:
                cid = self.s["cids"].get(key) or "not set"
                self.action_picker(g, label, f"Button ID {cid}", ("buttons", key), ACTIONS)

            gg = self.group(page, "Gestures", "Hold the gesture button and move the mouse. "
                            "Applies to any button set to “Gestures”.")
            for d, label in GESTURE_DIRS:
                self.action_picker(gg, label, None, ("gestures", d), SUB_CHOICES)
            self.spin(gg, "Gesture distance", "How far to move before a direction counts (pixels)",
                      ("gesture_threshold",), 10, 400, 5)
            return page

        def page_scroll(self):
            page = Adw.PreferencesPage()
            g = self.group(page, "SmartShift", "Automatically switches the main wheel from "
                           "ratchet to free-spin when you flick it.")
            self.switch(g, "SmartShift", "Auto switch to free-spin", ("smartshift_on",))
            self.spin(g, "Sensitivity threshold", "Lower values switch to free-spin sooner",
                      ("smartshift_threshold",), 1, 255, 1)
            self.spin(g, "Ratchet force (torque)", "Strength of the ratchet feel",
                      ("smartshift_torque",), 1, 100, 1)

            g2 = self.group(page, "Main wheel")
            self.switch(g2, "Smooth scrolling (hi-res)", "High-resolution scroll events", ("hires",))
            self.switch(g2, "Invert scroll direction", "Done by the mouse itself; don’t combine "
                        "with Natural scrolling on the Pointer page", ("invert_scroll",))

            g3 = self.group(page, "Thumb wheel", "Off = normal horizontal scrolling. "
                            "On = the actions below.")
            self.switch(g3, "Invert thumb wheel", None, ("thumb_invert",))
            self.switch(g3, "Use custom thumb-wheel actions", None, ("thumb_divert",))
            self.spin(g3, "Thumb wheel step", "Wheel notches per action (higher = slower)",
                      ("thumb_interval",), 1, 20, 1)
            self.action_picker(g3, "Rotate left / up", None, ("thumb", "left"), SUB_CHOICES)
            self.action_picker(g3, "Rotate right / down", None, ("thumb", "right"), SUB_CHOICES)
            self.action_picker(g3, "Tap (touch-sensitive wheels only)", None, ("thumb", "tap"), SUB_CHOICES)
            return page

        def page_pointer(self):
            page = Adw.PreferencesPage()
            g = self.group(page, "Sensor (applied by logid)")
            self.spin(g, "DPI", "Pointer sensitivity on the sensor (200–8000)", ("dpi",), 200, 8000, 50)
            r = self.entry(g, "DPI presets for “Cycle DPI” (comma-separated)", ("cycle_dpis",))
            r.set_tooltip_text("Assign “Cycle DPI presets” to a button on the Buttons page.")

            src = Gio.SettingsSchemaSource.get_default()
            if src and src.lookup("org.gnome.desktop.peripherals.mouse", True):
                ms = Gio.Settings.new("org.gnome.desktop.peripherals.mouse")
                g2 = self.group(page, "Desktop pointer (GNOME, applies instantly)")

                row = Adw.ActionRow(title="Pointer speed")
                scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -1.0, 1.0, 0.05)
                scale.set_hexpand(True)
                scale.set_size_request(260, -1)
                scale.set_value(ms.get_double("speed"))
                scale.add_mark(0.0, Gtk.PositionType.BOTTOM, None)
                scale.connect("value-changed", lambda w: ms.set_double("speed", w.get_value()))
                row.add_suffix(scale)
                g2.add(row)

                profiles = ["default", "flat", "adaptive"]
                labels = ["System default", "Flat (no acceleration)", "Adaptive (accelerated)"]
                combo = Adw.ComboRow(title="Pointer acceleration", model=Gtk.StringList.new(labels))
                cur = ms.get_string("accel-profile")
                combo.set_selected(profiles.index(cur) if cur in profiles else 0)
                combo.connect("notify::selected",
                              lambda w, _p: ms.set_string("accel-profile", profiles[w.get_selected()]))
                g2.add(combo)

                nat = Adw.SwitchRow(title="Natural scrolling", subtitle="Content follows your finger")
                nat.set_active(ms.get_boolean("natural-scroll"))
                nat.connect("notify::active", lambda w, _p: ms.set_boolean("natural-scroll", w.get_active()))
                g2.add(nat)

                hand = Adw.SwitchRow(title="Left-handed (swap primary buttons)")
                hand.set_active(ms.get_boolean("left-handed"))
                hand.connect("notify::active", lambda w, _p: ms.set_boolean("left-handed", w.get_active()))
                g2.add(hand)
            return page

        def page_device(self):
            page = Adw.PreferencesPage()
            g = self.group(page, "Status")
            self.batt_row = Adw.ActionRow(title="Battery")
            self.svc_row = Adw.ActionRow(title="logid service")
            refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=CENTER, tooltip_text="Refresh")
            refresh.add_css_class("flat")
            refresh.connect("clicked", lambda *_: self.refresh_status())
            self.batt_row.add_suffix(refresh)
            g.add(self.batt_row)
            g.add(self.svc_row)
            self.refresh_status()

            g2 = self.group(page, "Device", "The name must match what logid reports exactly.")
            name = self.entry(g2, "Device name", ("device_name",))
            det = Gtk.Button(label="Detect", valign=CENTER)
            det.connect("clicked", lambda *_: self.detect(name))
            name.add_suffix(det)

            g3 = Adw.PreferencesGroup(title="Advanced: button IDs (CIDs)",
                                      description="Defaults match the MX Master 4 (and 3/3S, minus the haptic panel). "
                                      "To check yours, stop logid and run `sudo logid -v`; it lists each "
                                      "reprogrammable button with its CID. Leave blank to skip a button.")
            page.add(g3)
            for key, label, _ in BUTTONS:
                self.entry(g3, label, ("cids", key))

            g4 = self.group(page, "Service")
            for title, sub, label, cmd, msg in [
                ("Enable logid at startup", "Start the daemon now and on every boot", "Enable",
                 ["systemctl", "enable", "--now", "logid"], "logid enabled"),
                ("Restart logid", "Reload the current /etc/logid.cfg", "Restart",
                 ["systemctl", "restart", "logid"], "logid restarted"),
            ]:
                row = Adw.ActionRow(title=title, subtitle=sub)
                b = Gtk.Button(label=label, valign=CENTER)
                b.connect("clicked", lambda _b, c=cmd, m=msg: self.run_async(["pkexec"] + c, m, self.refresh_status))
                row.add_suffix(b)
                g4.add(row)
            logrow = Adw.ActionRow(title="Show logid log", subtitle="Useful when a setting doesn’t take effect")
            lb = Gtk.Button(label="Open", valign=CENTER)
            lb.connect("clicked", lambda *_: self.text_window("logid log", run(
                ["journalctl", "-u", "logid", "-b", "--no-pager", "-n", "300"])[1] or
                "No log available (you may need to be in the 'adm' group)."))
            logrow.add_suffix(lb)
            g4.add(logrow)

            g5 = self.group(page, "Profiles", "Save and switch between complete setups.")
            names = list_profiles()
            if names:
                pc = Adw.ComboRow(title="Saved profile", model=Gtk.StringList.new(names))
                load = Gtk.Button(label="Load", valign=CENTER)
                load.connect("clicked", lambda *_: self.load_profile(names[pc.get_selected()]))
                dele = Gtk.Button(icon_name="user-trash-symbolic", valign=CENTER, tooltip_text="Delete profile")
                dele.add_css_class("flat")
                dele.connect("clicked", lambda *_: self.delete_profile(names[pc.get_selected()]))
                pc.add_suffix(load)
                pc.add_suffix(dele)
                g5.add(pc)
            save = Adw.EntryRow(title="Save current settings as… (press Enter)")
            save.set_show_apply_button(True)
            save.connect("apply", lambda w: self.save_profile(w.get_text()))
            g5.add(save)

            g6 = self.group(page, "Not available on Linux")
            g6.add(Adw.ActionRow(
                title="Haptic feedback, Actions Ring, per-app profiles, Flow",
                subtitle="These Options+ features rely on Logitech’s closed software and aren’t "
                         "exposed by logiops. Buttons, gestures, DPI, SmartShift and both wheels are."))
            return page

        # ---------- behaviour ----------
        def refresh_status(self):
            bats = read_batteries()
            self.batt_row.set_subtitle(
                "; ".join(f"{n}: {lvl}" + (f" ({st.lower()})" if st else "") for n, lvl, st in bats)
                if bats else "No Logitech battery found. Is the mouse connected and awake?")
            self.svc_row.set_subtitle(service_state())

        def tick(self):
            if self.win and self.win.get_visible():
                self.refresh_status()
            return True

        def detect(self, row):
            names = detect_device_names()
            if not names:
                self.toast("Nothing detected. Start logid (Device page) and move the mouse.")
                return
            pick = next((n for n in names if "MX" in n), names[0])
            row.set_text(pick)
            self.toast(f"Detected: {pick}")

        def run_async(self, argv, ok_msg, then=None):
            def work():
                code, out, err = run(argv, timeout=120)
                GLib.idle_add(self.done, code, ok_msg, err or out, then)
            threading.Thread(target=work, daemon=True).start()

        def done(self, code, ok_msg, err, then):
            if code == 0:
                self.toast(ok_msg)
            elif code == 126:
                self.toast("Cancelled: administrator password needed")
            else:
                self.toast("Failed: " + (err.splitlines()[-1] if err else f"exit code {code}")[:160])
            if then:
                then()
            return False

        def apply(self):
            text, warns = build_config(self.s)
            save_json(STATE_FILE, self.s)
            with open(STAGED_CFG, "w") as f:
                f.write(text)
            for w in warns:
                self.toast(w)
            if solaar_running():
                self.toast("Solaar is running and may conflict with logid. Quit it for best results.")
            script = f"install -m 0644 {shlex.quote(STAGED_CFG)} {SYSTEM_CFG} && systemctl restart logid"
            self.run_async(["pkexec", "sh", "-c", script], "Settings applied to your mouse", self.refresh_status)

        def reset(self):
            self.s = copy.deepcopy(DEFAULTS)
            self.build()
            self.toast("Defaults restored. Press Apply to send them to the mouse.")

        def save_profile(self, raw):
            name = clean_name(raw)
            if not name:
                self.toast("Enter a profile name (letters, numbers, spaces, - or _)")
                return
            save_json(os.path.join(PROFILE_DIR, name + ".json"), self.s)
            self.build("device")
            self.toast(f"Saved profile “{name}”")

        def load_profile(self, name):
            self.s = deep_merge(DEFAULTS, load_json(os.path.join(PROFILE_DIR, name + ".json")))
            self.build("device")
            self.toast(f"Loaded “{name}”. Press Apply to send it to the mouse.")

        def delete_profile(self, name):
            try:
                os.remove(os.path.join(PROFILE_DIR, name + ".json"))
            except OSError as e:
                self.toast(str(e))
            self.build("device")

        def text_window(self, title, text):
            w = Adw.Window(transient_for=self.win, modal=True, title=title,
                           default_width=680, default_height=620)
            tv = Gtk.TextView(editable=False, monospace=True, left_margin=12, right_margin=12,
                              top_margin=12, bottom_margin=12)
            tv.get_buffer().set_text(text)
            sc = Gtk.ScrolledWindow(vexpand=True)
            sc.set_child(tv)
            box = Adw.ToolbarView()
            box.add_top_bar(Adw.HeaderBar())
            box.set_content(sc)
            w.set_content(box)
            w.present()

    return App().app.run(sys.argv[:1])


if __name__ == "__main__":
    if "--print-config" in sys.argv:
        cfg, warnings = build_config(load_state())
        print(cfg)
        for w in warnings:
            print("warning:", w, file=sys.stderr)
        sys.exit(0)
    sys.exit(run_gui())
