#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Portfolio Manager - investment and loan tracker
===============================================

Desktop app for logging Nordnet deposits, trades and dividends plus loan drawdowns and
payments. Shows the portfolio value with live prices, the allocation, today's movers and
how much of the money in Nordnet is student loan and how much is your own.

Pages (top menu): Overview, Portfolio, Transactions, Loans, Notes, Settings.

Requires Python 3.8+ with Tkinter. Pillow (PIL) makes the graphics anti-aliased. Prices
come from Yahoo Finance (prices.py); without a connection the latest saved prices are shown.

Start:  python portfolio_manager.py   (or double-click Start.bat)
        python portfolio_manager.py --data path.json   (another data file)

Data is saved automatically to data/portfolio.json (daily backup in
data/backups).
"""

import csv
import datetime as dt
import os
import subprocess
import sys
import time
import webbrowser
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk, filedialog
import tkinter.font as tkfont

from prices import PriceService, tradingview_url
from accounting import (APP_NAME, CURRENCIES, DATA_FILE, LEGACY_DATA_DIR, excluded_securities, KIND_SIGN, estimate_rate, session_date, KIND_TABLE, LOAN_KIND_KEYS, LOAN_KINDS,
                        NORDNET_KINDS, Store, Summary, Valuation, fmt_date, fmt_eur, fmt_input, fmt_money,
                        fmt_money_short, fmt_num, fmt_pct, fmt_price, fmt_qty, fmt_usd, intraday_pnl, intraday_values, ledger,
                        new_id, parse_date, parse_num, resource_path, sorted_entries, symbol_of, valuate,
                        value_history)
from widgets import (C, HAVE_PIL, MASK, MASK_EUR, MASK_USD, EyeButton, THEMES, WHEEL_EVENTS, ChipGrid, NavBar, ScrollFrame, Segmented, StackBar,
                         Treemap, ValueChart, blend, change_color, set_titlebar_dark, set_window_app_id,
                         system_theme)

if HAVE_PIL:
    from PIL import Image, ImageTk

APP_ID = "TomasVallas.PortfolioManager"

PAGES = [("dash", "Overview"), ("port", "Portfolio"), ("log", "Transactions"), ("loans", "Loans"),
         ("notes", "Notes"), ("settings", "Settings")]
# chart ranges: (key, label, days of daily history; None = intraday or everything)
RANGES = [("1d", "1D", None), ("1w", "1W", None), ("1m", "1M", 31), ("3m", "3M", 92), ("1y", "1Y", 366),
          ("all", "All", None)]

# ---------------------------------------------------------------------------
#  Entry form
# ---------------------------------------------------------------------------

# key: (label, field type, unit)
FIELD_DEF = {
    "date": ("Date", "entry", ""),
    "account": ("Account", "seg", ""),
    "to_account": ("To account", "seg", ""),
    "loan": ("Loan", "combo", ""),
    "security": ("Security", "combo", ""),
    "qty": ("Quantity", "entry", "pcs"),
    "price": ("Price", "entry", "@ccy"),
    "currency": ("Currency", "ccy", ""),
    "fees": ("Fees", "entry", "€"),
    "amount": ("Amount", "entry", "€"),
    "interest": ("Interest", "entry", "€"),
    "loan_part": ("Of which student loan", "entry", "€"),
    "note": ("Note", "wide", ""),
}
TRADE = ["date", "account", "security", "qty", "price", "currency", "fees", "amount", "note"]
KIND_FIELDS = {
    "deposit": ["date", "account", "amount", "loan_part", "note"],
    "withdrawal": ["date", "account", "amount", "loan_part", "note"],
    "transfer": ["date", "account", "to_account", "amount", "note"],
    "buy": TRADE,
    "sell": TRADE,
    "dividend": ["date", "account", "security", "amount", "note"],
    "fee": ["date", "account", "amount", "note"],
    "transfer_in": ["date", "account", "security", "qty", "price", "currency", "amount", "note"],
    "loan_draw": ["date", "loan", "amount", "note"],
    "loan_cap": ["date", "loan", "amount", "note"],
    "loan_pay": ["date", "loan", "amount", "interest", "fees", "note"],
    "loan_credit": ["date", "loan", "amount", "note"],
}
LABEL_OVERRIDE = {
    ("transfer", "account"): "From account",
    ("buy", "amount"): "Amount EUR (empty = estimate)",
    ("sell", "amount"): "Amount EUR (empty = estimate)",
    ("dividend", "amount"): "Dividend (net)",
    ("fee", "amount"): "Fee, interest or tax",
    ("transfer_in", "price"): "Cost per share",
    ("transfer_in", "amount"): "Cost basis EUR (empty = from price)",
    ("loan_draw", "amount"): "Amount drawn",
    ("loan_cap", "amount"): "Capitalized interest",
    ("loan_pay", "amount"): "Principal repaid",
    ("loan_pay", "fees"): "Bank fees",
    ("loan_credit", "amount"): "Student loan credit",
}
KIND_HINT = {
    "deposit": "Deposit into Nordnet. Enter how much of it is student loan – the rest counts as your own money.",
    "buy": "Quantity, price and fees as on Nordnet's Trades tab. The EUR amount appears under Transactions "
           "later; if you leave it empty it is estimated from the latest trade's exchange rate.",
    "sell": "The EUR amount is what the account receives after fees. An empty amount is estimated from the "
            "latest trade's exchange rate.",
    "dividend": "Dividend paid to the account after withholding tax.",
    "withdrawal": "Withdrawal from Nordnet to your bank. Enter the student loan part if you take loan money out.",
    "transfer": "Transfer between your own Nordnet accounts (e.g. “Internal from” or “cover from another "
                "account”). Does not change your contributions.",
    "fee": "Any other fee, overdraft interest or tax charged to the account.",
    "transfer_in": "Shares moved into Nordnet from elsewhere, e.g. a gift (“Jättö siirto”). For a gift the cost "
                   "basis is the market value on the day you received it.",
    "loan_draw": "Loan installment the bank paid to your bank account.",
    "loan_cap": "Interest the bank adds to the loan (e.g. on 15 Jun and 15 Dec).",
    "loan_pay": "Principal, interest and fees you pay to the bank.",
    "loan_credit": "Kela's student loan credit, which reduces the loan.",
}

TREE_COLS = [("date", "Date", 82, "w"), ("acc", "Account", 82, "w"), ("kind", "Type", 100, "w"),
             ("desc", "Description", 150, "w"), ("qty", "Qty", 46, "e"), ("price", "Price", 92, "e"),
             ("fees", "Fees", 50, "e"), ("sum", "Amount €", 96, "e"), ("fund", "Funding", 150, "w")]


def short_time(ts: float) -> str:
    t = dt.datetime.fromtimestamp(ts)
    hm = f"{t.hour}:{t.minute:02d}"
    return hm if t.date() == dt.date.today() else f"{fmt_date(t.date())[:-5]} {hm}"


class App(tk.Tk):
    def __init__(self, data_path: str = DATA_FILE, live: bool = True):
        super().__init__()
        self.store = Store(data_path)
        self.data = self.store.data
        theme = self.data["settings"].get("theme") or system_theme()
        self.theme = theme if theme in THEMES else "dark"
        C.clear()
        C.update(THEMES[self.theme])
        self.title("Portfolio Manager – investments, loans and own money")
        self.sc = self.winfo_fpixels("1i") / 96.0
        sc = self.sc
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(int(1440 * sc), sw - 40), min(int(940 * sc), sh - 80)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(min(int(1180 * sc), w), min(int(700 * sc), h))
        self._icon()
        if os.name == "nt":
            try:
                self.update_idletasks()
                set_window_app_id(int(self.wm_frame(), 16), APP_ID)
            except Exception:
                pass
        set_titlebar_dark(self, self.theme == "dark")
        self._fonts()
        self.summary = Summary()
        self.val = Valuation()
        self.vhist: List[float] = []
        self.editing: Optional[str] = None
        self._undo: Optional[Tuple[int, dict]] = None
        self._saved_form = None
        self._filter = "All"
        self._jobs: Dict[str, object] = {}
        self.main: Optional[tk.Frame] = None
        self.page_var = tk.StringVar(value="dash")
        self.range_key = "1y"
        self.hidden = bool(self.data["settings"].get("hide_values", False))  # privacy mode on the Overview
        st = self.data["settings"]
        self.prices = PriceService(os.path.join(self.store.folder, "prices.json"), int(st.get("interval", 60)))
        self.prices.enabled = live and bool(st.get("live", True))
        self.quotes, self.hist, _found, self.intraday = self.prices.snapshot()
        self._seen_version = self.prices.version
        self._seen_rest = self.prices.rest_version
        self._tables: Dict[str, dict] = {}
        self._stale: set = set()
        self._build_all()
        self.refresh()
        if self.store.error:
            self.flash(self.store.error, bad=True, sticky=True)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind_all("<Control-n>", lambda e: self.new_entry())
        self._request_prices()
        self.prices.start()
        self.after(700, self._poll)

    # ---- look --------------------------------------------------------------
    def S(self, px: float) -> int:
        return int(round(px * self.sc))

    def _icon(self):
        png = resource_path("icon", "icon-256.png")
        self._brand = None
        try:
            if os.path.exists(png) and HAVE_PIL:
                src = Image.open(png).convert("RGBA")
                lz = getattr(Image, "Resampling", Image).LANCZOS
                self._imgs = [ImageTk.PhotoImage(src.resize((n, n), lz), master=self)
                              for n in (256, 64, 48, 32, 24, 16)]
                self.iconphoto(True, *self._imgs)
                n = int(28 * self.sc)
                self._brand = ImageTk.PhotoImage(src.resize((n, n), lz), master=self)
            elif os.path.exists(png):
                self._imgs = [tk.PhotoImage(file=png, master=self)]
                self.iconphoto(True, *self._imgs)
        except (tk.TclError, OSError):
            pass

    def _fonts(self):
        fam = "Segoe UI" if "Segoe UI" in tkfont.families() else "TkDefaultFont"
        semi = "Segoe UI Semibold" if "Segoe UI Semibold" in tkfont.families() else fam
        self.f = {
            "hero": tkfont.Font(family=semi, size=26),
            "kpi": tkfont.Font(family=semi, size=16),
            "brand": tkfont.Font(family=fam, size=13, weight="bold"),
            "nav": tkfont.Font(family=fam, size=10),
            "cardt": tkfont.Font(family=semi, size=11),
            "h": tkfont.Font(family=fam, size=9, weight="bold"),
            "body": tkfont.Font(family=fam, size=10),
            "bodyb": tkfont.Font(family=fam, size=10, weight="bold"),
            "small": tkfont.Font(family=fam, size=9),
            "tiny": tkfont.Font(family=fam, size=8),
        }

    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        for name, fb, fg, bc in (("TCombobox", C["input"], C["text"], C["border"]),
                                 ("Bad.TCombobox", C["bad_bg"], C["bad_fg"], C["bad"])):
            st.configure(name, fieldbackground=fb, background=C["card2"], foreground=fg,
                         arrowcolor=C["muted"], bordercolor=bc, lightcolor=fb, darkcolor=fb,
                         insertcolor=C["text"], selectbackground=C["accent"],
                         selectforeground=C["on_accent"], padding=self.S(3))
            st.map(name, fieldbackground=[("readonly", fb)], foreground=[("readonly", fg)],
                   background=[("active", C["hover"])], bordercolor=[("focus", C["accent"])],
                   selectbackground=[("readonly", fb)], selectforeground=[("readonly", fg)])
        self.option_add("*TCombobox*Listbox.background", C["card2"])
        self.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", C["on_accent"])
        self.option_add("*TCombobox*Listbox.font", self.f["body"])
        self.option_add("*TCombobox*Listbox.borderWidth", 0)
        for name, trough in (("App.Vertical.TScrollbar", C["bg"]), ("Card.Vertical.TScrollbar", C["card"])):
            st.configure(name, background=C["card2"], troughcolor=trough, bordercolor=trough,
                         arrowcolor=C["muted"], lightcolor=C["card2"], darkcolor=C["card2"], gripcount=0)
            st.map(name, background=[("active", C["border"])])
        st.configure("Log.Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["text"],
                     bordercolor=C["card"], lightcolor=C["card"], darkcolor=C["card"],
                     rowheight=self.S(28), font=self.f["body"], borderwidth=0, relief="flat")
        st.map("Log.Treeview", background=[("selected", C["sel"])], foreground=[("selected", C["text"])])
        st.configure("Log.Treeview.Heading", background=C["card"], foreground=C["muted"], font=self.f["h"],
                     relief="flat", borderwidth=0, bordercolor=C["border"], lightcolor=C["card"],
                     darkcolor=C["card"], padding=(self.S(4), self.S(6)))
        st.map("Log.Treeview.Heading", background=[("active", C["card2"])])
        st.layout("Log.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    # ---- building blocks ---------------------------------------------------
    def card(self, parent, title: str = None, sub: str = None):
        """Card: returns (outer frame, body, title row). The caller places the outer frame."""
        S = self.S
        outer = tk.Frame(parent, bg=C["card"], highlightthickness=1, highlightbackground=C["border"])
        head = None
        if title:
            head = tk.Frame(outer, bg=C["card"])
            head.pack(fill="x", padx=S(18), pady=(S(14), S(10)))
            tk.Label(head, text=title, bg=C["card"], fg=C["text"], font=self.f["cardt"]).pack(side="left")
            if sub:
                tk.Label(head, text=sub, bg=C["card"], fg=C["faint"], font=self.f["small"]).pack(
                    side="left", padx=(S(10), 0), pady=(S(2), 0))
        body = tk.Frame(outer, bg=C["card"])
        body.pack(fill="both", expand=True, padx=S(18), pady=(0 if title else S(16), S(16)))
        return outer, body, head

    def entry(self, parent, var: tk.StringVar, width: int = 12, justify: str = "right") -> tk.Entry:
        return tk.Entry(parent, textvariable=var, width=width, justify=justify, bg=C["input"],
                        fg=C["text"], insertbackground=C["text"], relief="flat", font=self.f["body"],
                        highlightthickness=1, highlightbackground=C["border"], highlightcolor=C["accent"],
                        selectbackground=C["accent"], selectforeground=C["on_accent"])

    def label(self, parent, text: str = "", fg: str = None, font: str = "body", bg: str = None,
              **kw) -> tk.Label:
        return tk.Label(parent, text=text, bg=bg or C["card"], fg=fg or C["muted"], font=self.f[font], **kw)

    def note(self, parent, text: str = "", fg: str = None, wrap: int = 380) -> tk.Label:
        return tk.Label(parent, text=text, bg=C["card"], fg=fg or C["muted"], font=self.f["small"],
                        justify="left", anchor="w", wraplength=self.S(wrap))

    def autowrap(self, lbl: tk.Label):
        lbl.bind("<Configure>", lambda e: lbl.configure(wraplength=max(120, e.width - 4)))
        return lbl

    def button(self, parent, text: str, command, style: str = "secondary", bg: str = None) -> tk.Label:
        base = bg or C["card2"]
        cols = {"primary": (C["accent"], C["on_accent"], C["accent"], C["accent_hi"]),
                "secondary": (base, C["text"], C["border"], C["hover"]),
                "danger": (base, C["red"], C["border"], C["hover"])}[style]
        b = tk.Label(parent, text=text, bg=cols[0], fg=cols[1],
                     font=self.f["bodyb" if style == "primary" else "small"],
                     padx=self.S(14), pady=self.S(6), cursor="hand2", highlightthickness=1,
                     highlightbackground=cols[2])
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: b.configure(bg=cols[3]))
        b.bind("<Leave>", lambda e: b.configure(bg=cols[0]))
        return b

    def link(self, parent, text: str, command, bg: str = None, font: str = "small") -> tk.Label:
        lbl = tk.Label(parent, text=text, bg=bg or C["card"], fg=C["accent"], font=self.f[font], cursor="hand2")
        lbl.bind("<Button-1>", lambda e: command())
        return lbl

    def dot(self, parent, col: str) -> tk.Frame:
        return tk.Frame(parent, bg=C.get(col, col), width=self.S(9), height=self.S(9))

    def later(self, name: str, ms: int, fn):
        """Delayed call: a new request with the same name replaces the previous one."""
        if self._jobs.get(name):
            self.after_cancel(self._jobs[name])
        self._jobs[name] = self.after(ms, lambda: (self._jobs.pop(name, None), fn()))

    def _wheel_passthrough(self, widget, scroller: ScrollFrame):
        """The mouse wheel scrolls the panel instead of changing the dropdown value."""
        for ev in WHEEL_EVENTS:
            widget.bind(ev, lambda e: (scroller.wheel(e), "break")[1])

    @staticmethod
    def _clear(frame: tk.Widget):
        for w in frame.winfo_children():
            w.destroy()

    # ---- layout ------------------------------------------------------------
    def _build_all(self):
        if self.main is not None:
            self._capture_form()
            self.main.destroy()
        self.configure(bg=C["bg"])
        self._style()
        self.main = tk.Frame(self, bg=C["bg"])
        self.main.pack(fill="both", expand=True)
        self._ticker_names = None
        self._tables = {}
        self._build_topbar()
        self.container = tk.Frame(self.main, bg=C["bg"])
        self.container.pack(fill="both", expand=True)
        self.pages: Dict[str, tk.Frame] = {}
        self._build_dash()
        self._build_port()
        self._build_log()
        self._build_loans()
        self._build_notes()
        self._build_settings()
        self._build_toast()
        self._restore_form()
        self._show_page()

    def _build_topbar(self):
        S = self.S
        bar = tk.Frame(self.main, bg=C["bar"])
        bar.pack(fill="x")
        inner = tk.Frame(bar, bg=C["bar"])
        inner.pack(fill="x", padx=S(20))
        brand = tk.Frame(inner, bg=C["bar"], cursor="hand2")
        brand.pack(side="left")
        if self._brand:
            tk.Label(brand, image=self._brand, bg=C["bar"]).pack(side="left", padx=(0, S(9)))
        tk.Label(brand, text=APP_NAME, bg=C["bar"], fg=C["text"], font=self.f["brand"]).pack(side="left")
        for w in [brand] + list(brand.winfo_children()):
            w.bind("<Button-1>", lambda e: self.goto("dash"))
        NavBar(inner, PAGES, self.page_var, self.sc, self.f["nav"], self._show_page).pack(
            side="left", padx=(S(30), 0))
        self.button(inner, "+  New entry", self.new_entry, "primary").pack(side="right")
        self.theme_var = tk.StringVar(value=self.theme)
        Segmented(inner, [("light", "Light"), ("dark", "Dark")], self.theme_var, self.sc, self.f["small"],
                  lambda: self.after_idle(self._theme_changed)).pack(side="right", padx=(0, S(16)))
        live = tk.Frame(inner, bg=C["bar"], cursor="hand2")
        live.pack(side="right", padx=(0, S(18)))
        self.live_dot = tk.Canvas(live, width=S(10), height=S(10), bg=C["bar"], highlightthickness=0)
        self.live_dot.pack(side="left", padx=(0, S(7)))
        self.live_lbl = tk.Label(live, text="", bg=C["bar"], fg=C["muted"], font=self.f["small"])
        self.live_lbl.pack(side="left")
        for w in (live, self.live_dot, self.live_lbl):
            w.bind("<Button-1>", lambda e: self.refresh_prices())
        tk.Frame(self.main, bg=C["border"], height=1).pack(fill="x")

    def _page(self, key: str, scroll: bool = True):
        """New page: returns the content frame (and the scrolling area)."""
        S = self.S
        if scroll:
            sf = ScrollFrame(self.container, C["bg"], autohide=True)
            self.pages[key] = sf
            pad = tk.Frame(sf.inner, bg=C["bg"])
            pad.pack(fill="both", expand=True, padx=S(22), pady=S(18))
            return pad, sf
        pg = tk.Frame(self.container, bg=C["bg"])
        self.pages[key] = pg
        pad = tk.Frame(pg, bg=C["bg"])
        pad.pack(fill="both", expand=True, padx=S(22), pady=S(18))
        return pad, None

    def goto(self, page: str):
        self.page_var.set(page)
        self._show_page()

    def _show_page(self):
        p = self.page_var.get()
        for k, f in self.pages.items():
            if k != p:
                f.pack_forget()
        self.pages[p].pack(fill="both", expand=True)
        if p in self._stale:  # prices moved while the page was hidden
            self._stale.discard(p)
            self._page_refreshers().get(p, lambda: None)()
        if isinstance(self.pages[p], ScrollFrame):
            self.pages[p].canvas.yview_moveto(0)
            self.pages[p]._schedule_fit()

    # ---- Overview ----------------------------------------------------------
    def _build_dash(self):
        S = self.S
        root, sf = self._page("dash")
        row1 = tk.Frame(root, bg=C["bg"])
        row1.pack(fill="x")
        for c in range(4):
            row1.columnconfigure(c, weight=1, uniform="r1")

        hero, b, _ = self.card(row1)
        hero.grid(row=0, column=0, columnspan=2, sticky="nsew")
        top = tk.Frame(b, bg=C["card"])
        top.pack(anchor="w")
        self.label(top, "Portfolio value", font="small").pack(side="left")
        EyeButton(top, self.sc, self.hidden, self._set_hidden).pack(side="left", padx=(S(8), 0))
        self.hero_val = self.label(b, "–", fg=C["text"], font="hero")
        self.hero_val.pack(anchor="w")
        self.hero_usd = self.label(b, "", fg=C["muted"], font="small")
        self.hero_usd.pack(anchor="w", pady=(0, S(6)))
        r = tk.Frame(b, bg=C["card"])
        r.pack(anchor="w")
        self.hero_day = self.label(r, "", font="bodyb")
        self.hero_day.pack(side="left")
        self.label(r, "  today", font="body").pack(side="left")
        self.hero_gain = self.label(b, "", font="body")
        self.hero_gain.pack(anchor="w", pady=(S(6), 0))
        self.hero_note = self.label(b, "", fg=C["faint"], font="small")
        self.hero_note.pack(anchor="w", pady=(S(2), 0))

        c, b, _ = self.card(row1, "Funding")
        c.grid(row=0, column=2, sticky="nsew", padx=(S(14), 0))
        self.fund_rows, self.fund_vals = {}, {}
        for key, name in (("loan", "Student loan"), ("own", "Own money"), ("gift", "Gifted shares"),
                          ("ext", "Binance & Nordea")):
            r = tk.Frame(b, bg=C["card"])
            r.pack(fill="x", pady=S(2))
            self.dot(r, key).pack(side="left", padx=(0, S(8)))
            self.label(r, name, fg=C["text"]).pack(side="left")
            self.fund_vals[key] = self.label(r, "", fg=C["text"], font="bodyb")
            self.fund_vals[key].pack(side="right")
            self.fund_rows[key] = r
        self.fund_bar = StackBar(b, self.sc, 10)
        self.fund_bar.pack(fill="x", pady=(S(10), S(6)))
        self.fund_note = self.label(b, "", font="small")
        self.fund_note.pack(anchor="w")

        c, b, _ = self.card(row1, "Loans")
        c.grid(row=0, column=3, sticky="nsew", padx=(S(14), 0))
        self.label(b, "Loan balance", font="small").pack(anchor="w")
        self.debt_val = self.label(b, "", fg=C["text"], font="kpi")
        self.debt_val.pack(anchor="w")
        self.label(b, "Net worth", font="small").pack(anchor="w", pady=(S(8), 0))
        self.worth_val = self.label(b, "", fg=C["text"], font="kpi")
        self.worth_val.pack(anchor="w")
        self.label(b, "portfolio value − loans", fg=C["faint"], font="tiny").pack(anchor="w")

        row2 = tk.Frame(root, bg=C["bg"])
        row2.pack(fill="x", pady=(S(14), 0))
        row2.columnconfigure(0, weight=3)
        row2.columnconfigure(1, weight=1, minsize=S(320))
        c, b, _ = self.card(row2, "Allocation", "size = share of portfolio · colour = today's move · "
                                                "click: TradingView")
        c.grid(row=0, column=0, sticky="nsew")
        self.treemap = Treemap(b, self.sc, self.f, on_click=self.open_tradingview)
        self.treemap.configure(height=S(290))
        self.treemap.pack(fill="both", expand=True)
        c, b, _ = self.card(row2, "Today's movers")
        c.grid(row=0, column=1, sticky="nsew", padx=(S(14), 0))
        self.movers = tk.Frame(b, bg=C["card"])
        self.movers.pack(fill="both", expand=True)
        self.movers_empty = self.note(self.movers, "No holdings.")
        self.mover_rows, self.mover_keys = [], [None] * 10
        for i in range(10):
            row = tk.Frame(self.movers, bg=C["card"], cursor="hand2")
            t = self.label(row, "", fg=C["text"], font="bodyb", width=7, anchor="w")
            t.pack(side="left")
            n = self.label(row, "", font="small", anchor="w")
            n.pack(side="left", padx=(S(6), 0))
            pill = tk.Label(row, text="", bg=C["card"], font=self.f["bodyb"], width=8, anchor="e", padx=S(6),
                            pady=S(2))
            pill.pack(side="right")
            px = self.label(row, "", fg=C["text"], font="small", anchor="e")
            px.pack(side="right", padx=(0, S(8)))
            n.px = px
            for w in (row, t, n, pill, px):
                w.bind("<Button-1>", lambda e, i=i: self.mover_keys[i] and self.open_tradingview(self.mover_keys[i]))
            self.mover_rows.append((row, t, n, pill))

        c, b, head = self.card(root, "Portfolio performance")
        c.pack(fill="both", expand=True, pady=(S(14), 0))
        self.range_var = tk.StringVar(value=self.range_key)
        Segmented(head, [(k, t) for k, t, _ in RANGES], self.range_var, self.sc, self.f["small"],
                  self._range_changed).pack(side="right")
        self.legend = tk.Frame(head, bg=C["card"])
        self.legend.pack(side="right", padx=(0, S(18)))
        self.chart = ValueChart(b, self.sc, self.f)
        self.chart.configure(height=S(280))
        self.chart.pack(fill="both", expand=True)
        sf.grow, sf.grow_min = self.chart, S(210)

    def _set_hidden(self, on: bool):
        """Eye button: hide or show all amounts on the Overview (percentages stay visible)."""
        self.hidden = on
        self.data["settings"]["hide_values"] = on
        self._save()
        self._refresh_dash()

    def _range_changed(self):
        self.range_key = self.range_var.get()
        self.prices.set_want_week(self.range_key == "1w" and self.prices.enabled)
        self._update_chart()

    # ---- Portfolio ---------------------------------------------------------
    def _build_port(self):
        S = self.S
        root, _sf = self._page("port")
        c, b, head = self.card(root, "Holdings", "in US dollars · live prices")
        c.pack(fill="x")
        self.button(head, "Refresh prices", self.refresh_prices).pack(side="right")
        self.hold_box = tk.Frame(b, bg=C["card"])
        self.hold_box.pack(fill="x")
        self.hold_note = self.autowrap(self.note(b, fg=C["faint"]))
        self.hold_note.pack(anchor="w", fill="x", pady=(S(10), 0))
        row = tk.Frame(root, bg=C["bg"])
        row.pack(fill="x", pady=(S(14), 0))
        row.columnconfigure(0, weight=3, uniform="p")
        row.columnconfigure(1, weight=2, uniform="p")
        c, self.ext_box, _ = self.card(root, "Binance & Nordea", "outside Nordnet – quantity and average cost, "
                                                       "saved automatically")
        c.pack(fill="x", pady=(S(14), 0), before=row)
        self._build_ext()
        c, self.acc_box, _ = self.card(row, "Accounts", "in euros")
        c.grid(row=0, column=0, sticky="nsew")
        c, self.stats_box, _ = self.card(row, "Income and costs", "in euros")
        c.grid(row=0, column=1, sticky="nsew", padx=(S(14), 0))

    def _build_ext(self):
        S, box = self.S, self.ext_box
        for c, h in enumerate(["Platform", "Asset", "Ticker", "Quantity", "Avg cost", ""]):
            self.label(box, h, font="h").grid(row=0, column=c, sticky="w", padx=(0, S(14)), pady=(0, S(6)))
        for r, x in enumerate(self.data.get("external", []), start=1):
            self.label(box, x.get("platform", ""), fg=C["text"]).grid(row=r, column=0, sticky="w", pady=S(2))
            self.label(box, x["name"], fg=C["text"]).grid(row=r, column=1, sticky="w", padx=(0, S(14)))
            self.label(box, symbol_of(self.data, x["name"]), font="small").grid(row=r, column=2, sticky="w")
            for c, key in ((3, "qty"), (4, "cost_price")):
                v = tk.StringVar(value=fmt_input(x.get(key)))
                self.entry(box, v, width=12).grid(row=r, column=c, sticky="w", padx=(0, S(14)), ipady=S(2))
                v.trace_add("write", lambda *a, x=x, key=key, v=v: self.later("ext", 700, lambda: self._ext_changed(x, key, v)))
            self.label(box, x.get("currency", "EUR"), font="small").grid(row=r, column=5, sticky="w")
        box.columnconfigure(1, weight=1)

    def _ext_changed(self, x: dict, key: str, var: tk.StringVar):
        try:
            v = parse_num(var.get())
        except ValueError:
            return
        if v is None or v < 0 or v == x.get(key):
            return
        x[key] = v
        self._save()
        self.refresh()
        self._request_prices()

    # ---- Transactions (form + table) ---------------------------------------
    def _build_log(self):
        S = self.S
        root, _ = self._page("log", scroll=False)
        self.form_sf = ScrollFrame(root, C["bg"], S(430))
        self.form_sf.pack(side="left", fill="y")
        self._build_form(self.form_sf.inner)
        c, b, head = self.card(root, "Transactions")
        c.pack(side="left", fill="both", expand=True, padx=(S(14), 0))
        self.filter_var = tk.StringVar(value=self._filter)
        self.count_lbl = self.label(head, "", font="small")
        self.count_lbl.pack(side="right")
        Segmented(head, ["All", "Nordnet", "Loans"], self.filter_var, self.sc, self.f["small"],
                  self.refresh_tree).pack(side="right", padx=(0, S(14)))
        self.label(b, "Click a row to edit it.", fg=C["faint"], font="small").pack(anchor="w", pady=(0, S(6)))
        holder = tk.Frame(b, bg=C["card"])
        holder.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(holder, columns=[c[0] for c in TREE_COLS], show="headings",
                                 style="Log.Treeview", selectmode="browse")
        for key, title, w, anchor in TREE_COLS:
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=S(w), minwidth=S(90 if key == "desc" else 40), anchor=anchor,
                             stretch=key == "desc")
        tsb = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview, style="Card.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("odd", background=C["card2"])
        self.tree.tag_configure("est", foreground=C["muted"])
        self.tree.bind("<<TreeviewSelect>>", self._tree_select)

    def _build_form(self, root):
        S = self.S
        acc_ids = [a["id"] for a in self.data["accounts"]]
        c, b, head = self.card(root, "New entry")
        c.pack(fill="x", padx=(0, S(10)))
        self.form_title = head.winfo_children()[0]
        self.kind = tk.StringVar(value="deposit")
        self.label(b, "NORDNET", fg=C["faint"], font="h").pack(anchor="w", pady=(0, S(4)))
        ChipGrid(b, NORDNET_KINDS, self.kind, self.sc, self.f["small"]).pack(fill="x")
        self.label(b, "LOAN", fg=C["faint"], font="h").pack(anchor="w", pady=(S(10), S(4)))
        ChipGrid(b, LOAN_KINDS, self.kind, self.sc, self.f["small"]).pack(fill="x")
        self.hint = self.note(b)
        self.hint.pack(anchor="w", fill="x", pady=(S(10), 0))

        ff = tk.Frame(b, bg=C["card"])
        ff.pack(fill="x", pady=(S(8), 0))
        ff.columnconfigure(0, weight=1)
        self.fv: Dict[str, tk.StringVar] = {k: tk.StringVar() for k in FIELD_DEF}
        self.fv["date"].set(fmt_date(dt.date.today()))
        self.fv["account"].set(acc_ids[0])
        self.fv["to_account"].set(acc_ids[1] if len(acc_ids) > 1 else acc_ids[0])
        self.fv["currency"].set("USD")
        self.fv["loan"].set(self.data["loans"][0]["name"] if self.data["loans"] else "Student loan")
        self.frow: Dict[str, list] = {}
        self.flabel: Dict[str, tk.Label] = {}
        self.fwidget: Dict[str, tk.Widget] = {}
        self.fcombo: Dict[str, ttk.Combobox] = {}
        for key, (text, wtype, unit) in FIELD_DEF.items():
            lbl = self.label(ff, text)
            parts = [(lbl, dict(column=0, sticky="w", pady=S(3)))]
            if wtype == "entry":
                e = self.entry(ff, self.fv[key])
                u = self.label(ff, "" if unit == "@ccy" else unit, fg=C["faint"], font="small", width=4,
                               anchor="w")
                if unit == "@ccy":
                    u.configure(textvariable=self.fv["currency"])
                parts += [(e, dict(column=1, sticky="e", ipady=S(3), pady=S(3))),
                          (u, dict(column=2, sticky="w", padx=(S(6), 0)))]
                self.fwidget[key] = e
            elif wtype == "seg":
                seg = Segmented(ff, acc_ids, self.fv[key], self.sc, self.f["small"])
                parts.append((seg, dict(column=1, columnspan=2, sticky="e", pady=S(3))))
            elif wtype in ("combo", "ccy"):
                cb = ttk.Combobox(ff, textvariable=self.fv[key], font=self.f["body"],
                                  width=7 if wtype == "ccy" else 26,
                                  values=CURRENCIES if wtype == "ccy" else [],
                                  state="readonly" if wtype == "ccy" else "normal")
                cb.bind("<<ComboboxSelected>>", lambda e, c=cb: c.selection_clear())
                self._wheel_passthrough(cb, self.form_sf)
                parts.append((cb, dict(column=1, columnspan=2, sticky="e", pady=S(3))))
                self.fcombo[key] = cb
                self.fwidget[key] = cb
            else:  # wide
                e = self.entry(ff, self.fv[key], width=29, justify="left")
                parts.append((e, dict(column=1, columnspan=2, sticky="e", ipady=S(3), pady=S(3))))
                self.fwidget[key] = e
            self.frow[key] = parts
            self.flabel[key] = lbl
        for w in self.fwidget.values():
            w.bind("<Return>", lambda e: self.submit())
            w.bind("<Escape>", lambda e: self.cancel_edit())
        self.form_info = self.note(b, fg=C["muted"])
        self.form_info.pack(anchor="w", fill="x", pady=(S(8), 0))
        btns = tk.Frame(b, bg=C["card"])
        btns.pack(fill="x", pady=(S(12), 0))
        self.btn_submit = self.button(btns, "Add entry", self.submit, "primary")
        self.btn_submit.pack(side="left")
        self.btn_cancel = self.button(btns, "Clear", self.cancel_edit)
        self.btn_cancel.pack(side="left", padx=(S(8), 0))
        self.btn_delete = self.button(btns, "Delete", self.delete_current, "danger")
        for v in self.fv.values():
            v.trace_add("write", lambda *a: self.later("form", 80, self._update_form_info))
        self.kind.trace_add("write", lambda *a: self._show_fields())
        self._show_fields()

    # ---- Loans -------------------------------------------------------------
    def _build_loans(self):
        S = self.S
        root, _ = self._page("loans")
        row = tk.Frame(root, bg=C["bg"])
        row.pack(fill="x")
        row.columnconfigure(0, weight=1, uniform="l")
        row.columnconfigure(1, weight=1, uniform="l")
        c, b, _ = self.card(row, "Student loan vs. own money", "contributed to Nordnet")
        c.grid(row=0, column=0, sticky="nsew")
        self.src_bar = StackBar(b, self.sc)
        self.src_bar.pack(fill="x", pady=(S(2), S(10)))
        self.src_leg = tk.Frame(b, bg=C["card"])
        self.src_leg.pack(fill="x")
        self.src_insight = self.autowrap(self.note(b, fg=C["text"]))
        self.src_insight.pack(anchor="w", fill="x", pady=(S(12), 0))
        c, b, _ = self.card(row, "Where the money is now", "at cost")
        c.grid(row=0, column=1, sticky="nsew", padx=(S(14), 0))
        self.use_bar = StackBar(b, self.sc)
        self.use_bar.pack(fill="x", pady=(S(2), S(10)))
        self.use_leg = tk.Frame(b, bg=C["card"])
        self.use_leg.pack(fill="x")
        self.use_note = self.autowrap(self.note(b, fg=C["faint"]))
        self.use_note.pack(anchor="w", fill="x", pady=(S(10), 0))
        self.loan_cards = tk.Frame(root, bg=C["bg"])
        self.loan_cards.pack(fill="x", pady=(S(14), 0))
        c, self.loan_list, _ = self.card(root, "Loan entries", "click a row to edit")
        c.pack(fill="x", pady=(S(14), 0))

    # ---- Notes -------------------------------------------------------------
    def _build_notes(self):
        S = self.S
        root, _ = self._page("notes", scroll=False)
        c, b, _ = self.card(root, "Notes", "plans and things to remember – saved automatically")
        c.pack(fill="both", expand=True)
        self.notes = tk.Text(b, wrap="word", bg=C["input"], fg=C["text"], insertbackground=C["text"],
                             relief="flat", font=self.f["body"], highlightthickness=1,
                             highlightbackground=C["border"], highlightcolor=C["accent"],
                             selectbackground=C["accent"], selectforeground=C["on_accent"],
                             padx=S(14), pady=S(12), undo=True, spacing1=S(2), spacing3=S(2))
        self.notes.pack(fill="both", expand=True)
        self.notes.insert("1.0", self.data.get("notes", ""))
        self.notes.edit_reset()
        self.notes.bind("<KeyRelease>", lambda e: self.later("notes", 800, self._save_notes))

    # ---- Settings ----------------------------------------------------------
    def _build_settings(self):
        S = self.S
        root, _sf = self._page("settings")
        cols = tk.Frame(root, bg=C["bg"])
        cols.pack(fill="x")
        cols.columnconfigure(0, weight=3, uniform="s")
        cols.columnconfigure(1, weight=2, uniform="s")
        left = tk.Frame(cols, bg=C["bg"])
        left.grid(row=0, column=0, sticky="nsew")
        right = tk.Frame(cols, bg=C["bg"])
        right.grid(row=0, column=1, sticky="nsew", padx=(S(14), 0))

        c, b, head = self.card(left, "Ticker symbols", "Yahoo Finance symbols for live prices")
        c.pack(fill="x")
        self.link(head, "Search missing again", self.research_symbols).pack(side="right")
        self.ticker_box = tk.Frame(b, bg=C["card"])
        self.ticker_box.pack(fill="x")
        self.autowrap(self.note(b, "Symbols are found automatically from the security name. You can correct "
                                   "one yourself (e.g. NVDA, TSM, SOFF.OL). Greyed-out securities have been sold.",
                                fg=C["faint"])).pack(anchor="w", fill="x", pady=(S(10), 0))

        c, b, _ = self.card(right, "Prices")
        c.pack(fill="x")
        r = tk.Frame(b, bg=C["card"])
        r.pack(fill="x")
        self.label(r, "Live prices").pack(side="left")
        self.live_var = tk.StringVar(value="on" if self.prices.enabled else "off")
        Segmented(r, [("on", "On"), ("off", "Off")], self.live_var, self.sc, self.f["small"],
                  self._live_changed).pack(side="right")
        self.price_status = self.autowrap(self.note(b))
        self.price_status.pack(anchor="w", fill="x", pady=(S(10), 0))
        self.button(b, "Refresh now", self.refresh_prices).pack(anchor="w", pady=(S(10), 0))
        self.autowrap(self.note(
            b, "Source: Yahoo Finance (free, no key). While the app is open, prices stream in real time and "
               "the screen updates every second. If the stream is unavailable, prices are fetched every 60 s. "
               "Yesterday's close and the 1D chart refresh every 60 s, the 1W chart every 5 min (only while "
               "selected) and daily history every 6 h. US exchanges are real time, others may be delayed about "
               "15 min. TradingView has no public data API, so it is only used for opening charts.", fg=C["faint"])).pack(anchor="w", fill="x",
                                                                                     pady=(S(10), 0))

        c, b, _ = self.card(right, "Accounts")
        c.pack(fill="x", pady=(S(14), 0))
        self.label(b, "Name", font="small").grid(row=0, column=1, sticky="w", padx=(S(8), 0))
        self.label(b, "Number", font="small").grid(row=0, column=2, sticky="w", padx=(S(8), 0))
        self.acc_vars = []
        for r_, acc in enumerate(self.data["accounts"], start=1):
            self.label(b, acc["id"], fg=C["text"], font="bodyb").grid(row=r_, column=0, sticky="w", pady=S(3))
            nv, numv = tk.StringVar(value=acc.get("name", "")), tk.StringVar(value=acc.get("number", ""))
            self.entry(b, nv, width=20, justify="left").grid(row=r_, column=1, sticky="we", padx=(S(8), 0),
                                                            ipady=S(2))
            self.entry(b, numv, width=10, justify="left").grid(row=r_, column=2, sticky="we", padx=(S(8), 0),
                                                              ipady=S(2))
            for v in (nv, numv):
                v.trace_add("write", lambda *a: self._accounts_changed())
            self.acc_vars.append((acc, nv, numv))
        b.columnconfigure(1, weight=1)

        c, b, _ = self.card(right, "Data")
        c.pack(fill="x", pady=(S(14), 0))
        self.saved_lbl = self.label(b, "", font="small")
        self.saved_lbl.pack(anchor="w")
        self.autowrap(self.note(b, self.store.path, fg=C["faint"])).pack(anchor="w", fill="x", pady=(S(4), 0))
        r = tk.Frame(b, bg=C["card"])
        r.pack(anchor="w", pady=(S(10), 0))
        self.button(r, "Open folder", self.open_folder).pack(side="left")
        self.button(r, "Export CSV", self.export_csv).pack(side="left", padx=(S(8), 0))
        r = tk.Frame(b, bg=C["card"])
        r.pack(fill="x", pady=(S(14), 0))
        self.label(r, "Fallback rate for estimates: 1 EUR =").pack(side="left")
        self.label(r, "USD", fg=C["faint"], font="small").pack(side="right", padx=(S(6), 0))
        self.fx_var = tk.StringVar(value=fmt_input(self.data["settings"]["fx"].get("USD", 1.12)))
        self.fx_entry = self.entry(r, self.fx_var, width=8)
        self.fx_entry.pack(side="right", ipady=S(2))
        self.fx_var.trace_add("write", lambda *a: self.later("fx", 400, self._fx_changed))
        self.fx_info = self.autowrap(self.note(b, fg=C["faint"]))
        self.fx_info.pack(anchor="w", fill="x", pady=(S(6), 0))
        self._show_saved_status()

    def _build_toast(self):
        S = self.S
        self.toast = tk.Frame(self.main, bg=C["card2"], highlightthickness=1, highlightbackground=C["border"])
        self.toast_lbl = tk.Label(self.toast, text="", bg=C["card2"], fg=C["text"], font=self.f["body"],
                                  padx=S(14), pady=S(9))
        self.toast_lbl.pack(side="left")
        self.toast_undo = tk.Label(self.toast, text="Undo", bg=C["card2"], fg=C["accent"], font=self.f["bodyb"],
                                   padx=S(12), cursor="hand2")
        self.toast_undo.bind("<Button-1>", lambda e: self.undo())

    # ---- theme -------------------------------------------------------------
    def _theme_changed(self):
        t = self.theme_var.get()
        if t == self.theme or t not in THEMES:
            return
        self.theme = t
        self.data["settings"]["theme"] = t
        self.data["notes"] = self.notes.get("1.0", "end-1c")
        self._save()
        C.clear()
        C.update(THEMES[t])
        set_titlebar_dark(self, t == "dark")
        self._build_all()
        self.refresh()

    def _capture_form(self):
        self.data["notes"] = self.notes.get("1.0", "end-1c")
        self._saved_form = ({k: v.get() for k, v in self.fv.items()}, self.kind.get(), self.editing)
        self._filter = self.filter_var.get()

    def _restore_form(self):
        if not self._saved_form:
            return
        vals, kind, editing = self._saved_form
        self._saved_form = None
        self.kind.set(kind)
        for k, v in vals.items():
            self.fv[k].set(v)
        self.editing = editing
        self._set_edit_mode(bool(editing))

    # ---- form --------------------------------------------------------------
    def new_entry(self):
        self.goto("log")
        self.cancel_edit()
        w = self.fwidget.get("amount")
        if w is not None:
            self.after(50, w.focus_set)

    def _show_fields(self):
        kind = self.kind.get()
        for parts in self.frow.values():
            for w, _ in parts:
                w.grid_remove()
        for r, key in enumerate(KIND_FIELDS[kind]):
            for w, opts in self.frow[key]:
                w.grid(row=r, **opts)
            self.flabel[key].configure(text=LABEL_OVERRIDE.get((kind, key), FIELD_DEF[key][0]))
        self.hint.configure(text=KIND_HINT.get(kind, ""))
        self._clear_marks()
        self._update_form_info()

    def _mark(self, key: str, bad: bool):
        w = self.fwidget.get(key)
        if w is None:
            return
        if isinstance(w, ttk.Combobox):
            w.configure(style="Bad.TCombobox" if bad else "TCombobox")
        elif bad:
            w.configure(highlightbackground=C["bad"], bg=C["bad_bg"], fg=C["bad_fg"])
        else:
            w.configure(highlightbackground=C["border"], bg=C["input"], fg=C["text"])

    def _clear_marks(self):
        for k in self.fwidget:
            self._mark(k, False)

    def _num(self, key: str) -> Optional[float]:
        try:
            return parse_num(self.fv[key].get())
        except ValueError:
            return None

    def _collect(self) -> Tuple[dict, List[str], str]:
        """Read the form. Returns the entry, the invalid fields and an error message."""
        kind = self.kind.get()
        fields = KIND_FIELDS[kind]
        e: dict = {"kind": kind}
        bad: List[str] = []
        msg = ""

        def num(key: str, required: bool = True, allow_zero: bool = False) -> Optional[float]:
            try:
                v = parse_num(self.fv[key].get())
            except ValueError:
                bad.append(key)
                return None
            if v is None:
                if required:
                    bad.append(key)
                return None
            if v < 0 or (v == 0 and required and not allow_zero):
                bad.append(key)
                return None
            return round(v, 6)

        try:
            e["date"] = parse_date(self.fv["date"].get()).isoformat()
        except ValueError:
            bad.append("date")
        if "account" in fields:
            e["account"] = self.fv["account"].get()
        if "to_account" in fields:
            e["to_account"] = self.fv["to_account"].get()
            if e["to_account"] == e["account"]:
                msg = "Pick different accounts for the transfer."
        if "security" in fields:
            e["security"] = self.fv["security"].get().strip()
            if not e["security"] and kind in ("buy", "sell", "transfer_in"):
                bad.append("security")
        if "loan" in fields:
            e["loan"] = self.fv["loan"].get().strip()
            if not e["loan"]:
                bad.append("loan")
        if kind in ("buy", "sell"):
            e["qty"] = num("qty")
            e["price"] = num("price")
            e["currency"] = self.fv["currency"].get() or "EUR"
            e["fees"] = num("fees", required=False) or 0.0
            e["amount"] = num("amount", required=False)   # None = estimated
        elif kind == "transfer_in":
            e["qty"] = num("qty")
            e["price"] = num("price", required=False) or 0.0
            e["currency"] = self.fv["currency"].get() or "EUR"
            amt = num("amount", required=False)
            if amt is None and e["qty"] and e["price"]:
                amt = round(e["qty"] * e["price"] * self.summary.rates.get(e["currency"], 1.0), 2)
            e["amount"] = amt or 0.0
        else:
            e["amount"] = num("amount", allow_zero=kind == "loan_pay")
        if "loan_part" in fields:
            e["loan_part"] = num("loan_part", required=False) or 0.0
            if e.get("amount") is not None and e["loan_part"] > e["amount"] + 1e-9:
                bad.append("loan_part")
                msg = "The student loan part cannot be larger than the amount."
        if "interest" in fields:
            e["interest"] = num("interest", required=False) or 0.0
        if kind == "loan_pay":
            e["fees"] = num("fees", required=False) or 0.0
            if not bad and not (e["amount"] or e["interest"] or e["fees"]):
                bad.append("amount")
        note = self.fv["note"].get().strip()
        if note:
            e["note"] = note
        if bad and not msg:
            msg = "Check the fields marked in red."
        return e, bad, msg

    def _update_form_info(self):
        if not hasattr(self, "form_info") or not self.form_info.winfo_exists():
            return
        kind, s, num, fv = self.kind.get(), self.summary, self._num, self.fv
        txt, col = "", C["muted"]
        a = num("amount")
        if kind in ("deposit", "withdrawal") and a:
            lp = num("loan_part") or 0.0
            if lp > a + 1e-9:
                txt, col = "The student loan part cannot be larger than the amount.", C["red"]
            else:
                txt = f"Student loan {fmt_eur(lp)}  ·  own money {fmt_eur(a - lp)}"
        elif kind in ("buy", "sell"):
            q, p, fees, ccy = num("qty"), num("price"), num("fees") or 0.0, fv["currency"].get() or "EUR"
            acc = fv["account"].get()
            if q and p:
                gross = q * p
                if a:
                    eur = a
                    fx = (a - fees) / gross if kind == "buy" else (a + fees) / gross
                    txt = f"Trade value {fmt_money(gross, ccy)}"
                    if ccy != "EUR" and fx > 0:
                        txt += f"  ·  1 EUR = {fmt_num(1 / fx, 4)} {ccy} (incl. currency fee)"
                else:
                    try:
                        day = parse_date(fv["date"].get()).isoformat()
                    except ValueError:
                        day = dt.date.today().isoformat()
                    rate, src = estimate_rate(ccy, day, s.rates, self._fx_mid,
                                              {"security": fv["security"].get().strip(), "price": p, "date": day})
                    src = src or s.rate_src.get(ccy, "fallback rate")
                    eur = gross * rate + (fees if kind == "buy" else -fees)
                    txt = f"Trade value {fmt_money(gross, ccy)} ≈ {fmt_eur(eur)}"
                    if ccy != "EUR":
                        txt += f" (1 EUR ≈ {fmt_num(1 / rate, 4)} {ccy}: {src})"
                    txt += ". Fill in the exact EUR amount once the trade shows under Nordnet's Transactions."
                cash = s.cash.get(acc, 0.0)
                old = next((x for x in self.data["entries"] if x["id"] == self.editing), None)
                if old and old["kind"] in ("buy", "sell") and old.get("account") == acc:
                    oe = s.amounts.get(old["id"], (0.0, False))[0]
                    cash += oe if old["kind"] == "buy" else -oe
                after = cash - eur if kind == "buy" else cash + eur
                txt += f"\nCash left on {acc}: {'≈ ' if not a or s.estimates else ''}{fmt_eur(after)}."
                if after < -0.005:
                    col = C["red"]
        elif kind == "transfer":
            if fv["account"].get() == fv["to_account"].get():
                txt, col = "Pick different accounts for the transfer.", C["red"]
            else:
                txt = f"Cash on {fv['account'].get()} now {fmt_eur(s.cash.get(fv['account'].get(), 0))}."
        elif kind == "transfer_in":
            q, p, ccy = num("qty"), num("price"), fv["currency"].get() or "EUR"
            if q and p and not a:
                txt = (f"Cost basis {fmt_money(q * p, ccy)} ≈ {fmt_eur(q * p * s.rates.get(ccy, 1.0))} "
                       f"(converted at today's rate – enter the EUR amount for the exact value).")
            elif not a:
                txt = "Without a cost basis the whole value counts as return."
        elif kind == "loan_pay":
            tot = (a or 0) + (num("interest") or 0) + (num("fees") or 0)
            if tot:
                txt = f"Paid to the bank in total {fmt_eur(tot)}."
        elif kind == "loan_draw" and a:
            txt = "Remember to mark the student loan part on the deposit you make to Nordnet."
        self.form_info.configure(text=txt, fg=col)

    def submit(self):
        e, bad, msg = self._collect()
        self._clear_marks()
        if bad or msg:
            for k in bad:
                self._mark(k, True)
            self.form_info.configure(text=msg, fg=C["red"])
            return
        if self.editing:
            for i, old in enumerate(self.data["entries"]):
                if old["id"] == self.editing:
                    e["id"] = old["id"]
                    self.data["entries"][i] = e
                    break
            msg = "Changes saved."
        else:
            e["id"] = new_id()
            self.data["entries"].append(e)
            msg = f"{KIND_TABLE[e['kind']]} added."
        if e.get("loan") and e["loan"] not in [ln["name"] for ln in self.data["loans"]]:
            self.data["loans"].append({"name": e["loan"]})
        self._save()
        self._reset_form()
        self.refresh()
        self._request_prices()
        self.flash(msg)

    def _reset_form(self):
        """Clear the amounts; date, account, type, currency and fees stay for the next entry."""
        for k in ("amount", "loan_part", "qty", "price", "interest", "note", "security"):
            self.fv[k].set("")
        self.editing = None
        self._set_edit_mode(False)
        self._clear_marks()
        if self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())

    def _set_edit_mode(self, on: bool):
        self.form_title.configure(text="Edit entry" if on else "New entry", fg=C["accent"] if on else C["text"])
        self.btn_submit.configure(text="Save changes" if on else "Add entry")
        self.btn_cancel.configure(text="Cancel" if on else "Clear")
        if on:
            self.btn_delete.pack(side="right")
        else:
            self.btn_delete.pack_forget()

    def cancel_edit(self):
        self._reset_form()
        self._update_form_info()

    def load_entry(self, eid: str):
        e = next((x for x in self.data["entries"] if x["id"] == eid), None)
        if e is None:
            return
        if self.page_var.get() != "log":
            self.goto("log")
        self.editing = eid
        self.kind.set(e["kind"])
        fv = self.fv
        fv["date"].set(fmt_date(e["date"]))
        for k in ("account", "to_account", "loan", "currency"):
            if e.get(k):
                fv[k].set(e[k])
        fv["security"].set(e.get("security", ""))
        fv["note"].set(e.get("note", ""))
        for k in ("qty", "price", "fees", "amount", "loan_part", "interest"):
            v = e.get(k)
            fv[k].set("" if v is None or (k in ("loan_part", "interest") and not v) else fmt_input(v))
        self._set_edit_mode(True)
        self._clear_marks()
        self.form_sf.canvas.yview_moveto(0)
        if self.tree.exists(eid) and self.tree.selection() != (eid,):
            self.tree.selection_set(eid)
            self.tree.see(eid)

    def delete_current(self):
        idx = next((i for i, x in enumerate(self.data["entries"]) if x["id"] == self.editing), None)
        if idx is None:
            return
        e = self.data["entries"].pop(idx)
        self._undo = (idx, e)
        self._save()
        self._reset_form()
        self.refresh()
        self.flash(f"{KIND_TABLE[e['kind']]} of {fmt_date(e['date'])} deleted.", undo=True)

    def undo(self):
        if not self._undo:
            return
        idx, e = self._undo
        self._undo = None
        self.data["entries"].insert(min(idx, len(self.data["entries"])), e)
        self._save()
        self.refresh()
        self.flash("Delete undone.")

    def _tree_select(self, _e=None):
        sel = self.tree.selection()
        if sel and sel[0] != self.editing:
            self.load_entry(sel[0])

    # ---- prices ------------------------------------------------------------
    def _request_prices(self):
        """Tell the price service what to fetch: current holdings, history for the chart and
        symbol searches for securities without a symbol."""
        s = self.summary
        first: Dict[str, dt.date] = {}
        for i, snap in enumerate(s.hold_days):
            for sec in snap:
                first.setdefault(sec, s.days[i])
        held = {h.security for h in s.holdings}
        syms, starts, searches, ccys = [], {}, [], {"USD"}
        secs = self.data.setdefault("securities", {})
        for sec in sorted(set(first) | held | set(s.sec_ccy)):
            sym = symbol_of(self.data, sec)
            ccy = s.sec_ccy.get(sec, "EUR")
            if sym:
                if sec in first:
                    starts[sym] = first[sec]
                    q = self.quotes.get(sym)
                    ccys.add(q.currency if q is not None and q.currency else ccy)
                if sec in held:
                    syms.append(sym)
            elif not secs.get(sec, {}).get("searched"):
                searches.append((sec, ccy))
        d0 = min(first.values()) if first else dt.date.today()
        for c in sorted(ccys - {"EUR", ""}):
            syms.append(f"EUR{c}=X")
            starts[f"EUR{c}=X"] = d0
        self.prices.request(syms, starts, searches)

    def _fx_mid(self, ccy: str, day: str, trade: Optional[dict] = None) -> Optional[float]:
        """EUR per unit of a currency at mid rate. For today's trades: the rate at the moment the
        trade happened (found from the stock's 5-minute bars), else the live rate; for earlier
        trades: that day's close."""
        sym = f"EUR{ccy}=X"
        q = self.quotes.get(sym)
        if q is not None and q.price and day >= session_date(q):
            t = self._trade_time(trade)
            pts = self.intraday.get("1d", {}).get(sym)
            if t and pts:
                return 1.0 / min(pts, key=lambda tp: abs(tp[0] - t))[1]
            return 1.0 / q.price
        best = None
        for d, close in self.hist.get(sym, []):
            if d > day:
                break
            best = close
        return 1.0 / best if best else None

    def _trade_time(self, trade: Optional[dict]) -> Optional[float]:
        """When a trade from today most likely happened: the first 5-minute bar of the stock whose
        low–high range contains the trade price (the middle of that bar)."""
        if not trade or not trade.get("price"):
            return None
        bars = self.intraday.get("1d_bars", {}).get(symbol_of(self.data, trade.get("security", "")))
        q = self.quotes.get(symbol_of(self.data, trade.get("security", "")))
        if not bars or q is None or session_date(q) != trade.get("date"):
            return None
        price = float(trade["price"])
        for t, lo, hi in bars:
            if lo - 1e-9 <= price <= hi + 1e-9:
                return t + 150
        return None

    def refresh_prices(self):
        if not self.prices.enabled:
            self.flash("Live prices are off (Settings).")
            return
        self._request_prices()
        self.prices.week_fetched = 0.0
        self.prices.refresh_now()
        self.flash("Refreshing prices…")

    def research_symbols(self):
        for sec in self.data.get("securities", {}).values():
            if not sec.get("symbol"):
                sec.pop("searched", None)
        with self.prices.lock:
            self.prices.found.clear()
        self._save()
        self._ticker_names = None
        self.refresh_prices()

    def _poll(self):
        """Once a second: take in new prices. A price tick only updates the visible page; the
        regular fetch (every 60 s) also refreshes history, intraday prices and symbols."""
        try:
            p = self.prices
            if p.rest_version != self._seen_rest:
                self._seen_rest, self._seen_version = p.rest_version, p.version
                quotes, hist, found, intraday = p.snapshot()
                changed = False
                secs = self.data.setdefault("securities", {})
                for name, res in found.items():
                    rec = secs.setdefault(name, {})
                    if rec.get("symbol") or rec.get("searched"):
                        continue
                    rec["searched"] = True
                    if res:
                        rec.update(symbol=res[0], exchange=res[1], auto=True)
                    changed = True
                self.quotes, self.hist, self.intraday = quotes, hist, intraday
                if changed:
                    self._save()
                    self._ticker_names = None
                    self._request_prices()
                    p.refresh_now()
                if self.summary.estimates:  # estimated EUR amounts follow the live exchange rate
                    self.summary = ledger(self.data, fx_mid=self._fx_mid)
                    self.refresh_tree()
                    self._update_form_info()
                self._revalue(full=True)
            elif p.version != self._seen_version:
                self._seen_version = p.version
                with p.lock:
                    self.quotes = dict(p.quotes)
                self._revalue(full=False)
            self._update_live_status()
        except tk.TclError:
            return
        self.after(1000, self._poll)

    def _update_live_status(self):
        p = self.prices
        now = time.time()
        as_of = self.val.as_of
        closed = bool(as_of) and now - as_of > 15 * 60
        if not p.enabled:
            col, txt = C["faint"], "Live prices off"
        elif p.streaming and not closed:
            t = dt.datetime.fromtimestamp(p.stream.last_tick)
            col, txt = C["accent"], f"Live · {t.hour}:{t.minute:02d}:{t.second:02d}"
        elif p.last_ok and now - p.last_ok < max(180, 3 * p.interval):
            col = C["accent"] if not closed else C["faint"]
            txt = f"Market closed · prices from {short_time(as_of)}" if closed else f"Live (60 s) · {short_time(p.last_ok)}"
        elif p.busy or not p.last_try:
            col, txt = C["info"], "Fetching prices…"
        elif p.last_ok:
            col, txt = C["info"], f"Offline · prices from {short_time(p.last_ok)}"
        else:
            col, txt = C["red"], "No connection to the price service"
        if self.live_lbl.cget("text") != txt:
            self.live_lbl.configure(text=txt)
            self.live_dot.delete("all")
            r, c = self.S(4), self.S(5)
            self.live_dot.create_oval(c - r, c - r, c + r, c + r, fill=col, outline="")
        if not self.price_status.winfo_ismapped():
            return
        st = p.stream
        if not p.enabled:
            stream = "Off."
        elif p.streaming:
            stream = "Real-time stream connected – prices update every second."
        elif st.connected:
            stream = "Real-time stream connected, no new trades right now (market closed?)."
        else:
            stream = ("Real-time stream unavailable – prices update every 60 s."
                      + (f" ({st.error})" if st.error else ""))
        extra = f"\nError: {p.error}" if p.error else ""
        self.price_status.configure(text=f"{txt}.\n{stream}{extra}")

    def _live_changed(self):
        on = self.live_var.get() == "on"
        self.prices.enabled = on
        self.data["settings"]["live"] = on
        self._save()
        if on:
            self.refresh_prices()
        self._update_live_status()

    def open_tradingview(self, security: str):
        sym = symbol_of(self.data, security)
        if not sym:
            self.flash(f"{security}: no ticker symbol – add it under Settings.", bad=True)
            return
        q = self.quotes.get(sym)
        exch = (q.exchange if q is not None else "") or self.data["securities"].get(security, {}).get("exchange", "")
        webbrowser.open(tradingview_url(sym, exch))
        self.flash(f"Opening TradingView: {sym}")

    # ---- settings and saving -----------------------------------------------
    def _accounts_changed(self):
        for acc, nv, numv in self.acc_vars:
            acc["name"], acc["number"] = nv.get().strip(), numv.get().strip()
        self.later("save", 700, self._save)

    def _toggle_excluded(self, name: str):
        """Hide a security from the portfolio completely (value, allocation, movers, tables, charts,
        transactions) or bring it back."""
        rec = self.data.setdefault("securities", {}).setdefault(name, {})
        rec["excluded"] = not rec.get("excluded")
        self._save()
        self._ticker_names = None
        self.refresh()
        self._request_prices()
        self.flash(f"{name} {'hidden from' if rec['excluded'] else 'shown in'} the portfolio.")

    def _symbol_changed(self, name: str, var: tk.StringVar):
        sym = var.get().strip().upper()
        rec = self.data.setdefault("securities", {}).setdefault(name, {})
        if sym == (rec.get("symbol") or ""):
            return
        rec.update(symbol=sym, auto=False, searched=True)
        rec.pop("exchange", None)
        self._save()
        self._revalue()
        self._request_prices()
        if self.prices.enabled:
            self.prices.refresh_now()

    def _fx_changed(self):
        try:
            v = parse_num(self.fx_var.get())
            ok = v is not None and v > 0
        except ValueError:
            ok = False
        self.fx_entry.configure(highlightbackground=C["border"] if ok else C["bad"])
        if ok:
            self.data["settings"]["fx"]["USD"] = v
            self._save()
            self.refresh()

    def _save_notes(self):
        if self.notes.winfo_exists():
            self.data["notes"] = self.notes.get("1.0", "end-1c")
            self._save()

    def _save(self):
        if self.store.save():
            self._show_saved_status()
        else:
            self.flash(self.store.error, bad=True, sticky=True)

    def _show_saved_status(self):
        if not hasattr(self, "saved_lbl") or not self.saved_lbl.winfo_exists():
            return
        if self.store.exists and os.path.exists(self.store.path):
            self.saved_lbl.configure(text=f"Saved automatically · {short_time(os.path.getmtime(self.store.path))}")
        else:
            self.saved_lbl.configure(text="Nothing saved yet")

    def flash(self, msg: str, bad: bool = False, undo: bool = False, sticky: bool = False):
        S = self.S
        self.toast_lbl.configure(text=msg, fg=C["red"] if bad else C["text"])
        if undo:
            self.toast_undo.pack(side="left")
        else:
            self.toast_undo.pack_forget()
        self.toast.place(relx=1.0, rely=1.0, x=-S(22), y=-S(22), anchor="se")
        self.toast.lift()
        if sticky:
            job = self._jobs.pop("flash", None)
            if job:
                self.after_cancel(job)
        else:
            self.later("flash", 8000 if undo else 3500, self.toast.place_forget)

    def open_folder(self):
        folder = self.store.folder
        os.makedirs(folder, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except OSError as ex:
            self.flash(f"Could not open the folder: {ex}", bad=True)

    def export_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Export entries", defaultextension=".csv",
            initialfile=f"portfolio-{dt.date.today().isoformat()}.csv",
            filetypes=[("CSV (Excel)", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        s = self.summary

        def n(x, dec=2):
            return "" if x is None else fmt_input(round(x, dec) if dec is not None else x)

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["Date", "Type", "Account", "To account", "Loan", "Security", "Ticker", "Quantity",
                            "Price", "Currency", "Fees EUR", "Amount EUR", "Estimate", "Student loan part",
                            "Interest", "Note"])
                for e in sorted_entries(self.data):
                    amt, est = e.get("amount"), False
                    if e["kind"] in ("buy", "sell"):
                        amt, est = s.amounts.get(e["id"], (amt, False))
                    w.writerow([e["date"], KIND_TABLE[e["kind"]], e.get("account", ""),
                                e.get("to_account", ""), e.get("loan", ""), e.get("security", ""),
                                symbol_of(self.data, e.get("security", "")) if e.get("security") else "",
                                n(e.get("qty"), None), n(e.get("price"), None), e.get("currency", ""),
                                n(e.get("fees")), n(amt), "yes" if est else "", n(e.get("loan_part")),
                                n(e.get("interest")), e.get("note", "")])
            self.flash(f"Exported: {os.path.basename(path)}")
        except OSError as ex:
            self.flash(f"Export failed: {ex}", bad=True)

    def _close(self):
        """Finish pending saves before closing."""
        pending = False
        for name in ("notes", "save", "fx"):
            job = self._jobs.pop(name, None)
            if job:
                self.after_cancel(job)
                pending = True
                if name == "fx":
                    self._fx_changed()
        for name in [k for k in self._jobs if k.startswith("sym:")]:
            self.after_cancel(self._jobs.pop(name))
        text = self.notes.get("1.0", "end-1c")
        if text != self.data.get("notes", ""):
            self.data["notes"] = text
            pending = True
        if pending:
            self.store.save()
        self.prices.stop()
        self.destroy()

    # ---- refresh -----------------------------------------------------------
    def refresh(self):
        """Recalculate everything from the entries (after adding, editing or deleting)."""
        self.summary = s = ledger(self.data, fx_mid=self._fx_mid)
        self.refresh_tree()
        secs = sorted({e.get("security", "") for e in self.data["entries"] if e.get("security")})
        self.fcombo["security"].configure(values=secs)
        self.fcombo["loan"].configure(values=[ln["name"] for ln in self.data["loans"]])
        usd = s.rate_src.get("USD", "fallback rate")
        self.fx_info.configure(
            text=f"Trade estimates use 1 EUR = {fmt_num(1 / s.rates.get('USD', 1), 4)} USD "
                 f"({'latest trade: ' + usd if usd != 'fallback rate' else 'fallback rate'}).")
        self._update_form_info()
        self._revalue()

    def _page_refreshers(self):
        return {"dash": self._refresh_dash, "port": self._refresh_port, "loans": self._refresh_loans,
                "settings": self._refresh_tickers}

    def _revalue(self, full: bool = True):
        """Update the price-dependent views. full=False (a price tick, every second) recalculates
        only the values and refreshes just the visible page; hidden pages catch up when shown."""
        s = self.summary
        self.val = valuate(s, self.data, self.quotes)
        if full or len(self.vhist) != len(s.days):
            self.vhist = value_history(s, self.data, self.hist, self.quotes)
        if self.vhist:
            self.vhist[-1] = self.val.total
        refreshers = self._page_refreshers()
        self._ticking = not full      # tables keep their row order between price ticks
        if full:
            self._stale.clear()
            for fn in refreshers.values():
                fn()
            return
        page = self.page_var.get()
        if page in refreshers:
            refreshers[page]()
        self._stale |= set(refreshers) - {page}

    # ---- Overview ----------------------------------------------------------
    def _groups(self) -> Dict[str, dict]:
        """Holdings per security (the same security on several accounts is combined)."""
        groups: Dict[str, dict] = {}
        for p in self.val.positions:
            g = groups.setdefault(p.holding.security, {"value": 0.0, "usd": 0.0, "day_usd": 0.0, "cost_usd": 0.0,
                                                       "qty": 0.0, "p": p, "missing": False})
            g["value"] += p.value
            g["usd"] += p.value_usd
            g["day_usd"] += p.day_usd
            g["cost_usd"] += p.cost_usd
            g["qty"] += p.holding.qty
            g["missing"] |= p.holding.missing_cost
        return groups

    def _refresh_dash(self):
        S, s, v = self.S, self.summary, self.val
        hide = self.hidden
        eur = (lambda *a, **k: MASK_EUR) if hide else fmt_eur
        usd = (lambda *a, **k: MASK_USD) if hide else fmt_usd
        px = (lambda *a, **k: MASK) if hide else self._px
        qty = (lambda *a, **k: MASK) if hide else fmt_qty
        self.chart.hide = hide
        approx = "≈ " if v.missing and v.positions else ""
        self.hero_val.configure(text=approx + eur(v.total))
        self.hero_usd.configure(text=f"{usd(v.total_usd)}  ·  1 EUR = {fmt_num(v.usd_per_eur, 4)} USD"
                                if v.usd_per_eur else "")
        if any(p.live for p in v.positions):
            self.hero_day.configure(text=f"{usd(v.day_usd, sign=True)} ({fmt_pct(v.day_pct, 2, sign=True)})",
                                    fg=change_color(v.day_usd))
        else:
            self.hero_day.configure(text="–", fg=C["muted"])
        net = s.net_in
        gain = v.total - net
        self.hero_gain.configure(
            text=f"Total return {eur(gain, sign=True)}"
                 f"{' (' + fmt_pct(gain / net, 1, sign=True) + ')' if net > 0.005 else ''}  ·  "
                 f"contributed {eur(net, 0)}", fg=change_color(gain))
        note = f"Holdings {eur(v.holdings_value, 0)} · cash {'≈ ' if s.estimates else ''}{eur(v.cash)}"
        if v.missing:
            note += f" · {v.missing} without a price (valued at cost)"
        self.hero_note.configure(text=note)

        for key, val in (("loan", s.loan_in), ("own", s.own_in), ("gift", s.gift_in), ("ext", s.ext_in)):
            self.fund_vals[key].configure(text=eur(val, 0))
        for key, val in (("gift", s.gift_in), ("ext", s.ext_in)):
            if val > 0.005:
                self.fund_rows[key].pack(fill="x", pady=self.S(2), before=self.fund_bar)
            else:
                self.fund_rows[key].pack_forget()
        self.fund_bar.set([(s.loan_in, C["loan"]), (s.own_in, C["own"]), (s.gift_in, C["gift"]),
                           (s.ext_in, C["invest"])])
        self.fund_note.configure(text=f"{fmt_pct(s.loan_in / net)} loan · {fmt_pct(1 - s.loan_in / net)} "
                                      f"own" if net > 0.005 else "No deposits yet")
        self.debt_val.configure(text=eur(s.loan_balance, 0))
        self.worth_val.configure(text=eur(v.total - s.loan_balance, 0))

        groups = self._groups()
        items, total = [], v.holdings_usd
        for sec, g in groups.items():
            p, pct = g["p"], g["p"].day_pct
            ret = g["usd"] - g["cost_usd"]
            tip = [("Value", usd(g["usd"]), None, None),
                   ("Share of portfolio", fmt_pct(g["usd"] / total, 1) if total > 0 else "–", None, None),
                   ("Today", f"{fmt_pct(pct, 2, sign=True)} · {usd(g['day_usd'], sign=True)}" if pct is not None
                    else "no price", None, change_color(pct)),
                   ("Return", "cost basis missing" if g["missing"] else
                    usd(ret, sign=True) + (f" · {fmt_pct(ret / g['cost_usd'], 1, sign=True)}"
                                               if g["cost_usd"] > 0.005 else ""), None,
                    None if g["missing"] else change_color(ret)),
                   ("Shares × price", f"{qty(g['qty'])} × {px(p.price, p.currency)}" if p.live
                    else qty(g["qty"]), None, None),
                   ("Avg price (incl. fees)", px(g["cost_usd"] / g["qty"], "USD") if g["qty"] else "–",
                    None, None)]
            sub = [usd(g["usd"])]
            if p.live and not hide:
                sub.append(f"{fmt_qty(g['qty'])} × {px(p.price, p.currency)}")
            items.append(dict(key=sec, label=self._short(sec, p.symbol, 16), value=g["usd"], sub=sub,
                              pct=pct, tip=tip, title=sec + (f" · {p.symbol}" if p.symbol else "")))
        self.treemap.set_data(items)

        movers = sorted(groups.items(), key=lambda kv: -(kv[1]["p"].day_pct if kv[1]["p"].day_pct is not None
                                                          else -9.0))[:10]
        if movers:
            self.movers_empty.pack_forget()
        elif not self.movers_empty.winfo_ismapped():
            self.movers_empty.pack(anchor="w")
        for i, (row, t, n, pill) in enumerate(self.mover_rows):
            if i >= len(movers):
                self.mover_keys[i] = None
                row.pack_forget()
                continue
            sec, g = movers[i]
            p, pct = g["p"], g["p"].day_pct
            self.mover_keys[i] = sec
            t.configure(text=self._short(sec, p.symbol, 7))
            n.configure(text=sec if len(sec) <= 16 else sec[:15] + "…")
            n.px.configure(text=px(p.price, p.currency) if p.live else "")
            bg = (blend(C["up"] if pct >= 0 else C["down"], C["card"], 0.16) if pct is not None else C["card2"])
            pill.configure(text=fmt_pct(pct, 2, sign=True) if pct is not None else "–", bg=bg, fg=change_color(pct))
            if not row.winfo_ismapped():
                row.pack(fill="x", pady=(0, S(3)))
        self._update_chart()

    def _short(self, sec: str, symbol: str, n: int) -> str:
        """Short tile name: the ticker, or a label for long fund/crypto symbols (0P0001CKSS.F, XRP-USD)."""
        label = self.data.get("securities", {}).get(sec, {}).get("label")
        return (label or symbol or sec)[:n].strip()

    @staticmethod
    def _px(price: Optional[float], ccy: str) -> str:
        """Share price with cents ($161.09); prices below one keep four decimals ($0.3373)."""
        if price is None:
            return "–"
        return fmt_money(price, ccy or "USD", 2 if abs(price) >= 1 else 4)

    def _set_legend(self, entries):
        S = self.S
        self._clear(self.legend)
        for name, col, sym in entries:
            self.label(self.legend, sym, fg=col, font="small").pack(side="left", padx=(S(12), S(4)))
            self.label(self.legend, name, font="small").pack(side="left")

    def _update_chart(self):
        s = self.summary
        key = self.range_key
        if key in ("1d", "1w"):
            empty = ("Waiting for intraday prices…" if self.prices.enabled
                     else "Live prices are off – no intraday data.")
            total, contrib = self.val.total, None
            if key == "1d":
                # value = start of the day + today's gain, so the end matches "today" on the Overview
                times, pnl = intraday_pnl(s, self.data, self.intraday.get("1d", {}), self.quotes)
                base = total - self.val.day
                vals = [base + x for x in pnl]
            else:
                times, vals, base, contrib = intraday_values(s, self.data, self.intraday.get("5d", {}), self.quotes)
            if vals:  # the latest streamed price as the last point
                now = self.val.as_of or time.time()
                if now > times[-1] + 30:
                    times, vals = times + [now], vals + [total]
                    if contrib:
                        contrib = contrib + [contrib[-1]]
                else:
                    vals[-1] = total
            self.chart.set_intraday(times, vals, base, key == "1w", empty, contrib)
            col = C["up"] if self.chart.change()[0] >= 0 else C["down"]
            self._set_legend([("Portfolio value (EUR)", col, "━"),
                              ("Start of day", C["faint"], "╌╌") if key == "1d"
                              else ("Contributed", C["muted"], "╌╌")])
            return
        n = len(s.days)
        self._set_legend([("Portfolio value", C["value"], "━"), ("Student loan", C["loan"], "■"),
                          ("Own money", C["own"], "■")] +
                         ([("Gifted shares", C["gift"], "■")] if s.gift_in > 0.005 else []))
        if n < 2:
            self.chart.set_data([], [], [], [], [])
            return
        span = next((d for k, _t, d in RANGES if k == key), None)
        i0 = min(max(0, n - span) if span else 0, n - 2)
        ser = s.series
        self.chart.set_data(s.days[i0:], ser["own"][i0:], ser["gift"][i0:], ser["loan"][i0:], self.vhist[i0:])

    # ---- Portfolio ---------------------------------------------------------
    def _table(self, box, name: str, sig, heads, rows, anchors=None, total=None, striped=False, link=None):
        """Grid table that is rebuilt only when sig changes; otherwise texts and colours are updated
        in place, so tables do not flicker when prices tick every second.
        heads: [(text, anchor)] or None; rows: [[(text, fg, font)]]; total: [(column, text, fg)] in bold
        under a line; link: (text, callback(row index)) as an extra last column."""
        S = self.S
        anchors = anchors or [a for _h, a in heads]
        t = self._tables.get(name)
        if t is None or t["sig"] != sig:
            self._clear(box)
            off = 0
            if heads:
                off = 1
                for c, (h, a) in enumerate(heads):
                    self.label(box, h, font="h", anchor=a).grid(row=0, column=c, sticky="we", ipadx=S(5),
                                                                pady=(0, S(6)))
            cells = []
            for r, row in enumerate(rows):
                bg = C["card2"] if striped and r % 2 == 0 else C["card"]
                line = []
                for c, (txt, fg, font) in enumerate(row):
                    lbl = tk.Label(box, text=txt, bg=bg, fg=fg, font=self.f[font], anchor=anchors[c])
                    lbl.grid(row=r + off, column=c, sticky="nsew", ipadx=S(5), ipady=S(4 if striped else 3))
                    line.append(lbl)
                if link:
                    lk = tk.Label(box, text=link[0], bg=bg, fg=C["accent"], font=self.f["small"], cursor="hand2",
                                  anchor="e")
                    lk.grid(row=r + off, column=len(row), sticky="nsew", ipadx=S(5))
                    lk.bind("<Button-1>", lambda e, i=r: link[1](i))
                cells.append(line)
            tot = []
            if total is not None:
                r = len(rows) + off
                tk.Frame(box, bg=C["border"], height=1).grid(row=r, column=0, columnspan=len(anchors) + bool(link),
                                                             sticky="we", pady=S(3))
                for c, txt, fg in total:
                    lbl = self.label(box, txt, fg=fg, font="bodyb", anchor=anchors[c])
                    lbl.grid(row=r + 1, column=c, sticky="we", ipadx=S(5))
                    tot.append(lbl)
            self._tables[name] = {"sig": sig, "cells": cells, "total": tot}
            return
        for line, row in zip(t["cells"], rows):
            for lbl, (txt, fg, _font) in zip(line, row):
                if lbl.cget("text") != txt or lbl.cget("fg") != fg:
                    lbl.configure(text=txt, fg=fg)
        for lbl, (_c, txt, fg) in zip(t["total"], total or []):
            if lbl.cget("text") != txt or lbl.cget("fg") != fg:
                lbl.configure(text=txt, fg=fg)

    def _refresh_port(self):
        s, v = self.summary, self.val
        heads = [("Security", "w"), ("Ticker", "w"), ("Account", "w"), ("Qty", "e"), ("Avg price", "e"),
                 ("Price", "e"), ("Today", "e"), ("Value", "e"), ("Cost", "e"), ("Return", "e"), ("Weight", "e"),
                 ("", "e")]
        if not v.positions:
            if self._tables.get("hold", "x") is not None:
                self._clear(self.hold_box)
                self.note(self.hold_box, "No holdings yet. Add a buy under Transactions.").grid(row=0, column=0,
                                                                                                sticky="w")
                self._tables["hold"] = None
        else:
            # between price ticks keep the row order (no jumping rows); re-sort by value on the
            # regular refresh every 60 s
            keys = [(p.holding.account, p.holding.security) for p in v.positions]
            prev = self._tables.get("hold")
            keep = getattr(self, "_ticking", False) and prev and set(prev["sig"]) == set(keys)
            order = prev["sig"] if keep else tuple(keys)
            pos = {(p.holding.account, p.holding.security): p for p in v.positions}
            rows = []
            for k in order:
                p = pos[k]
                h = p.holding
                if h.missing_cost:
                    ret_txt, ret_col = "cost basis missing", C["faint"]
                else:
                    ret_txt = fmt_usd(p.ret_usd, sign=True) + (f" ({fmt_pct(p.ret_usd_pct, 1, sign=True)})"
                                                               if p.ret_usd_pct is not None else "")
                    ret_col = change_color(p.ret_usd)
                ccy = p.currency or h.currency
                rows.append([
                    (h.security, C["text"], "body"),
                    (p.symbol or "–", C["muted"], "small"),
                    (h.account, C["muted"], "small"),
                    (fmt_qty(h.qty), C["text"], "body"),
                    (self._px(h.avg_price, h.currency) if h.avg_price else "–", C["muted"], "body"),
                    (fmt_money(p.price, ccy, 2 if p.price >= 1 else 4) if p.live else "–", C["text"], "body"),
                    (fmt_pct(p.day_pct, 2, sign=True) if p.day_pct is not None else "–", change_color(p.day_pct),
                     "body"),
                    (("" if p.live else "≈ ") + fmt_usd(p.value_usd), C["text"], "bodyb"),
                    (fmt_usd(p.cost_usd), C["text"], "body"),
                    (ret_txt, ret_col, "body"),
                    (fmt_pct(p.weight, 1), C["muted"], "body"),
                ])
            gain = sum(p.ret_usd for p in v.positions if not p.holding.missing_cost)
            live = any(p.live for p in v.positions)
            total = [(0, "Total", C["text"]),
                     (6, fmt_usd(v.day_usd, sign=True) if live else "", change_color(v.day_usd)),
                     (7, fmt_usd(v.holdings_usd), C["text"]), (8, fmt_usd(v.cost_usd), C["text"]),
                     (9, fmt_usd(gain, sign=True), change_color(gain))]
            secs = [k[1] for k in order]
            self._table(self.hold_box, "hold", order, heads, rows, total=total, striped=True,
                        link=("TradingView ↗", lambda i, secs=secs: self.open_tradingview(secs[i])))
        self.hold_box.columnconfigure(0, weight=3)
        notes = ["Avg price and cost include trading fees, converted at each trade's own exchange rate – the same "
                 "as Nordnet's average price. Return = live value − cost in dollars; the euro return including "
                 "currency moves is on the Overview. Today = change since yesterday's close (or since the purchase "
                 "for shares bought today) minus today's trading fees."]
        if s.estimates:
            notes.append(f"≈ {s.estimates} trades still have an estimated EUR amount – fill it in once they show "
                         "under Nordnet's Transactions (Transactions → click the row).")
        if any(p.holding.missing_cost for p in v.positions):
            notes.append("“Cost basis missing”: add the cost basis to the shares-in entry so the return is right.")
        if v.missing:
            notes.append(f"{v.missing} securities have no price – valued at cost. Check the ticker under Settings.")
        txt = "\n".join(notes)
        if self.hold_note.cget("text") != txt:
            self.hold_note.configure(text=txt)

        rows = []
        est_acc = {e.get("account") for e in self.data["entries"] if s.amounts.get(e["id"], (0, False))[1]}
        accounts = list(self.data["accounts"])
        for x in self.data.get("external", []):
            if x.get("platform") not in [a["id"] for a in accounts]:
                accounts.append({"id": x.get("platform", "Other"), "name": "outside Nordnet"})
        for acc in accounts:
            a = acc["id"]
            val = sum(p.value for p in v.positions if p.holding.account == a) + s.cash.get(a, 0.0)
            net = s.acc_net.get(a, 0.0)
            cash = s.cash.get(a, 0.0)
            est = a in est_acc   # cash depends on estimated EUR amounts: a few euros either way
            cash_col = C["muted"] if est and abs(cash) < 25 else (C["red"] if cash < -0.005 else C["text"])
            rows.append([(f"{a} · {acc.get('name', '')}", C["text"], "body"),
                         (("≈ " if est else "") + fmt_eur(val), C["text"], "body"),
                         (("≈ " if est else "") + fmt_eur(cash), cash_col, "body"),
                         (fmt_eur(net), C["text"], "body"),
                         (fmt_eur(val - net, sign=True), change_color(val - net), "body")])
        self._table(self.acc_box, "acc", tuple((a["id"], a.get("name", "")) for a in accounts),
                    [("Account", "w"), ("Value", "e"), ("Cash", "e"), ("Contributed", "e"), ("Return", "e")], rows)
        self.acc_box.columnconfigure(0, weight=1)

        stats = [("Dividends (net)", s.dividends), ("Realized gains and losses", s.realized),
                 ("Trading fees", -s.trade_fees), ("Other fees, interest and taxes", -s.other_fees)]
        rows = [[(name, C["muted"], "body"),
                 (fmt_eur(val, sign=True), change_color(val) if r < 2 else C["text"], "body")]
                for r, (name, val) in enumerate(stats)]
        self._table(self.stats_box, "stats", "stats", None, rows, anchors=["w", "e"])
        self.stats_box.columnconfigure(0, weight=1)

    # ---- Loans -------------------------------------------------------------
    def _legend(self, frame: tk.Frame, rows: List[Tuple[str, float, str]], total: float):
        self._clear(frame)
        S = self.S
        for r, (name, val, col) in enumerate(rows):
            self.dot(frame, col).grid(row=r, column=0, padx=(0, S(8)))
            self.label(frame, name, fg=C["text"]).grid(row=r, column=1, sticky="w", pady=S(2))
            self.label(frame, fmt_eur(val), fg=C["text"]).grid(row=r, column=2, sticky="e")
            self.label(frame, fmt_pct(val / total) if total > 0.005 else "", width=6, anchor="e").grid(
                row=r, column=3, sticky="e")
        frame.columnconfigure(1, weight=1)

    def _refresh_loans(self):
        S, s, v = self.S, self.summary, self.val
        self.src_bar.set([(s.loan_in, C["loan"]), (s.own_in, C["own"]), (s.gift_in, C["gift"])])
        rows = [("Student loan", s.loan_in, "loan"), ("Own money", s.own_in, "own")]
        if s.gift_in > 0.005:
            rows.append(("Gifted shares", s.gift_in, "gift"))
        self._legend(self.src_leg, rows, sum(max(0.0, x) for _n, x, _c in rows))
        debt = s.loan_balance
        left = v.total - debt
        own = s.own_in + s.gift_in
        txt = (f"Portfolio value {fmt_eur(v.total, 0)} − loan {fmt_eur(debt, 0)} = {fmt_eur(left, 0)} would be "
               f"left for you if you paid the loan off now.")
        if own > 0.005:
            txt += (f"\nYour own money and gifts invested {fmt_eur(own, 0)} → now {fmt_eur(left, 0)} "
                    f"({fmt_pct(left / own - 1, 1, sign=True)}).")
        self.src_insight.configure(text=txt)
        stocks = s.cost_total - s.holding_fees
        fees = s.trade_fees + s.other_fees
        cash = max(0.0, s.cash_total)
        self.use_bar.set([(stocks, C["invest"]), (fees, C["debt"]), (cash, C["cash"])])
        self._legend(self.use_leg, [("Securities", stocks, "invest"), ("Fees and taxes", fees, "debt"),
                                    ("Cash", cash, "cash")], max(0.0, stocks) + fees + cash)
        self.use_note.configure(text=f"≈ {s.estimates} trades have an estimated EUR amount." if s.estimates else "")

        self._clear(self.loan_cards)
        loans = [ln for ln in s.loans.values() if ln.active]
        for k in range(2):
            self.loan_cards.columnconfigure(k, weight=1, uniform="lc")
        if not loans:
            c, b, _ = self.card(self.loan_cards, "Loans")
            c.grid(row=0, column=0, columnspan=2, sticky="nsew")
            self.note(b, "No loan entries. Add a drawdown: Transactions → Loan → Drawdown.").pack(anchor="w")
        for k, ln in enumerate(loans):
            c, b, _ = self.card(self.loan_cards, ln.name)
            c.grid(row=k // 2, column=k % 2, sticky="nsew", padx=(S(14) if k % 2 else 0, 0),
                   pady=(S(14) if k >= 2 else 0, 0))
            self.label(b, "Balance", font="small").grid(row=0, column=0, sticky="w")
            self.label(b, fmt_eur(ln.balance), fg=C["text"], font="kpi").grid(row=1, column=0, columnspan=2,
                                                                              sticky="w", pady=(0, S(8)))
            rows = [("Drawn", ln.drawn), ("Capitalized interest", ln.capitalized), ("Repaid", -ln.principal),
                    ("Kela credit", -ln.credit), ("Interest and fees paid", ln.interest + ln.fees)]
            for r, (name, val) in enumerate(rows, start=2):
                self.label(b, name).grid(row=r, column=0, sticky="w", pady=S(2))
                self.label(b, fmt_eur(val), fg=C["text"]).grid(row=r, column=1, sticky="e", padx=(S(30), 0))
            if ln.drawn > 0.005:
                self.note(b, f"{fmt_eur(s.loan_in)} deposited to Nordnet, {fmt_pct(s.loan_in / ln.drawn)} of the "
                             f"amount drawn.").grid(row=8, column=0, columnspan=2, sticky="w", pady=(S(8), 0))
            b.columnconfigure(0, weight=1)

        box = self.loan_list
        self._clear(box)
        ents = [e for e in sorted_entries(self.data) if e["kind"] in LOAN_KIND_KEYS or
                (e["kind"] in ("deposit", "withdrawal") and e.get("loan_part"))]
        if not ents:
            self.note(box, "No entries.").pack(anchor="w")
        for r, e in enumerate(reversed(ents)):
            k = e["kind"]
            if k in LOAN_KIND_KEYS:
                what, amt = f"{KIND_TABLE[k]} · {e.get('loan', '')}", float(e.get("amount") or 0)
                if k == "loan_pay":
                    amt += float(e.get("interest") or 0) + float(e.get("fees") or 0)
            else:
                what = f"{KIND_TABLE[k]} to Nordnet ({e.get('account', '')}), student loan part"
                amt = float(e["loan_part"])
            vals = [fmt_date(e["date"]), what, fmt_eur(amt), e.get("note", "")]
            for c, txt in enumerate(vals):
                lbl = self.label(box, txt, fg=C["text"] if c < 3 else C["faint"], font="body" if c < 3 else "small",
                                 anchor="e" if c == 2 else "w", cursor="hand2")
                lbl.grid(row=r, column=c, sticky="we", ipadx=S(8), pady=S(2))
                lbl.bind("<Button-1>", lambda ev, i=e["id"]: self.load_entry(i))
        box.columnconfigure(3, weight=1)

    # ---- Settings: ticker symbols ------------------------------------------
    def _refresh_tickers(self):
        S = self.S
        hidden = excluded_securities(self.data)
        names = tuple(sorted({e["security"] for e in self.data["entries"] if e.get("security")}
                             | {x["name"] for x in self.data.get("external", [])}))
        box = self.ticker_box
        held = {h.security for h in self.summary.holdings}
        if (names, tuple(sorted(hidden))) != self._ticker_names:
            self._ticker_names = (names, tuple(sorted(hidden)))
            self._clear(box)
            self.ticker_rows = {}
            for c, h in enumerate(["Security", "Ticker", "Price", "", "Portfolio"]):
                self.label(box, h, font="h").grid(row=0, column=c, sticky="w", padx=(0, S(12)), pady=(0, S(6)))
            for r, name in enumerate(names, start=1):
                self.label(box, name, fg=C["text"] if name in held else C["faint"]).grid(
                    row=r, column=0, sticky="w", pady=S(2))
                var = tk.StringVar(value=symbol_of(self.data, name))
                e = self.entry(box, var, width=11, justify="left")
                e.grid(row=r, column=1, sticky="w", padx=(0, S(12)), ipady=S(2))
                var.trace_add("write", lambda *a, n=name, v=var: self.later("sym:" + n, 900,
                                                                              lambda: self._symbol_changed(n, v)))
                pl = self.label(box, "", font="small")
                pl.grid(row=r, column=2, sticky="w", padx=(0, S(12)))
                self.link(box, "TradingView ↗", lambda n=name: self.open_tradingview(n)).grid(
                    row=r, column=3, sticky="w", padx=(0, S(12)))
                self.button(box, "Show" if name in hidden else "Hide",
                            lambda n=name: self._toggle_excluded(n)).grid(row=r, column=4, sticky="w", pady=S(1))
                self.ticker_rows[name] = pl
            box.columnconfigure(0, weight=1)
        for name, pl in getattr(self, "ticker_rows", {}).items():
            sym = symbol_of(self.data, name)
            q = self.quotes.get(sym) if sym else None
            rec = self.data.get("securities", {}).get(name, {})
            if name in hidden:
                pl.configure(text="hidden from the portfolio", fg=C["faint"])
            elif sym and name not in held:
                pl.configure(text="sold – history only", fg=C["faint"])
            elif q is not None:
                pl.configure(text=f"{fmt_price(q.price, q.currency)}  {fmt_pct(q.change_pct, 2, sign=True)}",
                             fg=change_color(q.change_pct))
            elif sym:
                pl.configure(text="fetching…" if self.prices.enabled else "no price", fg=C["faint"])
            else:
                pl.configure(text="not found – type the ticker" if rec.get("searched") else "searching…",
                             fg=C["red"] if rec.get("searched") else C["faint"])

    # ---- Transactions table ------------------------------------------------
    def _row_values(self, e: dict, s: Summary):
        k = e["kind"]
        amt = float(e.get("amount") or 0.0)
        acc = e.get("loan", "") if k in LOAN_KIND_KEYS else e.get("account", "")
        if k == "transfer":
            acc = f"{e.get('account', '')} → {e.get('to_account', '')}"
        desc = e.get("security", "") or ""
        note = e.get("note", "")
        desc = f"{desc} · {note}" if desc and note else desc or note
        qty = price = fees = fund = ""
        if k in ("buy", "sell"):
            qty = fmt_qty(e.get("qty") or 0)
            price = fmt_price(e.get("price") or 0, e.get("currency", ""))
            eur, est = s.amounts.get(e["id"], (amt, False))
            summa = ("≈ " if est else "") + fmt_num(-eur if k == "buy" else eur, 2)
        elif k == "transfer_in":
            qty = fmt_qty(e.get("qty") or 0)
            price = fmt_price(e["price"], e.get("currency", "")) if e.get("price") else ""
            summa = fmt_num(amt, 2) if amt else "–"
            fund = "gifted shares" if amt else "cost basis missing"
        elif k == "loan_pay":
            summa = fmt_num(-(amt + float(e.get("interest") or 0) + float(e.get("fees") or 0)), 2)
        else:
            sign = KIND_SIGN.get(k, 0)
            summa = fmt_num(sign * amt if sign else amt, 2)
        if k in ("buy", "sell", "loan_pay") and e.get("fees"):
            fees = fmt_num(e["fees"], 2)
        if k in ("deposit", "withdrawal"):
            lp = float(e.get("loan_part") or 0.0)
            own = amt - lp
            if lp > 0.005 and own > 0.005:
                fund = f"loan {fmt_money_short(lp)} · own {fmt_money_short(own)}"
            else:
                fund = "student loan" if lp > 0.005 else "own money"
        elif k == "loan_pay" and e.get("interest"):
            fund = f"principal {fmt_num(amt, 2)} · interest {fmt_num(e['interest'], 2)}"
        return (fmt_date(e["date"]), acc, KIND_TABLE[k], desc, qty, price, fees, summa, fund)

    def refresh_tree(self):
        s = self.summary
        flt = self.filter_var.get()
        self.tree.delete(*self.tree.get_children())
        ents = self.data["entries"]
        order = sorted(range(len(ents)), key=lambda i: (ents[i]["date"], i), reverse=True)
        hidden = excluded_securities(self.data)
        n = 0
        for i in order:
            e = ents[i]
            if e.get("security") in hidden:
                continue
            is_loan = e["kind"] in LOAN_KIND_KEYS
            if (flt == "Nordnet" and is_loan) or (flt == "Loans" and not is_loan):
                continue
            tags = ["odd"] if n % 2 else []
            if e["kind"] in ("buy", "sell") and s.amounts.get(e["id"], (0, False))[1]:
                tags.append("est")
            self.tree.insert("", "end", iid=e["id"], values=self._row_values(e, s), tags=tags)
            n += 1
        self.count_lbl.configure(text=f"{n} transactions")
        if self.editing and self.tree.exists(self.editing):
            self.tree.selection_set(self.editing)
            self.tree.see(self.editing)


def migrate_legacy_data(path: str = DATA_FILE) -> None:
    """Copy data from the old Finnish-named folder (tiedot/salkkukartta.json, hinnat.json,
    varmuuskopiot/) to data/ the first time the renamed app starts. The old folder is left as it is."""
    old = os.path.join(LEGACY_DATA_DIR, "salkkukartta.json")
    if os.path.exists(path) or not os.path.exists(old):
        return
    import shutil
    folder = os.path.dirname(path)
    os.makedirs(os.path.join(folder, "backups"), exist_ok=True)
    shutil.copy2(old, path)
    if os.path.exists(os.path.join(LEGACY_DATA_DIR, "hinnat.json")):
        shutil.copy2(os.path.join(LEGACY_DATA_DIR, "hinnat.json"), os.path.join(folder, "prices.json"))
    old_backups = os.path.join(LEGACY_DATA_DIR, "varmuuskopiot")
    if os.path.isdir(old_backups):
        for f in os.listdir(old_backups):
            shutil.copy2(os.path.join(old_backups, f),
                         os.path.join(folder, "backups", f.replace("salkkukartta-", "portfolio-")))


def main():
    path = DATA_FILE
    flag = next((f for f in ("--data", "--tiedosto") if f in sys.argv), None)
    if flag:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            path = os.path.abspath(sys.argv[i + 1])
    try:  # sharp text on scaled Windows displays
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        # own taskbar icon instead of Python's (see also set_window_app_id)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass
    if path == DATA_FILE:
        migrate_legacy_data()
    App(path).mainloop()


if __name__ == "__main__":
    main()
