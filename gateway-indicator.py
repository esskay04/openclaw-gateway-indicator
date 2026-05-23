#!/usr/bin/env /usr/bin/python3
import gi
import subprocess
import tempfile
import os
import re
import signal
import colorsys
import datetime
import threading
import collections
import time
import json
import urllib.request
from PIL import Image, ImageDraw, ImageFont

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
gi.require_version("Notify", "0.7")
from gi.repository import Gtk, GLib, AyatanaAppIndicator3, Notify, Gdk

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

# ---------------------------------------------------------------------------
# CEL (check-engine-light) state
# ---------------------------------------------------------------------------
_error_counts   = collections.defaultdict(list)   # fp -> [epoch timestamps]
_recent_errors  = collections.deque(maxlen=500)    # (ts, fp, raw_line)
_dismissed_fps  = set()                            # session-only dismissals
_always_dismiss = set()                            # persisted across restarts
_RECUR_WINDOW   = 300   # seconds
_RECUR_THRESH   = 3     # occurrences in window = "recurring"
_DISMISSED_FILE = os.path.expanduser("~/.openclaw/cel-dismissed.json")
_AUTH_FILE      = os.path.expanduser(
    "~/.openclaw/agents/main/agent/auth-profiles.json"
)
_OPENROUTER_KEY = None

_FP_STRIP = re.compile(r'\b(?:\d{1,5}|0x[0-9a-f]+)\b', re.I)

_ERROR_KWS = ("error", "critical", "exception", "traceback", "failed")

_PATTERNS = [
    (r'ECONNREFUSED',
     "The gateway is trying to reach a service that isn't running or is blocking connections."),
    (r'EADDRINUSE',
     "Something else is already using the port the gateway needs. Only one process can own a port at a time."),
    (r'ENOENT',
     "The gateway can't find a file it needs. It may have been deleted or never created."),
    (r'heap out of memory|JavaScript heap',
     "The gateway ran out of memory. Node.js hit its heap limit."),
    (r'ETIMEDOUT|timed out',
     "A network connection the gateway made took too long to respond."),
    (r'SyntaxError',
     "The gateway loaded a file that isn't valid JavaScript/JSON."),
    (r'MODULE_NOT_FOUND',
     "A required Node.js module is missing. A dependency may not be installed."),
    (r'certificate|SSL|TLS',
     "There's a problem with an HTTPS/TLS certificate — it may be expired or untrusted."),
    (r'permission denied|EACCES',
     "The gateway doesn't have permission to access a file or port it needs."),
    (r'SIGTERM|SIGKILL',
     "The gateway process was forcibly stopped, possibly by the OS or another process."),
]


# ---------------------------------------------------------------------------
# Lobster icon helpers
# ---------------------------------------------------------------------------

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


_CEL_FALLBACK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22">'
    '<polygon points="11,2 21,20 1,20" fill="#f39c12" stroke="#e67e22" stroke-width="1.5"/>'
    '<text x="11" y="17" text-anchor="middle" font-size="11" font-weight="bold" fill="#2c3e50">!</text>'
    '</svg>'
)


def build_cel_icon():
    """Write ⚠️ emoji PNG to a tempfile; fall back to SVG warning triangle."""
    try:
        font = ImageFont.truetype(_EMOJI_FONT, _EMOJI_NATIVE_SIZE)
        canvas = Image.new("RGBA", (136, 128), (0, 0, 0, 0))
        ImageDraw.Draw(canvas).text((0, 0), "⚠️", font=font, embedded_color=True)
        img = canvas.resize((_ICON_SIZE, _ICON_SIZE), Image.LANCZOS)
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(f.name)
        f.close()
        icon_files["cel"] = f.name
    except Exception:
        f = tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w")
        f.write(_CEL_FALLBACK_SVG)
        f.close()
        icon_files["cel"] = f.name


# ---------------------------------------------------------------------------
# Gateway status helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Live log window
# ---------------------------------------------------------------------------

