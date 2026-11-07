# -*- coding: utf-8 -*-
"""
Portfolio Manager UI parts: themes, selectors, scrolling area, allocation treemap,
value chart (daily history and 1D/1W intraday) and a split bar.
"""

import datetime as dt
import math
import os
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple

from accounting import date_ticks, fmt_eur, fmt_num, fmt_pct, nice_ticks, time_ticks, MONTHS

try:  # anti-aliased graphics
    from PIL import Image, ImageDraw, ImageTk
    _BOX = getattr(Image, "Resampling", Image).BOX
    HAVE_PIL = hasattr(ImageDraw.ImageDraw, "rounded_rectangle")
except Exception:  # pragma: no cover
    HAVE_PIL = False

SS = 4  # supersampling: draw at 4x and scale down

MASK = "●●●●"                 # privacy mode (eye button): amounts are replaced by four dots
MASK_EUR = MASK + " €"
MASK_USD = "$" + MASK

# Mouse wheel: Windows sends <MouseWheel>, Tk 8.6 on Linux buttons 4 (up) and 5 (down).
WHEEL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")


def wheel_step(e) -> int:
    """Scroll units: negative = up, positive = down."""
    if e.num == 4:
        return -1
    if e.num == 5:
        return 1
    return int(-e.delta / 120) or (-1 if e.delta > 0 else 1)


# Neutral grey surfaces with a green accent. Money source: own money = green,
# student loan = blue, gifted shares = pink. Price change: up = green, down = red.
THEMES = {
    "dark": {
        "bg": "#0F1011", "bar": "#141517", "card": "#17181A", "card2": "#1E1F22", "input": "#121315",
        "border": "#2A2C30", "grid": "#222326", "hover": "#27292D",
        "text": "#EDEDEF", "muted": "#A0A3A8", "faint": "#686B71",
        "accent": "#3ECF8E", "accent_hi": "#62DCA6", "on_accent": "#07140E", "sel": "#1E3329",
        "own": "#3ECF8E", "loan": "#2563EB", "gift": "#F472B6", "ext": "#A78BFA", "invest": "#A78BFA", "value": "#EDEDEF",
        "debt": "#F87171", "cash": "#7D8189", "red": "#F87171", "bad": "#EF4444", "bad_bg": "#2A1517",
        "bad_fg": "#FCA5A5", "good": "#3ECF8E", "up": "#2FA36F", "down": "#D0453F", "up_text": "#4ADE9C",
        "down_text": "#F87171", "info": "#2563EB",
    },
    "light": {
        "bg": "#F2F2EF", "bar": "#FFFFFF", "card": "#FFFFFF", "card2": "#F5F5F2", "input": "#FFFFFF",
        "border": "#DCDCD6", "grid": "#ECECE8", "hover": "#ECECE7",
        "text": "#1B1C1F", "muted": "#5D6066", "faint": "#9A9CA1",
        "accent": "#0E9F6E", "accent_hi": "#0B8A5F", "on_accent": "#FFFFFF", "sel": "#DDF1E7",
        "own": "#0E9F6E", "loan": "#2563EB", "gift": "#DB2777", "ext": "#7C5CE0", "invest": "#7C5CE0", "value": "#1B1C1F",
        "debt": "#DC2626", "cash": "#9A9CA1", "red": "#DC2626", "bad": "#DC2626", "bad_bg": "#FDEDED",
        "bad_fg": "#B42318", "good": "#0E9F6E", "up": "#16A06A", "down": "#D93B3B", "up_text": "#0B8A5F",
        "down_text": "#C42B2B", "info": "#2563EB",
    },
}
C: Dict[str, str] = dict(THEMES["dark"])


def blend(a: str, b: str, t: float) -> str:
    """Mix two colours: t = share of a."""
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(round(x * t + y * (1 - t)) for x, y in zip(pa, pb))


def luminance(c: str) -> float:
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def change_color(pct: Optional[float]) -> str:
    """Text colour for a price change."""
    if pct is None or abs(pct) < 5e-5:
        return C["muted"]
    return C["up_text"] if pct > 0 else C["down_text"]


def system_theme() -> str:
    """Windows app theme (light / dark). Dark elsewhere."""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
        except OSError:
            pass
    return "dark"


def set_titlebar_dark(win: tk.Tk, dark: bool) -> None:
    """Windows 10/11: dark or light window title bar to match the theme."""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = int(win.wm_frame(), 16)
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old id)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val),
                                                          ctypes.sizeof(val)) == 0:
                break
        # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER: repaint the title bar
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004)
    except Exception:
        pass


