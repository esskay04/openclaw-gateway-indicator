# openclaw-gateway-indicator

A system tray indicator for the [OpenClaw](https://github.com/openclaw/openclaw) personal AI assistant gateway, running on Linux (X11/GNOME).

OpenClaw runs as a background daemon that bridges your AI agents to messaging platforms (Telegram, Gmail, etc.). This indicator gives you a persistent at-a-glance health check in your panel without having to open a terminal.

## The lobster

The tray icon is a 🦞 lobster whose color shifts with the gateway's state:

| Color | Meaning |
|-------|---------|
| 🟢 Green lobster | Gateway is active and running |
| 🟡 Amber lobster | Gateway is starting, restarting, or shutting down |
| 🔴 Red lobster (natural) | Gateway is down |

Color is applied by hue-rotating the emoji's pixel data at startup — no external image assets needed.

## Features

- Right-click menu: **Start / Stop / Restart / View Logs** / Quit
- Live uptime in the menu label (`Running — up 2h 14m`)
- Desktop notification when the service changes state
- Hover tooltip showing gateway port and openclaw version
- Graceful fallback to plain colored circles if the emoji font is unavailable

## Requirements

- Linux with X11 and a panel that supports AppIndicator (GNOME with AppIndicator extension, XFCE, KDE, etc.)
- Python 3 (system install — `/usr/bin/python3`)
- OpenClaw installed and configured with a `openclaw-gateway.service` user systemd unit

### Python dependencies

```
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 python3-pil fonts-noto-color-emoji
```

## Installation

Copy the script into your OpenClaw scripts directory (or anywhere convenient):

```
cp gateway-indicator.py ~/.openclaw/scripts/
```

### Autostart (GNOME/XFCE)

Create `~/.config/autostart/openclaw-gateway-indicator.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=OpenClaw Gateway Indicator
Exec=/usr/bin/python3 /home/YOUR_USER/.openclaw/scripts/gateway-indicator.py
Terminal=false
X-GNOME-Autostart-enabled=true
```

Replace `YOUR_USER` with your actual username.

### Manual launch

```
/usr/bin/python3 gateway-indicator.py &
```

## Configuration

Two constants near the top of the script are the main things you might want to adjust:

```python
SERVICE = "openclaw-gateway.service"   # systemd user service name
```

```python
# Hue shift applied to the lobster emoji per state (degrees, 0–360)
shifts = {"red": 0, "yellow": 50, "green": 100}
```

The hue values assume a naturally orange-red lobster (~20° hue). Increasing the green shift pushes it further toward true green; decreasing the yellow shift keeps it more orange.

## How it works

At startup, the indicator renders the 🦞 emoji via Pillow at the font's native 136×128px bitmap size, scales it to 48×48, then produces three hue-rotated copies saved as PNG tempfiles. These are hot-swapped as the service state changes. On exit, the tempfiles are cleaned up.

Service state is polled every 5 seconds via `systemctl --user is-active`.
