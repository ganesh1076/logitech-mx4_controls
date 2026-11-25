#!/usr/bin/env python3
"""
Return a Logitech HID++ 2.0 mouse's buttons and thumb wheel to native mode.

When logid (logiops) crashes or fails while adding a device, buttons it had
diverted to itself stay diverted: the mouse sends them as HID++ events that
nothing handles, so the gesture button and side scroll appear dead. This
script clears those diverts. Stop logid first, or it will divert them again.

Usage:
    mx-undivert.py               auto-detect Logitech devices
    mx-undivert.py /dev/hidrawN  a specific device

Needs read/write access to the hidraw node (run with sudo if you get
"Permission denied").
"""
import glob
import os
import select
import sys
import time

SW_ID = 0x0A
REPROG_CONTROLS_V4 = 0x1B04
THUMBWHEEL = 0x2150


class Device:
    def __init__(self, path, index):
        self.path, self.index = path, index
        self.fd = os.open(path, os.O_RDWR)

    def request(self, feat_idx, func, params=b"", timeout=1.5):
        msg = bytes([0x11, self.index, feat_idx, (func << 4) | SW_ID]) + params
        os.write(self.fd, msg.ljust(20, b"\0"))
        end = time.time() + timeout
        while time.time() < end:
            if not select.select([self.fd], [], [], max(0, end - time.time()))[0]:
                break
            d = os.read(self.fd, 64)
            if len(d) < 5 or d[1] != self.index:
                continue
            if d[0] == 0x11 and d[2] == 0xFF and d[3] == feat_idx:
                raise IOError(f"HID++ error 0x{d[5]:02x}")
            if d[0] == 0x10 and d[2] == 0x8F:
                raise IOError(f"HID++ 1.0 error 0x{d[5]:02x}")
            if d[2] == feat_idx and (d[3] & 0x0F) == SW_ID:
                return d[4:]
        raise TimeoutError("no response")

    def feature_index(self, feature_id):
        r = self.request(0, 0, bytes([feature_id >> 8, feature_id & 0xFF]))
        return r[0] or None

    def close(self):
        os.close(self.fd)


def logitech_hidraw_nodes():
    for sysdir in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            uevent = open(os.path.join(sysdir, "device/uevent")).read()
        except OSError:
            continue
        if ":0000046D:" in uevent.upper():
            name = next((l.split("=", 1)[1] for l in uevent.splitlines() if l.startswith("HID_NAME=")), "?")
            yield "/dev/" + os.path.basename(sysdir), name


def find_device(path):
    """Ping the direct (0xFF) and receiver-paired (1-6) indexes; return the first that answers."""
    for index in (0xFF, 1, 2, 3, 4, 5, 6):
        try:
            dev = Device(path, index)
        except OSError as e:
            print(f"  {path}: {e.strerror}")
            return None
        try:
            # First request may take a few hundred ms while the mouse wakes up.
            r = dev.request(0, 1, bytes([0, 0, 0x5A]), timeout=2.0 if index == 0xFF else 0.5)
            if r[0] >= 2:
                return dev
        except (IOError, TimeoutError):
            pass
        dev.close()
    return None


def undivert(dev):
    changed = 0
    idx = dev.feature_index(REPROG_CONTROLS_V4)
    if idx:
        count = dev.request(idx, 0)[0]
        for i in range(count):
            info = dev.request(idx, 1, bytes([i]))
            cid_hi, cid_lo = info[0], info[1]
            cid = (cid_hi << 8) | cid_lo
            rep = dev.request(idx, 2, bytes([cid_hi, cid_lo]))
            if rep[2] & 0x15:  # divert (0x01) / persistent divert (0x04) / raw XY (0x10)
                # flags: dvalid|pvalid|rawXY-valid with all values cleared
                dev.request(idx, 3, bytes([cid_hi, cid_lo, 0x0A | 0x20, 0, 0]))
                print(f"    button 0x{cid:x}: undiverted")
                changed += 1
    idx = dev.feature_index(THUMBWHEEL)
    if idx:
        if dev.request(idx, 1)[0] & 0x01:
            dev.request(idx, 2, bytes([0x00, 0x00]))
            print("    thumb wheel: back to native scrolling")
            changed += 1
    return changed


def main():
    paths = sys.argv[1:] or None
    candidates = [(p, "") for p in paths] if paths else list(logitech_hidraw_nodes())
    if not candidates:
        sys.exit("No Logitech hidraw devices found.")
    found = False
    for path, name in candidates:
        dev = find_device(path)
        if not dev:
            continue
        found = True
        print(f"{path} {name} (index 0x{dev.index:02x})")
        try:
            if not undivert(dev):
                print("    nothing was diverted")
        finally:
            dev.close()
    if not found:
        sys.exit("No HID++ 2.0 device answered. Is the mouse awake? Try moving it, or run with sudo.")


if __name__ == "__main__":
    main()