def set_window_app_id(hwnd: int, app_id: str) -> None:
    """Per-window app id (Windows) so the taskbar shows the app icon instead of Python's,
    also with the Microsoft Store Python."""
    import ctypes
    from ctypes import wintypes as wt

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wt.DWORD), ("d2", wt.WORD), ("d3", wt.WORD), ("d4", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wt.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                    ("r3", ctypes.c_ushort), ("val", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]

    def guid(s: str) -> GUID:
        g = GUID()
        ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
        return g

    iid_store = guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")                  # IPropertyStore
    key = PROPERTYKEY(guid("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"), 5)       # PKEY_AppUserModel_ID
    store = ctypes.c_void_p()
    if ctypes.windll.shell32.SHGetPropertyStoreForWindow(wt.HWND(hwnd), ctypes.byref(iid_store),
                                                         ctypes.byref(store)) != 0:
        return
    vt = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    value = PROPVARIANT(31, 0, 0, 0, app_id, None)                             # VT_LPWSTR
    ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                       ctypes.POINTER(PROPVARIANT))(vt[6])(store, ctypes.byref(key), ctypes.byref(value))
    ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vt[7])(store)           # Commit
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(store)          # Release


# ---------------------------------------------------------------------------
#  Selectors and layout
# ---------------------------------------------------------------------------

class EyeButton(tk.Canvas):
    """Eye icon like in password fields: open eye = amounts visible, crossed-out eye = hidden."""

    def __init__(self, master, sc: float, hidden: bool, command, bg: str = None, size: int = 30):
        self.sc, self.size = sc, int(size * sc)
        super().__init__(master, width=self.size, height=self.size, bg=bg or C["card"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.hidden, self.command, self.hover = hidden, command, False
        self._img = None
        self.bind("<Button-1>", lambda e: self.toggle())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.draw()

    def _set_hover(self, on: bool):
        self.hover = on
        self.draw()

    def toggle(self):
        self.hidden = not self.hidden
        self.draw()
        self.command(self.hidden)

    def draw(self):
        """Rounded square in the green of the allocation tiles, with an anti-aliased white eye."""
        self.delete("all")
        n, sc = self.size, self.sc
        fill = blend(C["up"], C["card2"], 0.85 if self.hover else 0.7)
        if not HAVE_PIL:
            self.create_rectangle(0, 0, n, n, fill=fill, outline="")
            self.create_text(n / 2, n / 2, text="◉" if not self.hidden else "⊘", fill="#F4F4F5")
            return
        k = 8                       # extra supersampling for a crisp small icon
        N = n * k
        img = Image.new("RGB", (N, N), self["bg"])
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, N - 1, N - 1], radius=7 * sc * k, fill=fill)
        white, lw = "#F4F4F5", max(1, round(1.9 * sc * k))
        cx, cy, a, b = N / 2, N / 2, N * 0.34, N * 0.2
        upper = [(cx - a + 2 * a * t / 40, cy - b * math.sin(math.pi * t / 40)) for t in range(41)]
        lower = [(cx - a + 2 * a * t / 40, cy + b * math.sin(math.pi * t / 40)) for t in range(41)]
        for pts in (upper, lower):
            d.line(pts, fill=white, width=lw, joint="curve")
        r = N * 0.1
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=white)
        if self.hidden:
            p0, p1 = (N * 0.24, N * 0.76), (N * 0.76, N * 0.24)
            d.line([p0, p1], fill=fill, width=lw * 3)
            d.line([p0, p1], fill=white, width=lw)
            for x, y in (p0, p1):
                d.ellipse([x - lw / 2, y - lw / 2, x + lw / 2, y + lw / 2], fill=white)
        self._img = ImageTk.PhotoImage(img.resize((n, n), Image.LANCZOS if hasattr(Image, "LANCZOS")
                                                  else _BOX))
        self.create_image(0, 0, image=self._img, anchor="nw")


class Segmented(tk.Frame):
    """Selector with one option selected. options: [str] or [(key, text)]."""

    def __init__(self, master, options, variable: tk.StringVar, sc: float, font, command=None):
        super().__init__(master, bg=C["card2"], highlightthickness=1, highlightbackground=C["border"])
        self.var, self.command, self.labels = variable, command, {}
        for opt in options:
            key, text = opt if isinstance(opt, tuple) else (opt, opt)
            lbl = tk.Label(self, text=text, font=font, padx=int(11 * sc), pady=int(3 * sc),
                           cursor="hand2", bd=0)
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, k=key: self._pick(k))
            lbl.bind("<Enter>", lambda e, k=key: self._hover(k, True))
            lbl.bind("<Leave>", lambda e, k=key: self._hover(k, False))
            self.labels[key] = lbl
        variable.trace_add("write", lambda *a: self.refresh())
        self.refresh()

    def _pick(self, key: str):
        if self.var.get() != key:
            self.var.set(key)
            if self.command:
                self.command()

    def _hover(self, key: str, on: bool):
        if key != self.var.get():
            self.labels[key].configure(bg=C["hover"] if on else C["card2"])

    def refresh(self):
        if not self.winfo_exists():
            return
        for k, lbl in self.labels.items():
            on = k == self.var.get()
            lbl.configure(bg=C["accent"] if on else C["card2"], fg=C["on_accent"] if on else C["muted"])


