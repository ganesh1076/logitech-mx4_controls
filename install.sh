#!/usr/bin/env bash
# Installs MX Control and its dependencies on Ubuntu 24.04 or newer.
#
# logiops 0.3.3 (the version in Ubuntu's repos) cannot handle the MX Master 4:
# it truncates button IDs above 0xff, so adding the mouse fails with
# "Device timed out". If logid is missing or older than 0.3.4, this script
# offers to build logiops from source instead.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LOGIOPS_REPO="https://github.com/PixlOne/logiops"
LOGIOPS_REF="v0.3.5"

echo "==> Installing dependencies (sudo password needed)"
sudo apt update
sudo apt install -y python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 policykit-1 || \
sudo apt install -y python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pkexec

# Returns the installed logid version as "X.Y.Z", or nothing if logid is absent.
logid_version() {
  command -v logid >/dev/null 2>&1 || return 0
  local v
  v="$(logid --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
  [ -z "$v" ] && v="$(dpkg-query -W -f='${Version}' logiops 2>/dev/null | grep -oE '^[0-9]+\.[0-9]+\.[0-9]+' || true)"
  echo "$v"
}

version_ok() {  # true if $1 >= 0.3.4
  [ -n "$1" ] && [ "$(printf '%s\n0.3.4\n' "$1" | sort -V | head -1)" = "0.3.4" ]
}

build_logiops() {
  echo "==> Building logiops $LOGIOPS_REF from source"
  sudo apt install -y git cmake g++ pkg-config libevdev-dev libudev-dev libconfig++-dev libglib2.0-dev
  local src
  src="$(mktemp -d)"
  git clone --recursive --depth 1 --branch "$LOGIOPS_REF" "$LOGIOPS_REPO" "$src/logiops"
  cmake -S "$src/logiops" -B "$src/logiops/build" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$src/logiops/build" -j"$(nproc)"
  # The Ubuntu package owns the same systemd unit path; remove it first.
  # (apt remove keeps /etc/logid.cfg.)
  if dpkg -s logiops >/dev/null 2>&1; then sudo apt remove -y logiops; fi
  sudo cmake --install "$src/logiops/build"
  sudo systemctl daemon-reload
  rm -rf "$src"
}

V="$(logid_version)"
if version_ok "$V"; then
  echo "==> logiops $V already installed"
else
  if [ -z "$V" ]; then echo "==> logiops is not installed."; else echo "==> logiops $V is installed, but 0.3.4 or newer is needed for the MX Master 4."; fi
  read -r -p "    Build logiops $LOGIOPS_REF from source now? [Y/n] " ans
  case "${ans:-y}" in
    [Yy]*) build_logiops ;;
    *) echo "    Skipped. MX Control will not work until a working logid is installed." ;;
  esac
fi

echo "==> Installing MX Control to ~/.local/bin"
install -Dm755 "$HERE/mx_control.py" "$HOME/.local/bin/mx-control"

mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/mx-control.desktop" <<EOF
[Desktop Entry]
Name=MX Control
Comment=Configure your Logitech MX mouse
Exec=$HOME/.local/bin/mx-control
Icon=input-mouse
Terminal=false
Type=Application
Categories=Settings;HardwareSettings;
Keywords=logitech;mouse;mx;master;dpi;buttons;
EOF

if command -v logid >/dev/null 2>&1; then
  echo "==> Enabling the logid service"
  sudo systemctl enable --now logid || echo "   (couldn't start logid yet; use the Device page later)"
fi

if pgrep -x solaar >/dev/null 2>&1 || [ -e /etc/xdg/autostart/solaar.desktop ]; then
  echo
  echo "NOTE: Solaar is installed or running. It re-applies its own button and thumb wheel"
  echo "      settings at login, which undoes logid's. Consider: sudo apt remove solaar"
fi

echo
echo "Done. Open 'MX Control' from your app menu (or run: ~/.local/bin/mx-control)."