def open_log_window():
    win = Gtk.Window(title="OpenClaw Gateway — Live Log")
    win.set_default_size(860, 420)

    tv = Gtk.TextView()
    tv.set_editable(False)
    tv.set_cursor_visible(False)
    tv.set_monospace(True)
    tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    buf = tv.get_buffer()
    err_meta_tag = buf.create_tag("error_meta", foreground="#e74c3c")
    err_body_tag = buf.create_tag("error_body", foreground="#e67e22")

    # Matches: "MMM DD HH:MM:SS hostname unit[pid]: " as group 1, rest as group 2
    _PREFIX_RE = re.compile(r'^(.*?\[\d+\]: )(.*)', re.DOTALL)

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
                text = line.decode("utf-8", errors="replace")
                if any(kw in text.lower() for kw in _ERROR_KWS):
                    m = _PREFIX_RE.match(text)
                    if m:
                        buf.insert_with_tags(buf.get_end_iter(), m.group(1), err_meta_tag)
                        buf.insert_with_tags(buf.get_end_iter(), m.group(2), err_body_tag)
                    else:
                        buf.insert_with_tags(buf.get_end_iter(), text, err_meta_tag)
                else:
                    buf.insert(buf.get_end_iter(), text)
                GLib.idle_add(_scroll_to_end)
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            return False
        return True

    GLib.io_add_watch(proc.stdout, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_output)

    def on_destroy(_):
        proc.terminate()

    win.connect("destroy", on_destroy)


# ---------------------------------------------------------------------------
# Notifications and service control
# ---------------------------------------------------------------------------

def notify_state(description):
    n = Notify.Notification.new("OpenClaw Gateway", description, "dialog-information")
    try:
        n.show()
    except Exception:
        pass


def svc(*args):
    subprocess.Popen(["systemctl", "--user"] + list(args) + [SERVICE])


# ---------------------------------------------------------------------------
# CEL helpers
# ---------------------------------------------------------------------------

def _fingerprint(line):
    m = re.search(r'\[\d+\]: (.*)', line)
    body = m.group(1) if m else line
    return _FP_STRIP.sub('N', body).strip()


def _load_always_dismiss():
    global _always_dismiss
    try:
        with open(_DISMISSED_FILE) as f:
            _always_dismiss = set(json.load(f))
    except Exception:
        _always_dismiss = set()


def _save_always_dismiss():
    try:
        os.makedirs(os.path.dirname(_DISMISSED_FILE), exist_ok=True)
        with open(_DISMISSED_FILE, "w") as f:
            json.dump(list(_always_dismiss), f)
    except Exception:
        pass


def _resolve_openrouter_key():
    global _OPENROUTER_KEY
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        _OPENROUTER_KEY = key
        return
    try:
        with open(_AUTH_FILE) as f:
            profiles = json.load(f)
        key = profiles["profiles"]["openrouter:default"]["key"]
        if key:
            _OPENROUTER_KEY = key
            return
    except Exception:
        pass
    try:
        with open(os.path.expanduser("~/.config/openrouter/key")) as f:
            key = f.read().strip()
        if key:
            _OPENROUTER_KEY = key
    except Exception:
        pass