class NavBar(tk.Frame):
    """Top menu: the selected page is underlined."""

    def __init__(self, master, items, variable: tk.StringVar, sc: float, font, command=None):
        super().__init__(master, bg=C["bar"])
        self.var, self.command, self.items = variable, command, {}
        for key, text in items:
            f = tk.Frame(self, bg=C["bar"], cursor="hand2")
            f.pack(side="left", padx=(0, int(4 * sc)))
            lbl = tk.Label(f, text=text, font=font, bg=C["bar"], padx=int(12 * sc), pady=int(14 * sc),
                           cursor="hand2")
            lbl.pack()
            bar = tk.Frame(f, height=max(2, int(3 * sc)), bg=C["bar"])
            bar.pack(fill="x", padx=int(8 * sc))
            for w in (f, lbl, bar):
                w.bind("<Button-1>", lambda e, k=key: self._pick(k))
            lbl.bind("<Enter>", lambda e, k=key: self._hover(k, True))
            lbl.bind("<Leave>", lambda e, k=key: self._hover(k, False))
            self.items[key] = (lbl, bar)
        variable.trace_add("write", lambda *a: self.refresh())
        self.refresh()

    def _pick(self, key: str):
        self.var.set(key)
        if self.command:
            self.command()

    def _hover(self, key: str, on: bool):
        if key != self.var.get():
            self.items[key][0].configure(fg=C["text"] if on else C["muted"])

    def refresh(self):
        if not self.winfo_exists():
            return
        for k, (lbl, bar) in self.items.items():
            on = k == self.var.get()
            lbl.configure(fg=C["text"] if on else C["muted"])
            bar.configure(bg=C["accent"] if on else C["bar"])


class ChipGrid(tk.Frame):
    """Entry kind picker: buttons in a grid."""

    def __init__(self, master, options, variable: tk.StringVar, sc: float, font, cols: int = 4):
        super().__init__(master, bg=C["card"])
        self.var, self.labels = variable, {}
        for i, (key, text) in enumerate(options):
            r, c = divmod(i, cols)
            lbl = tk.Label(self, text=text, font=font, pady=int(5 * sc), cursor="hand2", bd=0,
                           highlightthickness=1)
            lbl.grid(row=r, column=c, sticky="we", padx=(0 if c == 0 else int(6 * sc), 0),
                     pady=(0 if r == 0 else int(6 * sc), 0))
            lbl.bind("<Button-1>", lambda e, k=key: self.var.set(k))
            lbl.bind("<Enter>", lambda e, k=key: self._hover(k, True))
            lbl.bind("<Leave>", lambda e, k=key: self._hover(k, False))
            self.labels[key] = lbl
        for c in range(cols):
            self.columnconfigure(c, weight=1, uniform="chip")
        variable.trace_add("write", lambda *a: self.refresh())
        self.refresh()

    def _hover(self, key: str, on: bool):
        if key != self.var.get():
            self.labels[key].configure(bg=C["hover"] if on else C["card2"])

    def refresh(self):
        if not self.winfo_exists():
            return
        for k, lbl in self.labels.items():
            on = k == self.var.get()
            lbl.configure(bg=C["accent"] if on else C["card2"], fg=C["on_accent"] if on else C["text"],
                          highlightbackground=C["accent"] if on else C["border"])


class ScrollFrame(tk.Frame):
    """Vertically scrolling area.

    grow: a widget (e.g. a chart) that stretches to fill the free height but never shrinks
    below grow_min – then the area starts to scroll.
    autohide: the scrollbar is shown only when the content does not fit."""

    def __init__(self, master, bg: str, width: int = 0, autohide: bool = False):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, width=width)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                style="App.Vertical.TScrollbar")
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.autohide = autohide
        self.grow: Optional[tk.Widget] = None
        self.grow_min = 0
        self._job = None
        self.inner.bind("<Configure>", self._schedule_fit)
        self.canvas.bind("<Configure>", self._canvas_resized)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        if not autohide:
            self.sb.pack(side="right", fill="y")
        self.bind("<Enter>", lambda e: self._bind_wheel(True))
        self.bind("<Leave>", lambda e: self._bind_wheel(False))

    def destroy(self):
        self._bind_wheel(False)
        super().destroy()

    def _bind_wheel(self, on: bool):
        for ev in WHEEL_EVENTS:
            try:
                if on:
                    self.bind_all(ev, self.wheel)
                else:
                    self.unbind_all(ev)
            except tk.TclError:
                pass

    def _canvas_resized(self, e):
        self.canvas.itemconfigure(self.win, width=e.width)
        self._schedule_fit()

    def _schedule_fit(self, _e=None):
        if self._job is None:
            self._job = self.after(15, self._fit)

    def _fit(self):
        self._job = None
        if not self.winfo_exists():
            return
        self.update_idletasks()
        ch = self.canvas.winfo_height()
        if ch < 2:
            return
        if self.grow is not None and self.grow.winfo_ismapped():
            cur = self.grow.winfo_reqheight()
            want = max(self.grow_min, ch - (self.inner.winfo_reqheight() - cur))
            if want != cur:
                self.grow.configure(height=want)
                self.update_idletasks()
        if self.autohide:
            need = self.inner.winfo_reqheight() > ch + 1
            if need and not self.sb.winfo_ismapped():
                self.sb.pack(side="right", fill="y", before=self.canvas)
            elif not need and self.sb.winfo_ismapped():
                self.sb.pack_forget()
                self.canvas.yview_moveto(0)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def wheel(self, e):
        try:
            if self.inner.winfo_height() > self.canvas.winfo_height():
                self.canvas.yview_scroll(wheel_step(e), "units")
        except tk.TclError:
            pass


