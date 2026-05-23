#!/usr/bin/env /usr/bin/python3
import gi
import subprocess
import tempfile
import os
import re
import signal
import colorsys
import datetime
from PIL import Image, ImageDraw, ImageFont

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
gi.require_version("Notify", "0.7")
from gi.repository import Gtk, GLib, AyatanaAppIndicator3, Notify

SERVICE = "openclaw-gateway.service"

icon_files = {}
_gateway_port = None   # cached at startup by GatewayIndicator.__init__

# Fallback SVG circles used if Pango/Cairo emoji rendering fails
_FALLBACK_SVGS = {
    "green":  ('<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22">'
               '<circle cx="11" cy="11" r="8" fill="#2ecc71"/></svg>'),
    "yellow": ('<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22">'
               '<circle cx="11" cy="11" r="8" fill="#f1c40f"/></svg>'),
    "red":    ('<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22">'
               '<circle cx="11" cy="11" r="8" fill="#e74c3c"/></svg>'),
}


_EMOJI_FONT = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
_EMOJI_NATIVE_SIZE = 109   # only bitmap size supported by NotoColorEmoji.ttf
_ICON_SIZE = 48


def _render_lobster_base():
    """Return a 48×48 RGBA PIL Image of the 🦞 emoji."""
    font = ImageFont.truetype(_EMOJI_FONT, _EMOJI_NATIVE_SIZE)
    canvas = Image.new("RGBA", (136, 128), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).text((0, 0), "🦞", font=font, embedded_color=True)
    return canvas.resize((_ICON_SIZE, _ICON_SIZE), Image.LANCZOS)


def _hue_shift(img, shift_degrees):
    """Return new PIL Image with saturated pixels hue-rotated by shift_degrees."""
    shift = shift_degrees / 360.0
    out = []
    for r, g, b, a in img.getdata():
        if a == 0:
            out.append((r, g, b, a))
            continue
        hv, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
        if s > 0.15:
            hv = (hv + shift) % 1.0
        nr, ng, nb = colorsys.hsv_to_rgb(hv, s, v)
        out.append((int(nr * 255), int(ng * 255), int(nb * 255), a))
    result = Image.new("RGBA", img.size)
    result.putdata(out)
    return result


def build_icons():
    """Write 3 hue-shifted lobster PNGs to tempfiles; fall back to SVG circles on error."""
    try:
        base = _render_lobster_base()
        shifts = {"red": 0, "yellow": 50, "green": 100}
        for name, deg in shifts.items():
            img = _hue_shift(base, deg) if deg else base
            f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            img.save(f.name)
            f.close()
            icon_files[name] = f.name
    except Exception:
        for name, svg in _FALLBACK_SVGS.items():
            f = tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w")
            f.write(svg)
            f.close()
            icon_files[name] = f.name


def _port_accepting(port, timeout=1.5):
    """Return True if localhost:port accepts a TCP connection."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def gateway_state():
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE],
            capture_output=True, text=True, timeout=3,
        )
        s = r.stdout.strip()
        if s in ("activating", "reloading", "deactivating"):
            return "yellow"
        if s != "active":
            return "red"
        # Process is running — confirm it's actually accepting connections
        if _gateway_port and not _port_accepting(_gateway_port):
            return "yellow"
        return "green"
    except Exception:
        return "red"


def get_uptime():
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", SERVICE, "--property=ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=3,
        )
        raw = r.stdout.strip().split("=", 1)[-1]
        if not raw:
            return ""
        # Format: "Fri 2026-05-22 18:30:45 NZST" — strip weekday and tz
        parts = raw.split()
        dt_str = f"{parts[1]} {parts[2]}"
        dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        elapsed = int((datetime.datetime.now() - dt).total_seconds())
        if elapsed < 0:
            return ""
        h, rem = divmod(elapsed, 3600)
        m = rem // 60
        return f"up {h}h {m}m" if h else f"up {m}m"
    except Exception:
        return ""


def get_port():
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", SERVICE, "--property=ExecStart"],
            capture_output=True, text=True, timeout=3,
        )
        m = re.search(r"--port\s+(\d+)", r.stdout)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


def get_version():
    try:
        r = subprocess.run(["openclaw", "--version"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or r.stderr.strip() or "?"
    except Exception:
        return "?"


def open_log_window():
    win = Gtk.Window(title="OpenClaw Gateway — Live Log")
    win.set_default_size(860, 420)

    tv = Gtk.TextView()
    tv.set_editable(False)
    tv.set_cursor_visible(False)
    tv.set_monospace(True)
    tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    buf = tv.get_buffer()

    sw = Gtk.ScrolledWindow()
    sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    sw.add(tv)
    win.add(sw)
    win.show_all()

    proc = subprocess.Popen(
        ["journalctl", "--user", "-u", SERVICE, "-f", "--no-pager", "-n", "200"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    adj = sw.get_vadjustment()

    def _scroll_to_end():
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return False

    def on_output(source, condition):
        if condition & GLib.IO_IN:
            line = source.readline()
            if line:
                end = buf.get_end_iter()
                buf.insert(end, line.decode("utf-8", errors="replace"))
                GLib.idle_add(_scroll_to_end)
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            return False
        return True

    GLib.io_add_watch(proc.stdout, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_output)

    def on_destroy(_):
        proc.terminate()

    win.connect("destroy", on_destroy)


def notify_state(description):
    n = Notify.Notification.new("OpenClaw Gateway", description, "dialog-information")
    try:
        n.show()
    except Exception:
        pass


def svc(*args):
    subprocess.Popen(["systemctl", "--user"] + list(args) + [SERVICE])


class GatewayIndicator:
    def __init__(self):
        global _gateway_port
        Notify.init("OpenClaw Gateway")
        build_icons()
        self.current = None
        try:
            _gateway_port = int(get_port())
        except (ValueError, TypeError):
            _gateway_port = None

        self.ind = AyatanaAppIndicator3.Indicator.new(
            "openclaw-gateway",
            icon_files["red"],
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.ind.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()

        self.status_item = Gtk.MenuItem(label="OpenClaw Gateway")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)
        menu.append(Gtk.SeparatorMenuItem())

        for label, cmd in [("Start", "start"), ("Stop", "stop"), ("Restart", "restart")]:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _, c=cmd: svc(c))
            menu.append(item)

        logs_item = Gtk.MenuItem(label="View Logs")
        logs_item.connect("activate", lambda _: open_log_window())
        menu.append(logs_item)

        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit Indicator")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        menu.append(quit_item)

        menu.show_all()
        self.ind.set_menu(menu)

        port = get_port()
        version = get_version()
        self.ind.set_title(f"port {port} | v{version}")

        self.poll()
        GLib.timeout_add_seconds(5, self.poll)

    def poll(self):
        state = gateway_state()
        uptime = get_uptime() if state == "green" else ""
        label_map = {
            "green":  f"Running{' — ' + uptime if uptime else ''}",
            "yellow": "Restarting…",
            "red":    "Down",
        }
        self.status_item.set_label(f"OpenClaw Gateway — {label_map[state]}")

        if state != self.current:
            if self.current is not None:
                notify_state(label_map[state])
            self.current = state
            self.ind.set_icon_full(icon_files[state], state)
        return True


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())
    signal.signal(signal.SIGINT, lambda *_: Gtk.main_quit())
    GatewayIndicator()
    Gtk.main()
    for f in icon_files.values():
        try:
            os.unlink(f)
        except OSError:
            pass
