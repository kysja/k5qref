"""k5qref - quick-reference snippet picker.

Runs in the background; a global hotkey (default Ctrl+F1, see config.json)
opens a searchable snippet list. Selecting an entry copies it to the
clipboard and (optionally) pastes it into the window that was active
before the picker opened.
"""

import ctypes
import ctypes.wintypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

import keyboard
import pyperclip
import pystray
from PIL import Image, ImageDraw

APP_NAME = "k5qref"
APP_TITLE = "Quick Reference"
MUTEX_NAME = "k5qref_single_instance_mutex"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.VkKeyScanW.restype = ctypes.c_short

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
HOTKEY_MODS = {
    "ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4,
    "win": 0x8, "windows": 0x8,
}
HOTKEY_VKS = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "backspace": 0x08,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "page up": 0x21, "pageup": 0x21, "page down": 0x22, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "print screen": 0x2C, "pause": 0x13,
    "num lock": 0x90, "scroll lock": 0x91, "caps lock": 0x14,
}

# Light palette
C_BORDER = "#d5d8dd"
C_BG = "#ffffff"
C_INPUT_BG = "#f1f2f5"
C_TEXT = "#202124"
C_MUTED = "#8a919e"
C_FAINT = "#b3b9c4"
C_SEL = "#e8f0fe"
C_ACCENT = "#1a73e8"

WIN_W = 680
ROW_H = 34
MAX_VISIBLE = 12

DEFAULT_CONFIG = {
    "hotkey": "ctrl+f1",
    "auto_paste": True,
    "tabs": {
        "Developer": {
            "Git": [
                {"title": "Check status", "text": "git status --short"},
                {"title": "View recent commits", "text": "git log --oneline -10"},
            ],
            "Shell": [
                {"title": "List files", "text": "Get-ChildItem -Force"},
                {"title": "Find text", "text": "rg \"search text\""},
            ],
        },
        "Writing": {
            "Markdown": [
                {"title": "Link", "text": "[label](https://example.com)"},
                {"title": "Task", "text": "- [ ] Task"},
            ],
            "Symbols": [
                {"title": "Right arrow", "text": "→"},
                {"title": "Em dash", "text": "—"},
            ],
        },
    },
}


def app_dir() -> str:
    """Directory the exe/script lives in (config.json is kept next to it)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "config.json")


def load_config() -> dict:
    """Read config.json; raises json.JSONDecodeError / OSError on a broken
    file so the caller can keep the last good config instead of silently
    reverting to defaults."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged


def ensure_single_instance() -> None:
    kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)


def force_foreground(hwnd: int) -> None:
    """SetForegroundWindow, working around the foreground-lock by attaching
    input queues to the current foreground thread."""
    if not hwnd or not user32.IsWindow(hwnd):
        return
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return
    our_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    if fg_tid and fg_tid != our_tid:
        user32.AttachThreadInput(our_tid, fg_tid, True)
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    finally:
        if fg_tid and fg_tid != our_tid:
            user32.AttachThreadInput(our_tid, fg_tid, False)


def parse_hotkey(spec: str) -> tuple[int, int]:
    """Parse a "ctrl+f1"-style string into (modifiers, vk) as expected by
    RegisterHotKey. Raises ValueError for anything it cannot map."""
    mods = MOD_NOREPEAT
    vk = None
    for part in spec.lower().split("+"):
        part = part.strip()
        if part in HOTKEY_MODS:
            mods |= HOTKEY_MODS[part]
            continue
        if vk is not None:
            raise ValueError(f"more than one non-modifier key in {spec!r}")
        if part in HOTKEY_VKS:
            vk = HOTKEY_VKS[part]
        elif part[:1] == "f" and part[1:].isdigit() \
                and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1  # VK_F1..VK_F24
        elif len(part) == 1:
            scan = user32.VkKeyScanW(ord(part))
            if scan == -1:
                raise ValueError(f"unknown key {part!r}")
            vk = scan & 0xFF
        else:
            raise ValueError(f"unknown key {part!r}")
    if vk is None:
        raise ValueError(f"no non-modifier key in {spec!r}")
    return mods, vk


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", ctypes.wintypes.RECT),
        ("rcWork", ctypes.wintypes.RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


def workarea_at_cursor() -> tuple[int, int, int, int]:
    """(left, top, width, height) of the work area of the monitor the
    mouse cursor is currently on."""
    pt = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    user32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT,
                                        ctypes.wintypes.DWORD]
    hmon = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        r = mi.rcWork
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    return 0, 0, 1920, 1080