# ---------------------------------------------------------------------------
#  Graphics
# ---------------------------------------------------------------------------

def dash_polyline(P, on: float, off: float):
    """Split a polyline into dashes."""
    segs, cur = [], [P[0]]
    drawing, remaining = True, on
    for (x0, y0), (x1, y1) in zip(P, P[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        pos = 0.0
        while L - pos > remaining:
            pos += remaining
            t = pos / L
            pt = (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
            if drawing:
                cur.append(pt)
                segs.append(cur)
            else:
                cur = [pt]
            drawing = not drawing
            remaining = on if drawing else off
        remaining -= L - pos
        if drawing:
            cur.append((x1, y1))
    if drawing and len(cur) >= 2:
        segs.append(cur)
    return segs


def paint_items(canvas: tk.Canvas, W: int, H: int, items, sc: float, bg: str = None):
    """Draw chart items: anti-aliased with Pillow, plain Tk lines otherwise.
    Returns the image, which must be kept (Tk frees it otherwise)."""
    bg = bg or C["card"]
    if not HAVE_PIL:
        for it in items:
            kind = it[0]
            if kind == "hline":
                _, x0, x1, y, col = it
                canvas.create_line(x0, y, x1, y, fill=col)
            elif kind == "hdash":
                _, x0, x1, y, col = it
                canvas.create_line(x0, y, x1, y, fill=col, dash=(4, 4))
            elif kind == "vline":
                _, x, y0, y1, col, dashed = it
                canvas.create_line(x, y0, x, y1, fill=col, **({"dash": (3, 4)} if dashed else {}))
            elif kind == "poly":
                _, pts, col = it
                canvas.create_polygon([c for p in pts for c in p], fill=col, outline="")
            elif kind == "line":
                _, pts, col, width, dashed = it
                canvas.create_line([c for p in pts for c in p], fill=col, width=max(2, width),
                                   capstyle="round", joinstyle="round", **({"dash": (6, 4)} if dashed else {}))
            elif kind == "dot":
                _, x, y, r, col, outline, ow = it
                canvas.create_oval(x - r, y - r, x + r, y + r, fill=col, outline=outline, width=ow)
            elif kind == "rrect":
                _, x0, y0, x1, y1, _r, col = it
                canvas.create_rectangle(x0, y0, x1, y1, fill=col, outline="")
        return None
    img = Image.new("RGB", (W * SS, H * SS), bg)
    d = ImageDraw.Draw(img)
    for it in items:
        kind = it[0]
        if kind == "hline":     # exactly one pixel high = sharp
            _, x0, x1, y, col = it
            yy = int(y)
            d.rectangle([int(x0) * SS, yy * SS, int(x1) * SS, yy * SS + SS - 1], fill=col)
        elif kind == "hdash":
            _, x0, x1, y, col = it
            yy, xx = int(y), x0
            while xx < x1:
                d.rectangle([xx * SS, yy * SS, min(xx + 4 * sc, x1) * SS, yy * SS + SS - 1], fill=col)
                xx += 8 * sc
        elif kind == "vline":
            _, x, y0, y1, col, dashed = it
            xx = int(x)
            if dashed:
                yy = y0
                while yy < y1:
                    d.rectangle([xx * SS, yy * SS, xx * SS + SS - 1, min(yy + 3 * sc, y1) * SS], fill=col)
                    yy += 7 * sc
            else:
                d.rectangle([xx * SS, y0 * SS, xx * SS + SS - 1, y1 * SS], fill=col)
        elif kind == "poly":
            _, pts, col = it
            d.polygon([(x * SS, y * SS) for x, y in pts], fill=col)
        elif kind == "line":
            _, pts, col, width, dashed = it
            P = [(x * SS, y * SS) for x, y in pts]
            w = max(1, int(round(width * SS)))
            segs = dash_polyline(P, 7 * sc * SS, 5 * sc * SS) if dashed else [P]
            rr = w / 2
            for seg in segs:
                if len(seg) >= 2:
                    d.line(seg, fill=col, width=w, joint="curve")
                    for (x, y) in (seg[0], seg[-1]):
                        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col)
        elif kind == "dot":
            _, x, y, r, col, outline, ow = it
            d.ellipse([(x - r - ow) * SS, (y - r - ow) * SS, (x + r + ow) * SS, (y + r + ow) * SS], fill=outline)
            d.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=col)
        elif kind == "rrect":
            _, x0, y0, x1, y1, r, col = it
            if x1 - x0 >= 1 and y1 - y0 >= 1:
                rr = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
                d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], radius=rr * SS, fill=col)
    photo = ImageTk.PhotoImage(img.resize((W, H), _BOX))
    canvas.create_image(0, 0, image=photo, anchor="nw")
    return photo


