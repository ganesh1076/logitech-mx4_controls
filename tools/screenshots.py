#!/usr/bin/python3
"""
Regenerate the README screenshots in docs/screenshots/.

Opens MX Control, switches to each tab and has GTK render the window
straight to PNG, so no desktop or other windows end up in the images.
A window appears for a few seconds and then closes on its own.

Usage:
    tools/screenshots.py [--defaults] [--size WxH] [OUT_DIR]

    --defaults   render with default settings instead of your own
                 (uses an empty temporary config directory)
    --size WxH   window size (default 960x900; narrower widths truncate
                 the tab labels)
    OUT_DIR      where to write the PNGs (default: docs/screenshots)

Run it with the system Python (/usr/bin/python3): it needs the GTK 4 and
libadwaita bindings (python3-gi), which a conda or venv Python usually lacks.
"""
import argparse
import atexit
import importlib.util
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ["buttons", "scroll", "pointer", "device"]

ap = argparse.ArgumentParser(description="Regenerate the README screenshots.")
ap.add_argument("out_dir", nargs="?", default=os.path.join(REPO, "docs", "screenshots"))
ap.add_argument("--defaults", action="store_true", help="use default settings, not yours")
ap.add_argument("--size", default="960x900", help="window size, e.g. 960x900")
args = ap.parse_args()
width, height = (int(v) for v in args.size.lower().split("x"))
os.makedirs(args.out_dir, exist_ok=True)

tmp_config = None
if args.defaults:
    # Must be set before mx_control is loaded: it reads XDG_CONFIG_HOME at import time.
    tmp_config = tempfile.mkdtemp(prefix="mx-control-shots-")
    os.environ["XDG_CONFIG_HOME"] = tmp_config
    atexit.register(shutil.rmtree, tmp_config, ignore_errors=True)

try:
    import gi  # noqa: F401
except ImportError:
    sys.exit("python3-gi not found. Run this with /usr/bin/python3 (sudo apt install python3-gi).")

spec = importlib.util.spec_from_file_location("mx_control", os.path.join(REPO, "mx_control.py"))
mx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mx)
# A separate app ID, so a running MX Control doesn't swallow this window
# (GTK apps are single-instance per ID).
mx.APP_ID += ".Screenshots"

from gi.repository import Adw, GLib, Gtk  # noqa: E402  (loaded with versions set by mx_control)

_set_default_size = Adw.ApplicationWindow.set_default_size
Adw.ApplicationWindow.set_default_size = lambda self, w, h: _set_default_size(self, width, height)


def find(widget, cls):
    if isinstance(widget, cls):
        return widget
    child = widget.get_first_child()
    while child:
        found = find(child, cls)
        if found:
            return found
        child = child.get_next_sibling()
    return None


def render(win, path):
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, win.get_width(), win.get_height())
    win.get_renderer().render_texture(snap.to_node(), None).save_to_png(path)
    print(f"saved {path} ({win.get_width()}x{win.get_height()})")


state = {"page": -1}


def step():
    wins = [w for w in Gtk.Window.list_toplevels() if w.get_title() == "MX Control" and w.get_mapped()]
    if not wins:
        return True  # window not up yet
    win = wins[0]
    if state["page"] >= 0:
        render(win, os.path.join(args.out_dir, f"{PAGES[state['page']]}.png"))
    state["page"] += 1
    if state["page"] >= len(PAGES):
        win.get_application().quit()
        return False
    find(win, Adw.ViewStack).set_visible_child_name(PAGES[state["page"]])
    win.set_focus(None)  # no focus highlight in the shot
    return True


def give_up():
    print("timed out waiting for the MX Control window", file=sys.stderr)
    if tmp_config:
        shutil.rmtree(tmp_config, ignore_errors=True)
    os._exit(1)  # skip GTK teardown; the main loop may be wedged


GLib.timeout_add(1200, step)  # gives each tab time to lay out before rendering
GLib.timeout_add_seconds(30, give_up)
sys.exit(mx.run_gui())