def round_window_corners(hwnd: int) -> None:
    """Windows 11 DWM rounded corners; silently a no-op elsewhere."""
    try:
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


def ellipsize(font: tkfont.Font, text: str, max_px: int) -> str:
    if font.measure(text) <= max_px:
        return text
    text = text[:120]
    while text and font.measure(text + "…") > max_px:
        text = text[:-1]
    return text + "…"


def resource_path(*parts: str) -> str:
    """Path to a bundled resource: next to main.py when running from
    source, inside the PyInstaller unpack dir when frozen."""
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def make_tray_image() -> Image.Image:
    try:
        return Image.open(resource_path("assets", "k5qref.png"))
    except Exception:
        pass
    # fallback: drawn placeholder if the asset is missing
    img = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 61, 61], radius=12, outline=(180, 185, 195, 255), width=3)
    d.rectangle([14, 18, 50, 24], fill=(66, 133, 244, 255))
    d.rectangle([14, 30, 42, 36], fill=(180, 185, 195, 255))
    d.rectangle([14, 42, 46, 48], fill=(180, 185, 195, 255))
    return img


HINT_TEXT = "↑↓ navigate      Enter select      Esc close"
HINT_TEXT_TABS = "←→ tab      " + HINT_TEXT


class SnippetPicker:
    def __init__(self) -> None:
        self.config_error: str | None = None
        try:
            self.config = load_config()
        except (json.JSONDecodeError, OSError) as e:
            self.config = dict(DEFAULT_CONFIG)
            self.config_error = self._fmt_config_error(e)
        self.config_mtime = self._mtime()
        self.prev_hwnd = 0
        self.hotkey_error: str | None = None
        self._hotkey_thread: threading.Thread | None = None
        self.events: queue.Queue = queue.Queue()
        # ("header", str, 0) | ("snip", dict, indent_px)
        self.rows: list[tuple[str, object, int]] = []
        self.sel = -1
        self.tab_idx = 0
        self.user_pos: tuple[int, int] | None = None  # kept after dragging
        self.show_pos: tuple[int, int] | None = None  # anchor for this open

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_TITLE)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self._build_ui()
        self._update_tabbar()

        self._register_hotkey()
        self.tray = pystray.Icon(
            APP_NAME,
            make_tray_image(),
            f"{APP_TITLE}  ({self.config['hotkey']})",
            menu=pystray.Menu(
                pystray.MenuItem("Show", lambda: self.events.put("show"), default=True),
                pystray.MenuItem("Edit config", lambda: self.events.put("edit")),
                pystray.MenuItem("Reload config", lambda: self.events.put("reload")),
                pystray.MenuItem("Quit", lambda: self.events.put("quit")),
            ),
        )
        self.tray.run_detached()
        self._update_status()
        self._ticks = 0
        self.root.after(50, self._poll_events)

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        self.f_title = tkfont.Font(family="Segoe UI", size=10)
        self.f_group = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.f_tab = tkfont.Font(family="Segoe UI", size=9)
        self.f_snip = tkfont.Font(family="Consolas", size=9)
        f_small = tkfont.Font(family="Segoe UI", size=8)
        f_brand = tkfont.Font(family="Segoe UI", size=9, weight="bold")

        self.root.configure(bg=C_BORDER)
        self.outer = tk.Frame(self.root, bg=C_BG)
        self.outer.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(self.outer, bg=C_BG)
        header.pack(fill="x", padx=14, pady=(10, 4))
        brand = tk.Label(header, text=APP_TITLE, font=f_brand, bg=C_BG, fg=C_MUTED)
        brand.pack(side="left")
        self.hotkey_label = tk.Label(
            header, text=self.config["hotkey"], font=f_small, bg=C_BG, fg=C_FAINT)
        self.hotkey_label.pack(side="right")

        box = tk.Frame(self.outer, bg=C_INPUT_BG)
        box.pack(fill="x", padx=12, pady=(2, 8))
        self.entry_var = tk.StringVar()
        self.entry_var.trace_add("write", lambda *_: self._refresh_list())
        self.entry = tk.Entry(
            box, textvariable=self.entry_var, font=self.f_title,
            bg=C_INPUT_BG, fg=C_TEXT, insertbackground=C_TEXT,
            relief="flat", bd=0, highlightthickness=0,
        )
        self.entry.pack(fill="x", padx=10, ipady=7)
        self.placeholder = tk.Label(
            box, text="Search…", font=self.f_title, bg=C_INPUT_BG, fg=C_FAINT)
        self.placeholder.place(x=10, rely=0.5, anchor="w")
        self.placeholder.bind("<Button-1>", lambda e: self.entry.focus_set())

        # Tab bar; packed before the canvas by _update_tabbar when there
        # is more than one tab, hidden otherwise.
        self.tabbar = tk.Frame(self.outer, bg=C_BG)

        self.canvas_w = WIN_W - 2 - 24  # window minus border and padx
        self.canvas = tk.Canvas(
            self.outer, bg=C_BG, width=self.canvas_w, height=ROW_H,
            highlightthickness=0, bd=0, yscrollincrement=ROW_H, cursor="hand2",
        )
        self.canvas.pack(padx=12)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", lambda e: self._select())
        self.canvas.bind(
            "<MouseWheel>",
            lambda e: self.canvas.yview_scroll(-e.delta // 120, "units"))

        footer = tk.Frame(self.outer, bg=C_BG)
        footer.pack(fill="x", padx=14, pady=(6, 9))
        hint = tk.Label(
            footer, text=HINT_TEXT, font=f_small, bg=C_BG, fg=C_FAINT)
        hint.pack(side="left")
        self.hint = hint

        # Keys are bound on the root only: toplevel bindings already fire
        # for key events in child widgets, so per-widget bindings run twice.
        self.root.bind("<Escape>", lambda e: self.hide())
        self.root.bind("<Return>", lambda e: self._select())
        self.root.bind("<Down>", lambda e: self._move(+1))
        self.root.bind("<Up>", lambda e: self._move(-1))
        self.root.bind("<FocusOut>", self._on_focus_out)
        # Left/Right switch tabs. Bound on the entry (widget bindings run
        # before the Entry class binding) so returning "break" suppresses
        # caret movement; with a single tab the handler returns None and
        # the caret moves as usual. The root binding covers the rare case
        # where focus is not on the entry.
        for widget in (self.entry, self.root):
            widget.bind("<Left>", lambda e: self._switch_tab(-1))
            widget.bind("<Right>", lambda e: self._switch_tab(+1))

        for w in (header, brand, self.hotkey_label, footer, hint, self.outer):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

    # ---------------------------------------------------------- dragging

    def _drag_start(self, event) -> None:
        self._drag_off = (event.x_root - self.root.winfo_x(),
                          event.y_root - self.root.winfo_y())

    def _drag_move(self, event) -> None:
        x = event.x_root - self._drag_off[0]
        y = event.y_root - self._drag_off[1]
        self.user_pos = self.show_pos = (x, y)
        self.root.geometry(f"+{x}+{y}")

    # -------------------------------------------------------------- tabs

    def _update_tabbar(self) -> None:
        """Rebuild the tab bar labels; hide the bar entirely for a single
        (or unnamed) tab. Called on startup, config reload and tab switch."""
        tabs = self._tabs()
        self.tab_idx = max(0, min(self.tab_idx, len(tabs) - 1))
        for w in self.tabbar.winfo_children():
            w.destroy()
        if len(tabs) < 2:
            self.tabbar.pack_forget()
            return
        self.tabbar.pack(fill="x", padx=12, pady=(0, 6), before=self.canvas)
        for i, (name, _) in enumerate(tabs):
            active = i == self.tab_idx
            lbl = tk.Label(
                self.tabbar, text=name, font=self.f_tab, cursor="hand2",
                bg=C_SEL if active else C_BG,
                fg=C_ACCENT if active else C_MUTED,
                padx=10, pady=2,
            )
            lbl.pack(side="left", padx=(0, 4))
            lbl.bind("<Button-1>", lambda e, i=i: self._set_tab(i))

    def _set_tab(self, idx: int) -> None:
        if idx == self.tab_idx:
            return
        self.tab_idx = idx
        self._update_tabbar()
        self._refresh_list()

    def _switch_tab(self, delta: int) -> str | None:
        tabs = self._tabs()
        if len(tabs) < 2:
            return None  # keep normal caret movement in the entry
        self._set_tab((self.tab_idx + delta) % len(tabs))
        return "break"

    # ---------------------------------------------------------- list view

    def _refresh_list(self) -> None:
        if self.entry_var.get():
            self.placeholder.place_forget()
            self.entry.config(insertbackground=C_TEXT)
        else:
            self.placeholder.place(x=10, rely=0.5, anchor="w")
            # invisible caret while empty, so the input doesn't shout "focus"
            self.entry.config(insertbackground=C_INPUT_BG)
        terms = self.entry_var.get().lower().split()
        self.rows = []
        for group, items in self._groups():
            matched = [
                s for s in items
                if all(t in ((group or "") + " " + s.get("title", "") + " "
                             + s.get("text", "")).lower()
                       for t in terms)
            ]
            if matched:
                if group:
                    self.rows.append(("header", group, 0))
                # indent items under a group header
                indent = 16 if group else 0
                self.rows.extend(("snip", s, indent) for s in matched)
        snips = [i for i, (k, *_) in enumerate(self.rows) if k == "snip"]
        self.sel = snips[0] if snips else -1
        rows = max(len(self.rows), 1)
        visible = min(rows, MAX_VISIBLE)
        self.canvas.configure(
            height=visible * ROW_H,
            scrollregion=(0, 0, self.canvas_w, rows * ROW_H))
        self.canvas.yview_moveto(0)
        self._redraw()
        self._fit_height()

    def _tabs(self) -> list[tuple[str | None, object]]:
        """Normalize the optional "tabs" config key to [(name, snippets)].
        Accepts a dict of {tab: snippets} or a list of single-key dicts /
        {"name": ..., "snippets": ...} objects. Without "tabs", everything
        lives in a single unnamed tab (no tab bar shown)."""
        tabs = self.config.get("tabs")
        out: list[tuple[str | None, object]] = []
        if isinstance(tabs, dict):
            out = [(str(name), sn) for name, sn in tabs.items()]
        elif isinstance(tabs, list):
            for item in tabs:
                if not isinstance(item, dict):
                    continue
                if "name" in item and "snippets" in item:
                    out.append((str(item["name"]), item["snippets"]))
                elif len(item) == 1:
                    (name, sn), = item.items()
                    out.append((str(name), sn))
        return out or [(None, self.config.get("snippets", []))]

    def _groups(self) -> list[tuple[str | None, list[dict]]]:
        """Normalize the active tab's snippet shapes: a dict of
        {group: [snippets]}, a flat list of snippets, or a list mixing
        plain snippets with single-key {group: [snippets]} dicts (file
        order preserved)."""
        tabs = self._tabs()
        sn = tabs[min(self.tab_idx, len(tabs) - 1)][1]
        if isinstance(sn, dict):
            return [(g, items) for g, items in sn.items()
                    if isinstance(items, list)]
        out: list[tuple[str | None, list[dict]]] = []
        for item in sn if isinstance(sn, list) else []:
            if not isinstance(item, dict):
                continue
            if "title" in item or "text" in item:
                if out and out[-1][0] is None:
                    out[-1][1].append(item)
                else:
                    out.append((None, [item]))
            elif len(item) == 1:
                (group, items), = item.items()
                if isinstance(items, list):
                    out.append((group, items))
        return out

    def _redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        w = self.canvas_w
        if not self.rows:
            c.create_text(w / 2, ROW_H / 2, text="No matching snippets",
                          font=self.f_title, fill=C_FAINT)
            return
        for i, (kind, data, indent) in enumerate(self.rows):
            y = i * ROW_H
            if kind == "header":
                c.create_text(14, y + ROW_H / 2 + 4, text=data, anchor="w",
                              font=self.f_group, fill=C_TEXT)
                continue
            s = data
            if i == self.sel:
                self._round_rect(4, y + 2, w - 4, y + ROW_H - 2, 8, fill=C_SEL)
            title = ellipsize(self.f_title, s.get("title", "(untitled)"),
                              int(w * 0.55))
            c.create_text(14 + indent, y + ROW_H / 2, text=title, anchor="w",
                          font=self.f_title, fill=C_TEXT)
            snip = s.get("text", "").splitlines()[0] if s.get("text") else ""
            if "\n" in s.get("text", ""):
                snip += " …"
            avail = w - 28 - indent - self.f_title.measure(title) - 16
            if avail > 30:
                c.create_text(w - 14, y + ROW_H / 2,
                              text=ellipsize(self.f_snip, snip, avail),
                              anchor="e", font=self.f_snip, fill=C_MUTED)

    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r,
               x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
               x1, y1 + r, x1, y1]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def _move(self, delta: int) -> None:
        snips = [i for i, (k, *_) in enumerate(self.rows) if k == "snip"]
        if not snips:
            return
        pos = snips.index(self.sel) if self.sel in snips else 0
        self.sel = snips[(pos + delta) % len(snips)] if self.sel in snips \
            else snips[0]
        self._redraw()
        # keep the selected row in view (incl. a header right above it)
        view_row = max(self.sel - 1, 0) \
            if self.sel > 0 and self.rows[self.sel - 1][0] == "header" \
            else self.sel
        top = self.canvas.canvasy(0)
        visible_h = min(len(self.rows), MAX_VISIBLE) * ROW_H
        total_h = len(self.rows) * ROW_H
        if view_row * ROW_H < top:
            self.canvas.yview_moveto(view_row * ROW_H / total_h)
        elif (self.sel + 1) * ROW_H > top + visible_h:
            self.canvas.yview_moveto(
                ((self.sel + 1) * ROW_H - visible_h) / total_h)

    def _on_click(self, event) -> None:
        idx = int(self.canvas.canvasy(event.y) // ROW_H)
        if 0 <= idx < len(self.rows) and self.rows[idx][0] == "snip":
            self.sel = idx
            self._redraw()
        self.entry.focus_set()

    def _on_focus_out(self, _event) -> None:
        # Hide when the user clicks away; ignore focus moves within the app.
        self.root.after(80, lambda: (
            self.hide() if self.root.focus_get() is None else None))

    # --------------------------------------------------------- show / hide

    def _fit_height(self) -> None:
        self.root.update_idletasks()
        x, y = self.show_pos or self._anchor_pos()
        self.root.geometry(
            f"{WIN_W}x{self.outer.winfo_reqheight() + 2}+{x}+{y}")

    def _anchor_pos(self) -> tuple[int, int]:
        """Position on the monitor the cursor is on; reuse a dragged
        position only if it is on that same monitor."""
        left, top, w, h = workarea_at_cursor()
        if self.user_pos:
            ux, uy = self.user_pos
            if left <= ux < left + w and top <= uy < top + h:
                return ux, uy
        return left + (w - WIN_W) // 2, top + h // 5

    def show(self) -> None:
        self._maybe_reload()
        self.show_pos = self._anchor_pos()
        self.entry_var.set("")   # triggers _refresh_list + _fit_height
        self.root.deiconify()
        self.root.lift()
        hwnd = user32.GetParent(self.entry.winfo_id())
        while user32.GetParent(hwnd):
            hwnd = user32.GetParent(hwnd)
        round_window_corners(hwnd)
        force_foreground(hwnd)
        self.entry.focus_force()

    def hide(self) -> None:
        self.root.withdraw()

    def _select(self) -> None:
        if not self.root.winfo_viewable():
            return
        if not (0 <= self.sel < len(self.rows)) \
                or self.rows[self.sel][0] != "snip":
            return
        text = self.rows[self.sel][1].get("text", "")
        pyperclip.copy(text)
        self.hide()
        if self.config.get("auto_paste", True) and self.prev_hwnd:
            hwnd = self.prev_hwnd
            self.root.after(120, lambda: self._paste_into(hwnd))

    def _paste_into(self, hwnd: int) -> None:
        force_foreground(hwnd)
        time.sleep(0.10)
        if user32.GetForegroundWindow() == hwnd:
            keyboard.send("ctrl+v")

    # ------------------------------------------------------ config / hotkey

    def _edit_config(self) -> None:
        """Open config.json in a text editor. The "edit" verb picks the
        registered editor; .json often has none, so fall back to Notepad."""
        try:
            os.startfile(CONFIG_PATH, "edit")
        except OSError:
            subprocess.Popen(["notepad.exe", CONFIG_PATH])

    def _mtime(self) -> float:
        try:
            return os.path.getmtime(CONFIG_PATH)
        except OSError:
            return 0.0

    def _maybe_reload(self) -> None:
        mtime = self._mtime()
        if mtime != self.config_mtime:
            self._reload()

    def _reload(self) -> None:
        old_hotkey = self.config.get("hotkey")
        # mtime is recorded even on failure so a broken file isn't re-parsed
        # every poll tick; keep the last good config until it's fixed.
        self.config_mtime = self._mtime()
        try:
            self.config = load_config()
            self.config_error = None
        except (json.JSONDecodeError, OSError) as e:
            self.config_error = self._fmt_config_error(e)
        if self.config.get("hotkey") != old_hotkey:
            self._register_hotkey()
        self.hotkey_label.config(text=self.config["hotkey"])
        self._update_status()
        self._update_tabbar()
        self._refresh_list()

    @staticmethod
    def _fmt_config_error(e: Exception) -> str:
        if isinstance(e, json.JSONDecodeError):
            return f"config.json is invalid: line {e.lineno} — {e.msg}"
        return f"config.json could not be read: {e}"

    def _update_status(self) -> None:
        error = self.config_error or self.hotkey_error
        if error:
            self.hint.config(text="⚠ " + error, fg="#d93025")
        else:
            hint = HINT_TEXT_TABS if len(self._tabs()) > 1 else HINT_TEXT
            self.hint.config(text=hint, fg=C_FAINT)
        if hasattr(self, "tray"):
            self.tray.title = f"{APP_TITLE}  ({self.config['hotkey']})" + \
                ("  — error!" if error else "")

    def _register_hotkey(self) -> None:
        """(Re)register the configured hotkey via Win32 RegisterHotKey on a
        dedicated message-loop thread. Unlike a low-level keyboard hook,
        the OS owns the registration, so it cannot be silently dropped
        when the process stalls (the cause of hotkeys "dying" over time)."""
        self._stop_hotkey_thread()
        spec = self.config.get("hotkey", "ctrl+f1")
        try:
            mods, vk = parse_hotkey(spec)
        except ValueError as e:
            self.hotkey_error = f"hotkey: {e}"
            return
        result: dict = {}
        ready = threading.Event()
        self._hotkey_thread = threading.Thread(
            target=self._hotkey_loop, args=(mods, vk, result, ready),
            daemon=True, name="hotkey")
        self._hotkey_thread.start()
        ready.wait(2.0)
        self.hotkey_error = None if result.get("ok") else \
            f'hotkey "{spec}" could not be registered (in use by another app?)'

    def _hotkey_loop(self, mods: int, vk: int, result: dict,
                     ready: threading.Event) -> None:
        msg = ctypes.wintypes.MSG()
        # PeekMessage creates this thread's message queue, so a WM_QUIT
        # posted by _stop_hotkey_thread is never lost
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        result["ok"] = bool(user32.RegisterHotKey(None, 1, mods, vk))
        ready.set()
        if not result["ok"]:
            return
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    # Capture the focused window here, before our own
                    # window takes it; the rest runs on the tk main thread.
                    self.prev_hwnd = user32.GetForegroundWindow()
                    self.events.put("show")
        finally:
            user32.UnregisterHotKey(None, 1)

    def _stop_hotkey_thread(self) -> None:
        t = self._hotkey_thread
        self._hotkey_thread = None
        if t and t.is_alive():
            user32.PostThreadMessageW(t.native_id, WM_QUIT, 0, 0)
            t.join(1.0)

    # --------------------------------------------------------------- loop

    def _poll_events(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                if ev == "show":
                    self.show()
                elif ev == "edit":
                    self._edit_config()
                elif ev == "reload":
                    self._reload()
                elif ev == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        # pick up config.json edits (incl. hotkey changes) within ~2 s,
        # without needing the picker to be opened first
        self._ticks += 1
        if self._ticks % 40 == 0:
            self._maybe_reload()
        self.root.after(50, self._poll_events)

    def quit(self) -> None:
        self._stop_hotkey_thread()
        self.tray.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    ensure_single_instance()
    SnippetPicker().run()


if __name__ == "__main__":
    main()