class StackBar(tk.Canvas):
    """Horizontal bar split into parts (e.g. student loan / own money)."""

    def __init__(self, master, sc: float, height: int = 12):
        self.h = int(height * sc)
        super().__init__(master, height=self.h, bg=C["card"], highlightthickness=0, bd=0)
        self.sc, self.parts, self._img = sc, [], None
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, parts: List[Tuple[float, str]]):
        self.parts = [(max(0.0, v), col) for v, col in parts]
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.h
        if w < 10:
            return
        total = sum(v for v, _ in self.parts)
        segs, x = [], 0.0
        if total > 0:
            for v, col in self.parts:
                if v > 0:
                    segs.append((x, x + w * v / total, col))
                    x += w * v / total
        if HAVE_PIL:
            W, Hh, gap = w * SS, h * SS, 2 * self.sc * SS
            bar = Image.new("RGB", (W, Hh), C["grid"])
            d = ImageDraw.Draw(bar)
            for k, (a, b, col) in enumerate(segs):
                d.rectangle([a * SS, 0, b * SS, Hh], fill=col)
                if k:
                    d.rectangle([a * SS - gap / 2, 0, a * SS + gap / 2, Hh], fill=C["card"])
            mask = Image.new("L", (W, Hh), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, Hh - 1], radius=Hh / 2, fill=255)
            img = Image.new("RGB", (W, Hh), C["card"])
            img.paste(bar, (0, 0), mask)
            self._img = ImageTk.PhotoImage(img.resize((w, h), _BOX))
            self.create_image(0, 0, image=self._img, anchor="nw")
            return
        self.create_rectangle(0, 0, w, h, fill=C["grid"], outline="")
        for a, b, col in segs:
            self.create_rectangle(a, 0, b, h, fill=col, outline="")


def _tooltip(canvas: tk.Canvas, x: float, y: float, title: str, rows, sc: float, fonts: dict,
             bounds: Tuple[float, float, float, float]):
    """Floating tooltip: a title and rows (name, value, swatch colour or None, value colour or None)."""
    L, T, R, B = bounds
    bw, lh = 238 * sc, 18 * sc
    bh = lh * (len(rows) + 1) + 10 * sc
    bx = x + 14 * sc
    if bx + bw > R:
        bx = x - 14 * sc - bw
    bx = max(L, bx)
    by = max(T, min(y - bh / 2, B - bh))
    canvas.create_rectangle(bx, by, bx + bw, by + bh, fill=C["card2"], outline=C["border"], tags="hover")
    canvas.create_text(bx + 10 * sc, by + 6 * sc + lh / 2, text=title, anchor="w", fill=C["text"],
                       font=fonts["bodyb"], tags="hover")
    for k, (name, val, col, vcol) in enumerate(rows, start=1):
        yy = by + 6 * sc + lh * k + lh / 2
        tx = bx + 10 * sc
        if col:
            canvas.create_rectangle(tx, yy - 4 * sc, tx + 8 * sc, yy + 4 * sc, fill=col, outline="", tags="hover")
            tx += 14 * sc
        canvas.create_text(tx, yy, text=name, anchor="w", fill=C["muted"], font=fonts["small"], tags="hover")
        canvas.create_text(bx + bw - 10 * sc, yy, text=val, anchor="e", fill=vcol or C["text"],
                           font=fonts["small"], tags="hover")