def _call_openrouter(lines):
    if not _OPENROUTER_KEY:
        return None
    log_text = '\n'.join(lines[-20:])
    payload = json.dumps({
        "model": "moonshotai/kimi-latest",
        "max_tokens": 120,
        "messages": [
            {"role": "system", "content":
                "You are a sysadmin assistant. Explain the error in 1-2 plain sentences "
                "a non-developer can understand. Do not suggest fixes."},
            {"role": "user", "content": log_text},
        ],
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {_OPENROUTER_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip() or None
    except Exception:
        return None


def _pattern_explain(lines):
    combined = '\n'.join(lines)
    for pattern, explanation in _PATTERNS:
        if re.search(pattern, combined, re.I):
            return explanation
    return "Unrecognized recurring error — copy the fix prompt into Claude Code for a diagnosis."


def _build_fix_prompt(lines):
    log_excerpt = '\n'.join(lines[-20:])
    return (
        "My OpenClaw gateway service (openclaw-gateway.service) is showing a recurring error.\n"
        "Please diagnose the issue and provide specific commands to fix it.\n\n"
        f"Relevant log output:\n```\n{log_excerpt}\n```"
    )


# ---------------------------------------------------------------------------
# CEL dialog
# ---------------------------------------------------------------------------

def _open_cel_dialog(lines, explanation, fix_prompt):
    win = Gtk.Window(title="OpenClaw Gateway — Error Report")
    win.set_default_size(760, 500)
    win.set_border_width(12)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    win.add(vbox)

    def _make_section(label_text, text, monospace=True, height=120):
        lbl = Gtk.Label(label=label_text, xalign=0)
        vbox.pack_start(lbl, False, False, 0)

        tv = Gtk.TextView()
        tv.set_editable(False)
        tv.set_cursor_visible(False)
        tv.set_monospace(monospace)
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.get_buffer().set_text(text)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.set_min_content_height(height)
        sw.add(tv)

        copy_btn = Gtk.Button(label="Copy")
        def on_copy(_, t=tv):
            buf = t.get_buffer()
            cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            cb.set_text(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True), -1)
            cb.store()
        copy_btn.connect("clicked", on_copy)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hbox.pack_start(sw, True, True, 0)
        btn_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        btn_col.pack_start(copy_btn, False, False, 0)
        hbox.pack_start(btn_col, False, False, 0)
        vbox.pack_start(hbox, True, True, 0)

    _make_section("Offending Log", '\n'.join(lines),
                  monospace=True, height=140)
    _make_section("What's Wrong", explanation,
                  monospace=False, height=80)
    _make_section("Fix Prompt  (paste into Claude Code or Cline)", fix_prompt,
                  monospace=True, height=160)

    close_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    close_btn = Gtk.Button(label="Close")
    close_btn.connect("clicked", lambda _: win.destroy())
    close_box.pack_end(close_btn, False, False, 0)
    vbox.pack_start(close_box, False, False, 4)

    win.show_all()


# ---------------------------------------------------------------------------
# Background log monitor
# ---------------------------------------------------------------------------

def _start_log_monitor(cel_indicator):
    def _run():
        proc = subprocess.Popen(
            ["journalctl", "--user", "-u", SERVICE, "-f", "--no-pager", "-n", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not any(kw in line.lower() for kw in _ERROR_KWS):
                continue
            fp = _fingerprint(line)
            if fp in _always_dismiss or fp in _dismissed_fps:
                continue
            now = time.time()
            _recent_errors.append((now, fp, line))
            counts = _error_counts[fp]
            counts.append(now)
            _error_counts[fp] = [t for t in counts if now - t < _RECUR_WINDOW]
            if len(_error_counts[fp]) >= _RECUR_THRESH:
                error_lines = [l for t, f, l in _recent_errors if f == fp][-20:]
                GLib.idle_add(cel_indicator.activate, fp, error_lines)
    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# CEL indicator
# ---------------------------------------------------------------------------

class CelIndicator:
    def __init__(self):
        self._fp = None
        self._lines = []
        self._explanation = "Fetching explanation…"
        self._fix_prompt = ""

        self.ind = AyatanaAppIndicator3.Indicator.new(
            "openclaw-cel",
            icon_files["cel"],
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.ind.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)

        menu = Gtk.Menu()
        view_item = Gtk.MenuItem(label="View Error Report")
        view_item.connect("activate", lambda _: self._open_dialog())
        dismiss_item = Gtk.MenuItem(label="Dismiss")
        dismiss_item.connect("activate", lambda _: self._dismiss())
        always_item = Gtk.MenuItem(label="Always Dismiss This Error")
        always_item.connect("activate", lambda _: self._always_dismiss())
        for item in (view_item, dismiss_item, always_item):
            menu.append(item)
        menu.show_all()
        self.ind.set_menu(menu)

    def activate(self, fp, lines):
        if fp == self._fp:
            return False
        self._fp = fp
        self._lines = lines
        self._explanation = "Fetching explanation…"
        self._fix_prompt = _build_fix_prompt(lines)
        self.ind.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        threading.Thread(target=self._fetch_explanation, daemon=True).start()
        return False  # GLib.idle_add compat

    def _fetch_explanation(self):
        self._explanation = _call_openrouter(self._lines) or _pattern_explain(self._lines)

    def _dismiss(self):
        if self._fp:
            _dismissed_fps.add(self._fp)
        self._fp = None
        self.ind.set_status(AyatanaAppIndicator3.IndicatorStatus.PASSIVE)

    def _always_dismiss(self):
        if self._fp:
            _always_dismiss.add(self._fp)
            _save_always_dismiss()
        self._dismiss()

    def _open_dialog(self):
        _open_cel_dialog(self._lines, self._explanation, self._fix_prompt)


# ---------------------------------------------------------------------------
# Main indicator
# ---------------------------------------------------------------------------

class GatewayIndicator:
    def __init__(self):
        global _gateway_port
        Notify.init("OpenClaw Gateway")
        build_icons()
        build_cel_icon()
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

        # Start CEL subsystem
        _load_always_dismiss()
        _resolve_openrouter_key()
        self.cel = CelIndicator()
        _start_log_monitor(self.cel)

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
