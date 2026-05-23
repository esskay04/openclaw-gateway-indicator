# Changelog

## [Unreleased]

## [1.1.0] — 2026-05-24

### Fixed
- **Indicator stays green when gateway is not functional.** Previously, the indicator polled
  `systemctl --user is-active` only, which confirms the process is alive but not that it is
  serving requests. The gateway process remains running through a WiFi drop or silent failure,
  causing a false green state.

### Added
- TCP socket health check against the gateway port on every poll cycle. If the process is
  active but the port is not accepting connections, the indicator shows **yellow** rather than
  green, accurately reflecting a degraded state.
- Gateway port is cached at startup (parsed once from `systemctl show ExecStart`) so the
  health check adds no extra subprocess overhead per poll.

### State semantics after this change

| Icon | Meaning |
|------|---------|
| 🟢 Green lobster | Service active and port accepting connections |
| 🟡 Amber lobster | Service transitioning, or process running but port unresponsive |
| 🔴 Red lobster | Service not active |

---

## [1.0.0] — 2026-05-23

Initial release.

### Features
- 🦞 Lobster emoji system tray icon with hue-shifted color states (red / amber / green)
  rendered via Pillow at native bitmap size and downscaled to 48×48
- Right-click menu: Start / Stop / Restart / View Logs / Quit Indicator
- Live uptime in menu label (`Running — up 2h 14m`), updated every 5 seconds
- Desktop notification via `libnotify` on service state transitions
- Hover tooltip showing gateway port and openclaw version
- Autostart support via `~/.config/autostart/` desktop entry
- Graceful fallback to plain colored SVG circles if Pillow or Noto Color Emoji is unavailable