class ValueChart(tk.Canvas):
    """Portfolio value over time.

    Daily mode: value line over stacked contributions (own money, gifts, student loan).
    Intraday mode (1D / 1W): value line coloured by the change from the base (dashed)."""

    def __init__(self, master, sc: float, fonts: dict):
        super().__init__(master, bg=C["card"], highlightthickness=0, bd=0)
        self.sc, self.f = sc, fonts
        self.mode = "daily"
        self.days, self.own, self.gift, self.loan, self.value = [], [], [], [], []
        self.times, self.ivals, self.base, self.multi_day = [], [], 0.0, False
        self.empty_text = "No entries yet."
        self.hide = False          # privacy mode: amounts shown as ●●●● €
        self.geom = None
        self._photo = None
        self._job = None
        self.bind("<Configure>", lambda e: self._later())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))

    def _e(self, x, dec=2, sign=False) -> str:
        return MASK_EUR if self.hide else fmt_eur(x, dec, sign)

    def _later(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(30, self.redraw)

    def set_data(self, days, own, gift, loan, value):
        self.mode = "daily"
        self.days, self.own, self.gift, self.loan, self.value = days, own, gift, loan, value
        self.redraw()

    def set_intraday(self, times, values, base, multi_day: bool, empty_text: str = "", contrib=None):
        """contrib: net deposits per point (1W). Deposits are then drawn as a dashed step line and
        left out of the change, so a deposit does not look like a return."""
        self.mode = "intraday"
        self.times, self.ivals, self.base, self.multi_day = times, values, base, multi_day
        self.contrib = contrib if contrib and len(contrib) == len(values) else None
        self.empty_text = empty_text or "No intraday prices yet."
        self.redraw()

    def change(self, i: int = -1) -> Tuple[float, float]:
        """Change up to point i without deposits: (EUR, share)."""
        if not self.ivals:
            return 0.0, 0.0
        if self.contrib:
            flows = self.contrib[i] - self.contrib[0]
            chg = self.ivals[i] - self.ivals[0] - flows
            base = self.ivals[0] + max(0.0, flows)
        else:
            chg, base = self.ivals[i] - self.base, self.base
        return chg, (chg / base if base else 0.0)

    # ---- drawing ----
    def redraw(self):
        self._job = None
        self.delete("all")
        W, H = self.winfo_width(), self.winfo_height()
        if W < 50 or H < 50:
            return
        if self.mode == "intraday":
            self._draw_intraday(W, H)
        else:
            self._draw_daily(W, H)

    def _frame(self, W, H, vals, lo_zero: bool):
        sc = self.sc
        lo, hi = (min(0.0, min(vals)) if lo_zero else min(vals)), max(vals)
        if not lo_zero:  # zoom in on intraday moves, with a little headroom
            pad = max((hi - lo) * 0.12, abs(hi) * 0.002, 1.0)
            lo, hi = lo - pad, hi + pad
        tlo, thi, ticks = nice_ticks(lo, hi, 5)
        L, R, T, B = 66 * sc, 116 * sc, 14 * sc, 28 * sc
        return tlo, thi, ticks, L, R, T, B, W - L - R, H - T - B

    def _draw_daily(self, W, H):
        sc = self.sc
        n = len(self.days)
        if n < 2:
            self.create_text(W / 2, H / 2, text="No entries yet.", fill=C["muted"], font=self.f["body"])
            return
        low = [a + b for a, b in zip(self.own, self.gift)]
        top = [a + b for a, b in zip(low, self.loan)]
        tlo, thi, ticks, L, R, T, B, pw, ph = self._frame(W, H, top + self.own + self.value, True)

        def X(i):
            return L + pw * i / (n - 1)

        def Y(v):
            return T + ph * (1 - (v - tlo) / (thi - tlo))

        def steps(data):
            pts = [(X(0), Y(data[0]))]
            for i in range(1, n):
                pts += [(X(i), Y(data[i - 1])), (X(i), Y(data[i]))]
            return pts

        self.geom = (L, R, T, B, n, X, Y)
        items, texts = [], []
        for v in ticks:
            items.append(("hline", L, W - R, Y(v), C["grid"] if abs(v) > 1e-9 else C["faint"]))
            texts.append((L - 10 * sc, Y(v), "" if self.hide else fmt_num(v), "e", C["muted"], "small"))
        for i, lab in date_ticks(self.days, max(2, int(pw / (70 * sc)))):
            texts.append((X(i), T + ph + 14 * sc, lab, "center", C["muted"], "small"))
        own_pts, low_pts, top_pts, y0 = steps(self.own), steps(low), steps(top), Y(0)
        items.append(("poly", top_pts + low_pts[::-1], blend(C["loan"], C["card"], 0.26)))
        if any(g > 0.005 for g in self.gift):
            items.append(("poly", low_pts + own_pts[::-1], blend(C["gift"], C["card"], 0.30)))
        items.append(("poly", [(X(0), y0)] + own_pts + [(X(n - 1), y0)], blend(C["own"], C["card"], 0.26)))
        items.append(("line", top_pts, C["loan"], 1.6 * sc, False))
        items.append(("line", own_pts, C["own"], 1.6 * sc, False))
        items.append(("line", [(X(i), Y(v)) for i, v in enumerate(self.value)], C["value"], 2.2 * sc, False))
        items.append(("dot", X(n - 1), Y(self.value[-1]), 4 * sc, C["value"], C["card"], 2 * sc))
        gain = self.value[-1] - top[-1]
        texts.append((X(n - 1) + 12 * sc, Y(self.value[-1]) - 8 * sc, self._e(self.value[-1], 0), "w",
                      C["text"], "bodyb"))
        texts.append((X(n - 1) + 12 * sc, Y(self.value[-1]) + 9 * sc, self._e(gain, 0, sign=True), "w",
                      change_color(gain), "small"))
        self._photo = paint_items(self, W, H, items, sc)
        for x, y, text, anchor, col, font in texts:
            self.create_text(x, y, text=text, anchor=anchor, fill=col, font=self.f[font])

    def _draw_intraday(self, W, H):
        sc = self.sc
        n = len(self.times)
        if n < 2:
            self.geom = None
            self.create_text(W / 2, H / 2, text=self.empty_text, fill=C["muted"], font=self.f["body"])
            return
        ref = self.contrib if self.contrib else [self.base]
        tlo, thi, ticks, L, R, T, B, pw, ph = self._frame(W, H, self.ivals + ref, False)

        def X(i):
            return L + pw * i / (n - 1)

        def Y(v):
            return T + ph * (1 - (v - tlo) / (thi - tlo))

        self.geom = (L, R, T, B, n, X, Y)
        chg, pct = self.change()
        col = C["up"] if chg >= 0 else C["down"]
        items, texts = [], []
        for v in ticks:
            items.append(("hline", L, W - R, Y(v), C["grid"]))
            texts.append((L - 10 * sc, Y(v), "" if self.hide else fmt_num(v), "e", C["muted"], "small"))
        for i, lab in time_ticks(self.times, max(2, int(pw / (60 * sc))), self.multi_day):
            if self.multi_day:
                items.append(("vline", X(i), T, T + ph, C["grid"], False))
            texts.append((X(i), T + ph + 14 * sc, lab, "center", C["muted"], "small"))
        pts = [(X(i), Y(v)) for i, v in enumerate(self.ivals)]
        items.append(("poly", [(X(0), T + ph)] + pts + [(X(n - 1), T + ph)], blend(col, C["card"], 0.14)))
        if self.contrib:
            cp = [(X(0), Y(self.contrib[0]))]
            for i in range(1, n):
                cp += [(X(i), Y(self.contrib[i - 1])), (X(i), Y(self.contrib[i]))]
            items.append(("line", cp, C["muted"], 1.4 * sc, True))
        else:
            items.append(("hdash", L, W - R, Y(self.base), C["faint"]))
        items.append(("line", pts, col, 2.2 * sc, False))
        items.append(("dot", X(n - 1), Y(self.ivals[-1]), 4 * sc, col, C["card"], 2 * sc))
        texts.append((X(n - 1) + 12 * sc, Y(self.ivals[-1]) - 8 * sc, self._e(self.ivals[-1], 0), "w",
                      C["text"], "bodyb"))
        texts.append((X(n - 1) + 12 * sc, Y(self.ivals[-1]) + 9 * sc,
                      f"{self._e(chg, 0, sign=True)} ({fmt_pct(pct, 2, sign=True)})", "w", change_color(chg),
                      "small"))
        self._photo = paint_items(self, W, H, items, sc)
        for x, y, text, anchor, c, font in texts:
            self.create_text(x, y, text=text, anchor=anchor, fill=c, font=self.f[font])

    def _hover(self, e):
        self.delete("hover")
        if not self.geom:
            return
        L, R, T, B, n, X, Y = self.geom
        W, H, sc = self.winfo_width(), self.winfo_height(), self.sc
        if n < 2 or e.x < L - 4 or e.x > W - R + 4:
            return
        i = max(0, min(n - 1, int(round((e.x - L) / max(1, W - L - R) * (n - 1)))))
        x = X(i)
        self.create_line(x, T, x, H - B, fill=C["faint"], tags="hover")
        r = 4 * sc
        if self.mode == "intraday":
            v = self.ivals[i]
            chg, pct = self.change(i)
            self.create_oval(x - r, Y(v) - r, x + r, Y(v) + r, fill=C["up"] if chg >= 0 else C["down"],
                             outline=C["card"], width=2 * sc, tags="hover")
            t = dt.datetime.fromtimestamp(self.times[i])
            title = (f"{t.day} {MONTHS[t.month - 1]} {t.hour}:{t.minute:02d}" if self.multi_day
                     else f"{t.hour}:{t.minute:02d}")
            rows = [("Portfolio value", self._e(v), None, None)]
            if self.contrib:
                rows.append(("Contributed", self._e(self.contrib[i]), None, None))
            rows.append(("Change" + (" (excl. deposits)" if self.contrib else ""),
                         f"{self._e(chg, sign=True)} ({fmt_pct(pct, 2, sign=True)})", None, change_color(chg)))
        else:
            dep = self.own[i] + self.gift[i] + self.loan[i]
            for v, col in ((self.value[i], C["value"]), (dep, C["loan"]), (self.own[i], C["own"])):
                self.create_oval(x - r, Y(v) - r, x + r, Y(v) + r, fill=col, outline=C["card"], width=2 * sc,
                                 tags="hover")
            d = self.days[i]
            gain = self.value[i] - dep
            title = f"{d.day} {MONTHS[d.month - 1]} {d.year}"
            rows = [("Portfolio value", self._e(self.value[i]), C["value"], None),
                    ("Contributed", self._e(dep), None, None),
                    ("Student loan", self._e(self.loan[i]), C["loan"], None),
                    ("Own money", self._e(self.own[i]), C["own"], None)]
            if self.gift[i] > 0.005:
                rows.append(("Gifted shares", self._e(self.gift[i]), C["gift"], None))
            rows.append(("Return", self._e(gain, sign=True), None, change_color(gain)))
        _tooltip(self, x, e.y, title, rows, sc, self.f, (4 * sc, T, W - 4 * sc, H - B))


def squarify(values: List[float], x: float, y: float, w: float, h: float):
    """Squarified treemap (Bruls et al. 2000): rectangles as close to squares as possible.
    values in descending order. Returns [(x, y, w, h)] in the same order."""
    total = sum(values)
    if total <= 0 or w <= 0 or h <= 0:
        return [(x, y, 0, 0) for _ in values]
    areas = [v * w * h / total for v in values]
    rects, i = [], 0

    def worst(row, length):
        s = sum(row)
        return max(max(length * length * r / (s * s), (s * s) / (length * length * r)) for r in row)

    while i < len(areas):
        length = min(w, h)
        row = [areas[i]]
        i += 1
        while i < len(areas) and worst(row + [areas[i]], length) <= worst(row, length):
            row.append(areas[i])
            i += 1
        s = sum(row)
        if w >= h:  # column along the left edge
            rw = s / h if h else 0
            yy = y
            for r in row:
                rh = r / rw if rw else 0
                rects.append((x, yy, rw, rh))
                yy += rh
            x, w = x + rw, w - rw
        else:       # row along the top edge
            rh = s / w if w else 0
            xx = x
            for r in row:
                rww = r / rh if rh else 0
                rects.append((xx, y, rww, rh))
                xx += rww
            y, h = y + rh, h - rh
    return rects


class Treemap(tk.Canvas):
    """Allocation treemap: tile size = share of the portfolio, colour = today's price change.
    items: [dict(key, label, value, value_txt, pct, tip=[(name, value, colour, value colour)], title)]"""

    def __init__(self, master, sc: float, fonts: dict, on_click: Optional[Callable[[str], None]] = None):
        super().__init__(master, bg=C["card"], highlightthickness=0, bd=0, cursor="hand2")
        self.sc, self.f, self.on_click = sc, fonts, on_click
        self.items: List[dict] = []
        self.rects: List[Tuple[float, float, float, float, dict]] = []
        self._photo = None
        self._job = None
        self.bind("<Configure>", lambda e: self._later())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))
        self.bind("<Button-1>", self._click)

    def _later(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(30, self.redraw)

    def set_data(self, items: List[dict]):
        self.items = [it for it in items if it["value"] > 0]
        self.items.sort(key=lambda it: -it["value"])
        self.redraw()

    @staticmethod
    def tile_color(pct: Optional[float]) -> str:
        base = C["card2"]
        if pct is None:
            return blend(C["faint"], base, 0.25)
        t = 0.18 + 0.62 * min(abs(pct) / 0.05, 1.0)   # a 5% move = full colour
        return blend(C["up"] if pct >= 0 else C["down"], base, t)

    def redraw(self):
        self._job = None
        self.delete("all")
        W, H = self.winfo_width(), self.winfo_height()
        sc = self.sc
        if W < 40 or H < 40:
            return
        if not self.items:
            self.create_text(W / 2, H / 2, text="No holdings yet.", fill=C["muted"], font=self.f["body"])
            return
        gap, rad = 3 * sc, 8 * sc
        rects = squarify([it["value"] for it in self.items], 0, 0, W, H)
        self.rects, items, texts = [], [], []
        for (x, y, w, h), it in zip(rects, self.items):
            x0, y0, x1, y1 = x + gap / 2, y + gap / 2, x + w - gap / 2, y + h - gap / 2
            col = self.tile_color(it.get("pct"))
            items.append(("rrect", x0, y0, x1, y1, rad, col))
            self.rects.append((x0, y0, x1, y1, it))
            tw, th = x1 - x0, y1 - y0
            fg = "#111214" if luminance(col) > 0.55 else "#F4F4F5"
            sub = "#2B2D31" if luminance(col) > 0.55 else blend("#F4F4F5", col, 0.75)
            pct = it.get("pct")
            ptxt = fmt_pct(pct, 2, sign=True) if pct is not None else "–"
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            # as many lines as fit: ticker, today's move, then value and shares × price with cents
            if tw > 74 * sc and th > 50 * sc:
                lines = [(it["label"], "bodyb", fg, 18), (ptxt, "body", sub, 17)]
                lines += [(t, "small" if k == 0 else "tiny", sub, 15 if k == 0 else 14)
                          for k, t in enumerate(it.get("sub", []))]
            elif tw > 52 * sc and th > 36 * sc:
                lines = [(it["label"], "small", fg, 15), (ptxt, "tiny", sub, 14)]
            elif tw > 34 * sc and th > 18 * sc:
                lines = [(it["label"], "tiny", fg, 14)]
            else:
                lines = []
            fit, used = [], 8 * sc
            for ln in lines:
                if used + ln[3] * sc > th or self.f[ln[1]].measure(ln[0]) > tw - 8 * sc:
                    if len(fit) >= 1 and ln is not lines[0]:
                        break
                    if ln is lines[0]:
                        break
                fit.append(ln)
                used += ln[3] * sc
            y = cy - sum(ln[3] for ln in fit) * sc / 2
            for text, font, col, lh in fit:
                texts.append((cx, y + lh * sc / 2, text, col, font))
                y += lh * sc
        self._photo = paint_items(self, W, H, items, sc)
        for x, y, text, col, font in texts:
            self.create_text(x, y, text=text, fill=col, font=self.f[font])

    def _at(self, x, y):
        for x0, y0, x1, y1, it in self.rects:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return x0, y0, x1, y1, it
        return None

    def _hover(self, e):
        self.delete("hover")
        hit = self._at(e.x, e.y)
        if not hit:
            return
        x0, y0, x1, y1, it = hit
        self.create_rectangle(x0 + 1, y0 + 1, x1 - 1, y1 - 1, outline=C["text"], width=max(1, int(1.5 * self.sc)),
                              tags="hover")
        W, H = self.winfo_width(), self.winfo_height()
        _tooltip(self, e.x, e.y, it.get("title", it["label"]), it.get("tip", []), self.sc, self.f,
                 (4, 4, W - 4, H - 4))

    def _click(self, e):
        hit = self._at(e.x, e.y)
        if hit and self.on_click:
            self.on_click(hit[4]["key"])
