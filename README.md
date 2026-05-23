# openclaw-gateway-indicator

System tray indicator for [OpenClaw](https://github.com/openclaw/openclaw) gateway service status on Linux (X11/GNOME).

## What it does

- Shows a 🦞 lobster in the system tray that color-shifts with service state:
  - **Green lobster** — gateway is active
  - **Amber/gold lobster** — gateway is activating, reloading, or deactivating
  - **Red lobster** (natural) — gateway is down
- Right-click menu: Start / Stop / Restart / View Logs / Quit
- Menu label shows live uptime when running (`Running — up 2h 14m`)
- Desktop notification on state transitions
- Hover tooltip shows gateway port and openclaw version

## Requirements

- Ubuntu / Debian with GNOME or XFCE (X11)
- `python3-gi` with AyatanaAppIndicator3 bindings
- `gir1.2-ayatanaappindicator3-0.1`
- `gir1.2-notify-0.7`
- `python3-pil` (Pillow)
- Noto Color Emoji font (`fonts-noto-color-emoji`)

```
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 python3-pil fonts-noto-color-emoji
```

## Installation

```
cp gateway-indicator.py ~/.openclaw/scripts/
```

Create `~/.config/autostart/openclaw-gateway-indicator.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=OpenClaw Gateway Indicator
Exec=/usr/bin/python3 /home/YOUR_USER/.openclaw/scripts/gateway-indicator.py
Terminal=false
X-GNOME-Autostart-enabled=true
```

Or launch manually:

```
/usr/bin/python3 gateway-indicator.py &
```

## Notes

- Hue shift values for yellow (+50°) and green (+100°) are tunable constants at the top of the script.
- Falls back to plain colored circles if Pillow or the Noto Color Emoji font is unavailable.
- Polls `systemctl --user is-active openclaw-gateway.service` every 5 seconds.
