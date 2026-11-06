#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Salkkukartta - sijoitus- ja lainamuistio
========================================

Offline-työpöytäsovellus, johon kirjataan Nordnetin talletukset, kaupat, osingot
ja tilien arvot sekä lainojen nostot ja maksut. Sovellus laskee, paljonko
Nordnetissä on opintolainaa ja paljonko omaa rahaa, mitä omistat ja paljonko
lainaa on jäljellä.

Vaatii Pythonin (3.8+), jossa on Tkinter. Pillow (PIL) tekee grafiikasta
pehmeäreunaisen; ilman sitä sovellus toimii, mutta grafiikka on karkeampi.

Käynnistys:  python salkkukartta.py   (tai tuplaklikkaa Kaynnista.bat)
             python salkkukartta.py --tiedosto polku.json   (muu tietotiedosto)

Tiedot tallentuvat automaattisesti jokaisen muutoksen jälkeen tiedostoon
tiedot/salkkukartta.json. Päivän ensimmäinen tallennus ottaa edellisestä
versiosta varmuuskopion kansioon tiedot/varmuuskopiot (30 viimeisintä).
"""

import csv
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


def resource_path(*parts: str) -> str:
    """Tiedostopolku sovelluksen kansiosta (esim. kuvake/kuvake-256.png)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)


APP_NAME = "Salkkukartta"
APP_ID = "TomasVallas.Salkkukartta"
DATA_FILE = resource_path("tiedot", "salkkukartta.json")
BACKUPS_KEPT = 30
MIN_DAYS = 14          # kuvaajan aikajana vähintään kaksi viikkoa

# ---------------------------------------------------------------------------
#  Kirjauslajit
# ---------------------------------------------------------------------------

NORDNET_KINDS = [("deposit", "Talletus"), ("buy", "Osto"), ("sell", "Myynti"), ("dividend", "Osinko"),
                 ("withdrawal", "Nosto"), ("transfer", "Siirto"), ("fee", "Kulu"), ("value", "Arvo")]
LOAN_KINDS = [("loan_draw", "Nosto"), ("loan_cap", "Pääomitus"), ("loan_pay", "Maksu"),
              ("loan_credit", "Hyvitys")]
LOAN_KIND_KEYS = {k for k, _ in LOAN_KINDS}

KIND_TABLE = {
    "deposit": "Talletus", "withdrawal": "Nosto", "transfer": "Siirto", "buy": "Osto", "sell": "Myynti",
    "dividend": "Osinko", "fee": "Kulu", "value": "Arvo", "loan_draw": "Lainan nosto",
    "loan_cap": "Pääomitus", "loan_pay": "Lainan maksu", "loan_credit": "Hyvitys",
}
# summan etumerkki tapahtumataulukossa (0 = näytetään ilman merkkiä)
KIND_SIGN = {"deposit": 1, "withdrawal": -1, "transfer": 0, "dividend": 1, "fee": -1, "value": 0,
             "loan_draw": 1, "loan_cap": 0, "loan_credit": 0}

CURRENCIES = ["USD", "EUR", "SEK", "NOK", "DKK", "CAD", "GBP"]
# Varakurssit (1 EUR = x) arvioille, joita käytetään vain, jos valuutalla ei ole
# yhtään vahvistettua kauppaa, josta kurssin voisi laskea.
FX_DEFAULT = {"USD": 1.12, "SEK": 11.2, "NOK": 11.7, "DKK": 7.46, "CAD": 1.58, "GBP": 0.86}


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def default_data() -> dict:
    return {
        "version": 1,
        "accounts": [
            {"id": "OST", "name": "Osakesäästötili", "number": ""},
            {"id": "AOT", "name": "Arvo-osuustili", "number": ""},
        ],
        "loans": [{"name": "Opintolaina"}],
        "entries": [],
        "notes": "",
        "settings": {"theme": "", "fx": dict(FX_DEFAULT)},
    }


def normalize(data: dict) -> dict:
    """Täydentää puuttuvat kentät (vanhemmat tai käsin muokatut tiedostot)."""
    base = default_data()
    for k, v in base.items():
        data.setdefault(k, v)
    st = data["settings"]
    st.setdefault("theme", "")
    fx = st.setdefault("fx", {})
    for k, v in FX_DEFAULT.items():
        fx.setdefault(k, v)
    if not data["accounts"]:
        data["accounts"] = base["accounts"]
    for e in data["entries"]:
        e.setdefault("id", new_id())
    return data


class Store:
    """JSON-tiedosto: atominen tallennus ja päivittäinen varmuuskopio."""

    def __init__(self, path: str = DATA_FILE):
        self.path = os.path.abspath(path)
        self.error = ""
        self.exists = os.path.exists(path)
        self.data = self.load()

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return normalize(json.load(f))
        except FileNotFoundError:
            return default_data()
        except (OSError, ValueError) as ex:
            # Rikkinäistä tiedostoa ei ylikirjoiteta: siitä otetaan kopio talteen.
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                shutil.copy2(self.path, f"{self.path}.rikki-{stamp}")
            except OSError:
                pass
            self.error = f"Tietoja ei voitu lukea ({ex}). Vanha tiedosto kopioitiin talteen."
            return default_data()

    @property
    def backup_dir(self) -> str:
        return os.path.join(os.path.dirname(self.path), "varmuuskopiot")

    def _backup(self):
        if not os.path.exists(self.path):
            return
        os.makedirs(self.backup_dir, exist_ok=True)
        name = os.path.join(self.backup_dir, f"salkkukartta-{dt.date.today().isoformat()}.json")
        if os.path.exists(name):
            return
        shutil.copy2(self.path, name)
        olds = sorted(f for f in os.listdir(self.backup_dir)
                      if f.startswith("salkkukartta-") and f.endswith(".json"))
        for f in olds[:-BACKUPS_KEPT]:
            os.remove(os.path.join(self.backup_dir, f))

    def save(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._backup()
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self.error = ""
            self.exists = True
            return True
        except OSError as ex:
            self.error = f"Tallennus epäonnistui: {ex}"
            return False


# ---------------------------------------------------------------------------
#  Laskenta
# ---------------------------------------------------------------------------

@dataclass
class Holding:
    account: str
    security: str
    currency: str = "EUR"
    qty: float = 0.0
    cost: float = 0.0          # hankinta-arvo euroina kuluineen
    cost_ccy: float = 0.0      # hankinta-arvo kaupankäyntivaluutassa ilman kuluja
    fees: float = 0.0
    estimated: bool = False

    @property
    def avg_price(self) -> float:
        return self.cost_ccy / self.qty if abs(self.qty) > 1e-9 else 0.0


@dataclass
class LoanState:
    name: str
    drawn: float = 0.0
    capitalized: float = 0.0
    principal: float = 0.0
    interest: float = 0.0
    fees: float = 0.0
    credit: float = 0.0

    @property
    def balance(self) -> float:
        return self.drawn + self.capitalized - self.principal - self.credit

    @property
    def active(self) -> bool:
        return any(abs(x) > 1e-9 for x in (self.drawn, self.capitalized, self.principal,
                                            self.interest, self.credit))


@dataclass
class Summary:
    deposited: float = 0.0
    withdrawn: float = 0.0
    loan_in: float = 0.0       # opintolainaa Nordnetissä (talletukset − nostot)
    own_in: float = 0.0        # omaa rahaa Nordnetissä
    cash: Dict[str, float] = field(default_factory=dict)
    acc_cost: Dict[str, float] = field(default_factory=dict)
    acc_net: Dict[str, float] = field(default_factory=dict)    # nettotalletukset tileittäin
    cost_total: float = 0.0
    holdings: List[Holding] = field(default_factory=list)
    trade_fees: float = 0.0
    other_fees: float = 0.0
    dividends: float = 0.0
    realized: float = 0.0
    loans: Dict[str, LoanState] = field(default_factory=dict)
    rates: Dict[str, float] = field(default_factory=dict)      # EUR / valuuttayksikkö
    rate_src: Dict[str, str] = field(default_factory=dict)     # mistä kurssi on peräisin
    amounts: Dict[str, Tuple[float, bool]] = field(default_factory=dict)  # kaupan EUR ja onko arvio
    estimates: int = 0
    value: Optional[float] = None
    value_date: Optional[dt.date] = None
    days: List[dt.date] = field(default_factory=list)
    series: Dict[str, list] = field(default_factory=lambda: {k: [] for k in
                                                              ("own", "loan", "cost", "value", "debt")})
    value_marks: List[int] = field(default_factory=list)

    @property
    def net_in(self) -> float:
        return self.loan_in + self.own_in

    @property
    def cash_total(self) -> float:
        return sum(self.cash.values())

    @property
    def loan_balance(self) -> float:
        return sum(ln.balance for ln in self.loans.values())

    @property
    def book_value(self) -> float:
        return self.cost_total + self.cash_total

    @property
    def holding_fees(self) -> float:
        return sum(h.fees for h in self.holdings)


def iso_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def fx_rates(entries: List[dict], settings: dict) -> Tuple[Dict[str, float], Dict[str, str]]:
    """EUR per valuuttayksikkö. Ensisijaisesti viimeisimmästä vahvistetusta kaupasta
    (sisältää Nordnetin valuutanvaihtokulun), muuten asetusten varakurssista."""
    rates = {c: 1.0 / v for c, v in settings.get("fx", {}).items() if v}
    src = {c: "varakurssi" for c in rates}
    rates["EUR"], src["EUR"] = 1.0, ""
    for e in entries:  # aikajärjestyksessä: viimeisin jää voimaan
        if e["kind"] in ("buy", "sell") and e.get("amount") is not None and e.get("currency", "EUR") != "EUR":
            gross = float(e.get("qty") or 0) * float(e.get("price") or 0)
            fees = float(e.get("fees") or 0)
            net = e["amount"] - fees if e["kind"] == "buy" else e["amount"] + fees
            if gross > 0 and net > 0:
                rates[e["currency"]] = net / gross
                src[e["currency"]] = f"{e.get('security', 'kauppa')} {fmt_date(e['date'])}"
    return rates, src


def trade_eur(e: dict, rates: Dict[str, float]) -> Tuple[float, bool]:
    """Kaupan euromäärä (positiivinen) ja onko se arvio."""
    if e.get("amount") is not None:
        return float(e["amount"]), False
    gross = float(e.get("qty") or 0) * float(e.get("price") or 0) * rates.get(e.get("currency", "EUR"), 1.0)
    fees = float(e.get("fees") or 0)
    return (gross + fees if e["kind"] == "buy" else gross - fees), True


def sorted_entries(data: dict) -> List[dict]:
    ents = data["entries"]
    return [ents[i] for i in sorted(range(len(ents)), key=lambda i: (ents[i]["date"], i))]


def ledger(data: dict, today: Optional[dt.date] = None) -> Summary:
    """Käy kirjaukset läpi aikajärjestyksessä ja laskee saldot, omistukset ja aikasarjat."""
    today = today or dt.date.today()
    entries = sorted_entries(data)
    s = Summary()
    s.rates, s.rate_src = fx_rates(entries, data.get("settings", {}))
    accounts = [a["id"] for a in data.get("accounts", [])]
    for a in accounts:
        s.cash[a] = s.acc_cost[a] = s.acc_net[a] = 0.0
    for ln in data.get("loans", []):
        s.loans[ln["name"]] = LoanState(ln["name"])
    hold: Dict[Tuple[str, str], Holding] = {}
    snap: Dict[str, float] = {}   # tili -> viimeisin arvo + rahavirrat sen jälkeen

    def add(d: dict, k: str, x: float):
        d[k] = d.get(k, 0.0) + x

    def snapflow(acc: str, x: float):
        if acc in snap:
            snap[acc] += x

    def apply(e: dict):
        k, acc = e["kind"], e.get("account", "")
        amt = float(e.get("amount") or 0.0)
        if k in ("deposit", "withdrawal"):
            sg = 1 if k == "deposit" else -1
            lp = float(e.get("loan_part") or 0.0)
            add(s.cash, acc, sg * amt)
            add(s.acc_net, acc, sg * amt)
            snapflow(acc, sg * amt)
            if k == "deposit":
                s.deposited += amt
            else:
                s.withdrawn += amt
            s.loan_in += sg * lp
            s.own_in += sg * (amt - lp)
        elif k == "transfer":
            to = e.get("to_account", "")
            for a, x in ((acc, -amt), (to, amt)):
                add(s.cash, a, x)
                add(s.acc_net, a, x)
                snapflow(a, x)
        elif k in ("buy", "sell"):
            eur, est = trade_eur(e, s.rates)
            s.amounts[e["id"]] = (eur, est)
            s.estimates += int(est)
            sec = (e.get("security") or "").strip() or "?"
            h = hold.get((acc, sec))
            if h is None:
                h = hold[(acc, sec)] = Holding(acc, sec, e.get("currency", "EUR"))
            q, p = float(e.get("qty") or 0), float(e.get("price") or 0)
            fees = float(e.get("fees") or 0)
            s.trade_fees += fees
            snapflow(acc, -fees)
            if k == "buy":
                add(s.cash, acc, -eur)
                h.qty += q
                h.cost += eur
                h.cost_ccy += q * p
                h.fees += fees
                h.estimated = h.estimated or est
                add(s.acc_cost, acc, eur)
                s.cost_total += eur
            else:
                add(s.cash, acc, eur)
                frac = min(1.0, q / h.qty) if h.qty > 1e-9 else 0.0
                removed = h.cost * frac
                s.realized += eur - removed
                h.cost -= removed
                h.cost_ccy -= h.cost_ccy * frac
                h.fees -= h.fees * frac
                h.qty -= q
                add(s.acc_cost, acc, -removed)
                s.cost_total -= removed
        elif k == "dividend":
            add(s.cash, acc, amt)
            s.dividends += amt
        elif k == "fee":
            add(s.cash, acc, -amt)
            s.other_fees += amt
            snapflow(acc, -amt)
        elif k == "value":
            snap[acc] = amt
            s.value_date = iso_date(e["date"])
        elif k in LOAN_KIND_KEYS:
            name = (e.get("loan") or "").strip() or "Laina"
            ln = s.loans.get(name)
            if ln is None:
                ln = s.loans[name] = LoanState(name)
            if k == "loan_draw":
                ln.drawn += amt
            elif k == "loan_cap":
                ln.capitalized += amt
            elif k == "loan_pay":
                ln.principal += amt
                ln.interest += float(e.get("interest") or 0)
                ln.fees += float(e.get("fees") or 0)
            else:
                ln.credit += amt

    def total_value() -> Optional[float]:
        if not snap:
            return None
        tot = 0.0
        for a in set(accounts) | set(snap) | set(s.cash):
            tot += snap[a] if a in snap else s.cash.get(a, 0.0) + s.acc_cost.get(a, 0.0)
        return tot

    if entries:
        first, last = iso_date(entries[0]["date"]), max(iso_date(entries[-1]["date"]), today)
        start = min(first - dt.timedelta(days=1), last - dt.timedelta(days=MIN_DAYS - 1))
        j, ser = 0, s.series
        for i in range((last - start).days + 1):
            d = start + dt.timedelta(days=i)
            iso = d.isoformat()
            while j < len(entries) and entries[j]["date"] <= iso:
                if entries[j]["kind"] == "value":
                    s.value_marks.append(i)
                apply(entries[j])
                j += 1
            s.days.append(d)
            ser["own"].append(s.own_in)
            ser["loan"].append(s.loan_in)
            ser["cost"].append(s.cost_total)
            ser["debt"].append(s.loan_balance)
            ser["value"].append(total_value())
        s.value = ser["value"][-1]
    s.holdings = sorted((h for h in hold.values() if abs(h.qty) > 1e-9), key=lambda h: -h.cost)
    return s


# ---------------------------------------------------------------------------
#  Muotoilu ja syötteiden tulkinta
# ---------------------------------------------------------------------------

def fmt_num(x: float, dec: int = 0) -> str:
    if abs(round(x, dec)) < 10 ** (-dec) / 2:
        x = 0.0
    s = f"{abs(x):,.{dec}f}".replace(",", " ").replace(".", ",")
    return ("−" if x < 0 else "") + s


def fmt_eur(x: float, dec: int = 2, sign: bool = False) -> str:
    s = fmt_num(x, dec)
    if sign and round(x, dec) > 0:
        s = "+" + s
    return s + " €"


def fmt_money_short(x: float) -> str:
    """Tasaeurot ilman senttejä: 1 300 / 150,50."""
    return fmt_num(x) if abs(x - round(x)) < 0.005 else fmt_num(x, 2)


def fmt_pct(x: float) -> str:
    return fmt_num(x * 100, 0) + " %"


def fmt_qty(x: float) -> str:
    return fmt_num(x) if abs(x - round(x)) < 1e-9 else fmt_num(x, 4).rstrip("0").rstrip(",")


def fmt_price(x: float) -> str:
    s = f"{x:.4f}".rstrip("0")
    dec = max(2, len(s.split(".")[1]) if "." in s else 0)
    return fmt_num(x, dec)


def fmt_input(x: Optional[float]) -> str:
    """Luku syöttökenttään: ei tuhaterottimia, desimaalipilkku."""
    if x is None:
        return ""
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def fmt_date(iso: str) -> str:
    d = iso_date(iso) if isinstance(iso, str) else iso
    return f"{d.day}.{d.month}.{d.year}"


def parse_num(txt: str) -> Optional[float]:
    """'1 013,98' / '186.3476' / '1.013,98 €' -> float. Tyhjä -> None, virhe -> ValueError."""
    t = txt.strip()
    for ch in (" ", " ", " ", "€", "%"):
        t = t.replace(ch, "")
    t = t.replace("−", "-")
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "")
    t = t.replace(",", ".")
    v = float(t)
    if math.isnan(v) or math.isinf(v):
        raise ValueError(txt)
    return v


def parse_date(txt: str, today: Optional[dt.date] = None) -> dt.date:
    """'6.11.2026', '6.11.26', '6.11.', '2026-11-06', 'tänään', 'eilen' -> date."""
    today = today or dt.date.today()
    t = txt.strip().lower()
    if t in ("", "t", "tänään", "nyt"):
        return today
    if t == "eilen":
        return today - dt.timedelta(days=1)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    parts = [p for p in re.split(r"[./]", t) if p]
    if len(parts) == 2:
        d, mo, y = parts[0], parts[1], str(today.year)
    elif len(parts) == 3:
        d, mo, y = parts
    else:
        raise ValueError(txt)
    if len(y) == 2:
        y = "20" + y
    return dt.date(int(y), int(mo), int(d))


def nice_ticks(lo: float, hi: float, n: int = 6):
    if hi - lo < 1e-9:
        hi = lo + 1.0
    span = hi - lo
    mag = 10 ** math.floor(math.log10(span / n))
    step = mag
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * mag
        if span / step <= n:
            break
    tlo, thi = math.floor(lo / step) * step, math.ceil(hi / step) * step
    ticks, v = [], tlo
    while v <= thi + step * 1e-6:
        ticks.append(v)
        v += step
    return tlo, thi, ticks


def date_ticks(days: List[dt.date], max_n: int) -> List[Tuple[int, str]]:
    """Aika-akselin merkit: päivät, viikot (maanantait) tai kuukaudet."""
    n = len(days)
    if not n:
        return []
    for step in (1, 2, 3, 7, 14):
        if n / step <= max_n:
            if step == 7:
                idx = [i for i, d in enumerate(days) if d.weekday() == 0]
            elif step == 14:
                idx = [i for i, d in enumerate(days) if d.weekday() == 0 and (d.toordinal() // 7) % 2 == 0]
            else:
                idx = [i for i, d in enumerate(days) if d.toordinal() % step == 0]
            return [(i, f"{days[i].day}.{days[i].month}.") for i in idx]
    for m in (1, 2, 3, 6, 12, 24, 60):
        idx = [i for i, d in enumerate(days) if d.day == 1 and (d.month - 1) % m == 0
               and (m < 12 or (d.month == 1 and d.year % (m // 12) == 0))]
        if len(idx) <= max_n:
            return [(i, str(days[i].year) if m >= 12 else f"{days[i].month}/{days[i].year}") for i in idx]
    return []


def dash_polyline(P, on: float, off: float):
    """Pilkkoo murtoviivan katkoviivan paloiksi."""
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


# ---------------------------------------------------------------------------
#  Käyttöliittymä
# ---------------------------------------------------------------------------

import tkinter as tk
from tkinter import ttk, filedialog
import tkinter.font as tkfont

try:  # pehmeäreunainen grafiikka
    from PIL import Image, ImageDraw, ImageTk
    _BOX = getattr(Image, "Resampling", Image).BOX
    HAVE_PIL = hasattr(ImageDraw.ImageDraw, "rounded_rectangle")
except Exception:  # pragma: no cover
    HAVE_PIL = False

SS = 4  # ylinäytteistys: piirretään 4x tarkkuudella ja pienennetään

# Hiiren rulla: Windows lähettää <MouseWheel>, Linuxin Tk 8.6 painikkeet 4 (ylös) ja 5 (alas).
WHEEL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")


def wheel_step(e) -> int:
    """Vieritysrivit: negatiivinen = ylös, positiivinen = alas."""
    if e.num == 4:
        return -1
    if e.num == 5:
        return 1
    return int(-e.delta / 120) or (-1 if e.delta > 0 else 1)


# Neutraalit harmaat pinnat, vihreä korostus. Värit kuvaavat rahan lähdettä:
# oma raha = vihreä, opintolaina = meripihka, sijoitettu = violetti, laina = punainen.
THEMES = {
    "dark": {
        "bg": "#0F1011", "card": "#17181A", "card2": "#1D1E21", "input": "#121315",
        "border": "#2B2D31", "grid": "#222326", "hover": "#27292D",
        "text": "#EDEDEF", "muted": "#A0A3A8", "faint": "#686B71",
        "accent": "#3ECF8E", "accent_hi": "#62DCA6", "on_accent": "#07140E", "sel": "#1E3329",
        "own": "#3ECF8E", "loan": "#F2B53D", "invest": "#A78BFA", "value": "#EDEDEF", "debt": "#F87171",
        "cash": "#7D8189", "red": "#F87171", "bad": "#EF4444", "bad_bg": "#2A1517", "bad_fg": "#FCA5A5",
        "good": "#3ECF8E",
    },
    "light": {
        "bg": "#F2F2EF", "card": "#FFFFFF", "card2": "#F6F6F3", "input": "#FFFFFF",
        "border": "#DCDCD6", "grid": "#ECECE8", "hover": "#ECECE7",
        "text": "#1B1C1F", "muted": "#5D6066", "faint": "#9A9CA1",
        "accent": "#0E9F6E", "accent_hi": "#0B8A5F", "on_accent": "#FFFFFF", "sel": "#DDF1E7",
        "own": "#0E9F6E", "loan": "#D4880B", "invest": "#7C5CE0", "value": "#1B1C1F", "debt": "#DC2626",
        "cash": "#9A9CA1", "red": "#DC2626", "bad": "#DC2626", "bad_bg": "#FDEDED", "bad_fg": "#B42318",
        "good": "#0E9F6E",
    },
}
C: Dict[str, str] = dict(THEMES["dark"])


def blend(a: str, b: str, t: float) -> str:
    """Sekoittaa värit: t = a:n osuus."""
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(round(x * t + y * (1 - t)) for x, y in zip(pa, pb))


def system_theme() -> str:
    """Windowsin sovellusteema (vaalea / tumma). Muualla oletuksena tumma."""
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
    """Windows 10/11: ikkunan otsikkopalkki tummaksi tai vaaleaksi teeman mukaan."""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = int(win.wm_frame(), 16)
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (uusi / vanha numero)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val),
                                                          ctypes.sizeof(val)) == 0:
                break
        # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER: piirretään otsikko uudelleen
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004)
    except Exception:
        pass


def set_window_app_id(hwnd: int, app_id: str = APP_ID) -> None:
    """Ikkunakohtainen sovellustunniste (Windows), jotta tehtäväpalkissa näkyy oma kuvake
    eikä Pythonin logo – toimii myös Microsoft Storen Pythonilla."""
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


class Segmented(tk.Frame):
    """Valitsin, jossa yksi vaihtoehto on kerrallaan valittuna. options: [str] tai [(avain, teksti)]."""

    def __init__(self, master, options, variable: tk.StringVar, sc: float, font, command=None):
        super().__init__(master, bg=C["card2"], highlightthickness=1, highlightbackground=C["border"])
        self.var, self.command, self.labels = variable, command, {}
        for opt in options:
            key, text = opt if isinstance(opt, tuple) else (opt, opt)
            lbl = tk.Label(self, text=text, font=font, padx=int(12 * sc), pady=int(4 * sc),
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


class ChipGrid(tk.Frame):
    """Kirjauslajin valinta: painikkeet ruudukossa."""

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
    """Pystysuunnassa rullattava alue.

    grow: widget (esim. kuvaaja), joka venyy täyttämään vapaan korkeuden, mutta ei
    kutistu alle grow_min-korkeuden – silloin alue alkaa rullata.
    autohide: vierityspalkki näkyy vain, kun sisältö ei mahdu."""

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


def _paint_items(canvas: tk.Canvas, W: int, H: int, items, sc: float):
    """Piirtää kuvaajan osat: Pillowlla pehmeäreunaisina, muuten Tk:n viivoina."""
    if not HAVE_PIL:
        for it in items:
            kind = it[0]
            if kind == "hline":
                _, x0, x1, y, col = it
                canvas.create_line(x0, y, x1, y, fill=col)
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
        return None
    img = Image.new("RGB", (W * SS, H * SS), C["card"])
    d = ImageDraw.Draw(img)
    for it in items:
        kind = it[0]
        if kind == "hline":     # tasan yhden pikselin rivi = terävä
            _, x0, x1, y, col = it
            yy = int(y)
            d.rectangle([int(x0) * SS, yy * SS, int(x1) * SS, yy * SS + SS - 1], fill=col)
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
    photo = ImageTk.PhotoImage(img.resize((W, H), _BOX))
    canvas.create_image(0, 0, image=photo, anchor="nw")
    return photo


class StackBar(tk.Canvas):
    """Vaakapalkki, joka jakautuu osiin (esim. opintolaina / oma raha)."""

    def __init__(self, master, sc: float):
        self.h = int(12 * sc)
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


class Chart(tk.Canvas):
    """Rahan kehitys päivittäin: talletukset (oma raha + opintolaina pinottuna),
    sijoitettu hankinta-arvo, salkun arvo ja lainasaldo."""

    LEGEND = [("Oma raha", "own", "area"), ("Opintolaina", "loan", "area"),
              ("Sijoitettu (hankinta-arvo)", "invest", "line"), ("Salkun arvo", "value", "line"),
              ("Lainaa jäljellä", "debt", "dash")]

    def __init__(self, master, sc: float, fonts: dict):
        super().__init__(master, bg=C["card"], highlightthickness=0, bd=0)
        self.sc, self.f = sc, fonts
        self.s: Optional[Summary] = None
        self.geom = None
        self._photo = None
        self._job = None
        self.bind("<Configure>", lambda e: self._later())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self.delete("hover"))

    def _later(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(30, self.redraw)

    def set_data(self, s: Summary):
        self.s = s
        self.redraw()

    def redraw(self):
        self._job = None
        self.delete("all")
        W, H = self.winfo_width(), self.winfo_height()
        sc, s = self.sc, self.s
        if W < 50 or H < 50:
            return
        if not s or not s.days:
            self.create_text(W / 2, H / 2, text="Ei vielä kirjauksia – lisää ensimmäinen vasemmalta.",
                             fill=C["muted"], font=self.f["body"])
            return
        ser, n = s.series, len(s.days)
        top = [a + b for a, b in zip(ser["own"], ser["loan"])]
        vals = top + ser["own"] + ser["cost"] + ser["debt"] + [v for v in ser["value"] if v is not None]
        tlo, thi, ticks = nice_ticks(min(0.0, min(vals)), max(vals), 6)
        L, R, T, B = 74 * sc, 18 * sc, 16 * sc, 30 * sc
        pw, ph = W - L - R, H - T - B

        def XB(i):  # päivän i alkureuna
            return L + pw * i / n

        def XC(i):  # päivän i keskikohta
            return L + pw * (i + 0.5) / n

        def Y(v):
            return T + ph * (1 - (v - tlo) / (thi - tlo))

        def steps(data):
            pts = []
            for i, v in enumerate(data):
                pts += [(XB(i), Y(v)), (XB(i + 1), Y(v))]
            return pts

        self.geom = (L, R, T, B, n, XC, Y)
        items, texts = [], []
        for v in ticks:
            items.append(("hline", L, W - R, Y(v), C["grid"] if abs(v) > 1e-9 else C["faint"]))
            texts.append((L - 10 * sc, Y(v), fmt_num(v), "e", C["muted"], "small"))
        for i, lab in date_ticks(s.days, max(2, int(pw / (64 * sc)))):
            items.append(("vline", XC(i), T, T + ph, C["grid"], False))
            texts.append((XC(i), T + ph + 14 * sc, lab, "center", C["muted"], "small"))
        own_pts, top_pts, y0 = steps(ser["own"]), steps(top), Y(0)
        items.append(("poly", top_pts + own_pts[::-1], blend(C["loan"], C["card"], 0.28)))
        items.append(("poly", [(XB(0), y0)] + own_pts + [(XB(n), y0)], blend(C["own"], C["card"], 0.28)))
        items.append(("line", own_pts, C["own"], 2 * sc, False))
        items.append(("line", top_pts, C["loan"], 2 * sc, False))
        if any(abs(v) > 1e-9 for v in ser["cost"]):
            items.append(("line", steps(ser["cost"]), C["invest"], 2 * sc, False))
        if any(abs(v) > 1e-9 for v in ser["debt"]):
            items.append(("line", steps(ser["debt"]), C["debt"], 2 * sc, True))
        vpts = [(XC(i), Y(v)) for i, v in enumerate(ser["value"]) if v is not None]
        if len(vpts) >= 2:
            items.append(("line", vpts, C["value"], 2 * sc, False))
        for i in s.value_marks:
            if ser["value"][i] is not None:
                items.append(("dot", XC(i), Y(ser["value"][i]), 3.5 * sc, C["value"], C["card"], 2 * sc))
        self._photo = _paint_items(self, W, H, items, sc)
        for x, y, text, anchor, col, font in texts:
            self.create_text(x, y, text=text, anchor=anchor, fill=col, font=self.f[font])

    def _hover(self, e):
        self.delete("hover")
        s = self.s
        if not s or not s.days or not self.geom:
            return
        L, R, T, B, n, XC, Y = self.geom
        W, H, sc = self.winfo_width(), self.winfo_height(), self.sc
        if e.x < L or e.x > W - R:
            return
        i = max(0, min(n - 1, int((e.x - L) / max(1, W - L - R) * n)))
        ser, x = s.series, XC(i)
        self.create_line(x, T, x, H - B, fill=C["faint"], tags="hover")
        top = ser["own"][i] + ser["loan"][i]
        rows = [("Opintolaina", ser["loan"][i], "loan", top), ("Oma raha", ser["own"][i], "own", ser["own"][i]),
                ("Talletukset yhteensä", top, None, None), ("Sijoitettu", ser["cost"][i], "invest", ser["cost"][i])]
        if ser["value"][i] is not None:
            rows.append(("Salkun arvo", ser["value"][i], "value", ser["value"][i]))
        if any(abs(v) > 1e-9 for v in ser["debt"]):
            rows.append(("Lainaa jäljellä", ser["debt"][i], "debt", ser["debt"][i]))
        for _n, _v, col, yv in rows:
            if col and yv is not None:
                r = 4 * sc
                self.create_oval(x - r, Y(yv) - r, x + r, Y(yv) + r, fill=C[col], outline=C["card"],
                                 width=2 * sc, tags="hover")
        d = s.days[i]
        lines = [f"{d.day}.{d.month}.{d.year}"] + rows
        bw, lh = 230 * sc, 18 * sc
        bh = lh * len(lines) + 10 * sc
        bx = x + 14 * sc
        if bx + bw > W - 4 * sc:
            bx = x - 14 * sc - bw
        by = max(T, min(e.y - bh / 2, H - B - bh))
        self.create_rectangle(bx, by, bx + bw, by + bh, fill=C["card2"], outline=C["border"], tags="hover")
        self.create_text(bx + 10 * sc, by + 6 * sc + lh / 2, text=lines[0], anchor="w",
                         fill=C["text"], font=self.f["bodyb"], tags="hover")
        for k, (name, v, col, _y) in enumerate(lines[1:], start=1):
            yy = by + 6 * sc + lh * k + lh / 2
            if col:
                self.create_rectangle(bx + 10 * sc, yy - 4 * sc, bx + 18 * sc, yy + 4 * sc,
                                      fill=C[col], outline="", tags="hover")
            self.create_text(bx + 24 * sc, yy, text=name, anchor="w", fill=C["muted"],
                             font=self.f["small"], tags="hover")
            self.create_text(bx + bw - 10 * sc, yy, text=fmt_eur(v), anchor="e", fill=C["text"],
                             font=self.f["small"], tags="hover")


# ---------------------------------------------------------------------------
#  Lomake: kentät kirjauslajeittain
# ---------------------------------------------------------------------------

# avain: (otsikko, kenttätyyppi, yksikkö)
FIELD_DEF = {
    "date": ("Päivä", "entry", ""),
    "account": ("Tili", "seg", ""),
    "to_account": ("Mihin tilille", "seg", ""),
    "loan": ("Laina", "combo", ""),
    "security": ("Arvopaperi", "combo", ""),
    "qty": ("Määrä", "entry", "kpl"),
    "price": ("Kurssi", "entry", "@ccy"),
    "currency": ("Valuutta", "ccy", ""),
    "fees": ("Kulut", "entry", "€"),
    "amount": ("Summa", "entry", "€"),
    "interest": ("Korko", "entry", "€"),
    "loan_part": ("Josta opintolainaa", "entry", "€"),
    "note": ("Muistiinpano", "wide", ""),
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
    "value": ["date", "account", "amount", "note"],
    "loan_draw": ["date", "loan", "amount", "note"],
    "loan_cap": ["date", "loan", "amount", "note"],
    "loan_pay": ["date", "loan", "amount", "interest", "fees", "note"],
    "loan_credit": ["date", "loan", "amount", "note"],
}
LABEL_OVERRIDE = {
    ("transfer", "account"): "Mistä tililtä",
    ("buy", "amount"): "Summa EUR (tyhjä = arvio)",
    ("sell", "amount"): "Summa EUR (tyhjä = arvio)",
    ("dividend", "amount"): "Osinko (netto)",
    ("fee", "amount"): "Kulu tai vero",
    ("value", "amount"): "Tilin arvo Nordnetissä",
    ("loan_draw", "amount"): "Nostettu summa",
    ("loan_cap", "amount"): "Pääomitettu korko",
    ("loan_pay", "amount"): "Lyhennys (pääomaa)",
    ("loan_pay", "fees"): "Pankin kulut",
    ("loan_credit", "amount"): "Opintolainahyvitys",
}
KIND_HINT = {
    "deposit": "Talletus Nordnetiin. Kerro, paljonko siitä on opintolainaa – loput lasketaan omaksi rahaksi.",
    "buy": "Määrä, kurssi ja kulut kuten Nordnetin Kaupat-välilehdellä. Summa EUR näkyy Tapahtumissa. "
           "Jos jätät sen tyhjäksi, se arvioidaan viimeisimmän kaupan valuuttakurssilla.",
    "sell": "Summa EUR on tilille tuleva rahamäärä kulujen jälkeen. Tyhjä summa arvioidaan "
            "viimeisimmän kaupan valuuttakurssilla.",
    "dividend": "Tilille maksettu osinko lähdeveron jälkeen.",
    "withdrawal": "Nosto Nordnetistä pankkitilille. Merkitse opintolainan osuus, jos nostat lainarahaa pois.",
    "transfer": "Siirto omien Nordnet-tilien välillä. Ei muuta talletusten määrää.",
    "fee": "Muu kulu tai vero, joka veloitetaan tililtä.",
    "value": "Tilin kokonaisarvo Nordnetissä (omistukset + käteinen). Kirjaa se aina, kun haluat "
             "nähdä tuoton.",
    "loan_draw": "Pankin maksama lainaerä pankkitilillesi.",
    "loan_cap": "Korko, jonka pankki lisää lainaan (esim. 15.6. ja 15.12.).",
    "loan_pay": "Lyhennys, korko ja kulut, jotka maksat pankille.",
    "loan_credit": "Kelan opintolainahyvitys, joka pienentää lainaa.",
}

TILES = [("net", "Nordnetissä", None), ("loan", "Opintolainaa", "loan"), ("own", "Omaa rahaa", "own"),
         ("debt", "Lainaa jäljellä", "debt"), ("cost", "Sijoitettu", "invest"),
         ("cash", "Käteinen tileillä", "cash"), ("value", "Salkun arvo", "value"),
         ("worth", "Nettovarallisuus", None)]

TREE_COLS = [("date", "Päivä", 80, "w"), ("acc", "Tili", 84, "w"), ("kind", "Laji", 88, "w"),
             ("desc", "Kuvaus", 150, "w"), ("qty", "Määrä", 46, "e"), ("price", "Kurssi", 98, "e"),
             ("fees", "Kulut", 54, "e"), ("sum", "Summa €", 98, "e"), ("fund", "Rahoitus", 140, "w")]


class App(tk.Tk):
    def __init__(self, data_path: str = DATA_FILE):
        super().__init__()
        self.store = Store(data_path)
        self.data = self.store.data
        theme = self.data["settings"].get("theme") or system_theme()
        self.theme = theme if theme in THEMES else "dark"
        C.clear()
        C.update(THEMES[self.theme])
        self.title("Salkkukartta – sijoitukset, lainat ja oma raha")
        self.sc = self.winfo_fpixels("1i") / 96.0
        sc = self.sc
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(int(1400 * sc), sw - 40), min(int(940 * sc), sh - 80)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(min(int(1150 * sc), w), min(int(700 * sc), h))
        self._icon()
        if os.name == "nt":
            try:
                self.update_idletasks()
                set_window_app_id(int(self.wm_frame(), 16))
            except Exception:
                pass
        set_titlebar_dark(self, self.theme == "dark")
        self._fonts()
        self.summary = Summary()
        self.editing: Optional[str] = None
        self._undo: Optional[Tuple[int, dict]] = None
        self._saved_form = None
        self._tab = "overview"
        self._filter = "Kaikki"
        self._jobs: Dict[str, object] = {}
        self.main: Optional[tk.Frame] = None
        self._build_all()
        self.refresh()
        if self.store.error:
            self.flash(self.store.error, bad=True, sticky=True)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---- ulkoasu -----------------------------------------------------------
    def S(self, px: float) -> int:
        return int(round(px * self.sc))

    def _icon(self):
        # Ikkunan ja tehtäväpalkin kuvake PNG:stä usealla koolla (Tk ei lue PNG-pakattua .ico:ta).
        png = resource_path("kuvake", "kuvake-256.png")
        try:
            if os.path.exists(png):
                if HAVE_PIL:
                    src = Image.open(png).convert("RGBA")
                    lz = getattr(Image, "Resampling", Image).LANCZOS
                    self._imgs = [ImageTk.PhotoImage(src.resize((n, n), lz), master=self)
                                  for n in (256, 64, 48, 32, 24, 16)]
                else:
                    self._imgs = [tk.PhotoImage(file=png, master=self)]
                self.iconphoto(True, *self._imgs)
        except (tk.TclError, OSError):
            pass

    def _fonts(self):
        fam = "Segoe UI" if "Segoe UI" in tkfont.families() else "TkDefaultFont"
        self.f = {
            "title": tkfont.Font(family=fam, size=17, weight="bold"),
            "h": tkfont.Font(family=fam, size=9, weight="bold"),
            "body": tkfont.Font(family=fam, size=10),
            "bodyb": tkfont.Font(family=fam, size=10, weight="bold"),
            "small": tkfont.Font(family=fam, size=9),
            "tiny": tkfont.Font(family=fam, size=8),
            "kpi": tkfont.Font(family=fam, size=15, weight="bold"),
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
        st.configure("App.Vertical.TScrollbar", background=C["card2"], troughcolor=C["bg"],
                     bordercolor=C["bg"], arrowcolor=C["muted"], lightcolor=C["card2"],
                     darkcolor=C["card2"], gripcount=0)
        st.map("App.Vertical.TScrollbar", background=[("active", C["border"])])
        st.configure("Card.Vertical.TScrollbar", background=C["card2"], troughcolor=C["card"],
                     bordercolor=C["card"], arrowcolor=C["muted"], lightcolor=C["card2"],
                     darkcolor=C["card2"], gripcount=0)
        st.map("Card.Vertical.TScrollbar", background=[("active", C["border"])])
        st.configure("Log.Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["text"],
                     bordercolor=C["card"], lightcolor=C["card"], darkcolor=C["card"],
                     rowheight=self.S(28), font=self.f["body"], borderwidth=0, relief="flat")
        st.map("Log.Treeview", background=[("selected", C["sel"])], foreground=[("selected", C["text"])])
        st.configure("Log.Treeview.Heading", background=C["card"], foreground=C["muted"], font=self.f["h"],
                     relief="flat", borderwidth=0, bordercolor=C["border"], lightcolor=C["card"],
                     darkcolor=C["card"], padding=(self.S(4), self.S(6)))
        st.map("Log.Treeview.Heading", background=[("active", C["card2"])])
        st.layout("Log.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    # ---- rakennuspalikat ---------------------------------------------------
    def card(self, parent, title: str, **pack):
        outer = tk.Frame(parent, bg=C["card"], highlightthickness=1, highlightbackground=C["border"])
        outer.pack(**{"fill": "x", **pack})
        head = tk.Label(outer, text=title.upper(), bg=C["card"], fg=C["muted"], font=self.f["h"])
        head.pack(anchor="w", padx=self.S(16), pady=(self.S(12), self.S(6)))
        body = tk.Frame(outer, bg=C["card"])
        body.pack(fill="both", expand=True, padx=self.S(16), pady=(0, self.S(14)))
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

    def button(self, parent, text: str, command, style: str = "secondary", bg: str = None) -> tk.Label:
        base = bg or C["card2"]
        cols = {"primary": (C["accent"], C["on_accent"], C["accent"], C["accent_hi"]),
                "secondary": (base, C["text"], C["border"], C["hover"]),
                "danger": (base, C["red"], C["border"], C["hover"])}[style]
        b = tk.Label(parent, text=text, bg=cols[0], fg=cols[1], font=self.f["bodyb" if style == "primary"
                                                                              else "small"],
                     padx=self.S(14), pady=self.S(6), cursor="hand2", highlightthickness=1,
                     highlightbackground=cols[2])
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: b.configure(bg=cols[3]))
        b.bind("<Leave>", lambda e: b.configure(bg=cols[0]))
        return b

    def later(self, name: str, ms: int, fn):
        """Viivästetty kutsu: uusi pyyntö samalla nimellä korvaa edellisen."""
        if self._jobs.get(name):
            self.after_cancel(self._jobs[name])
        self._jobs[name] = self.after(ms, lambda: (self._jobs.pop(name, None), fn()))

    def _wheel_passthrough(self, widget, scroller: "ScrollFrame"):
        """Rulla vierittää paneelia eikä vaihda pudotusvalikon arvoa."""
        for ev in WHEEL_EVENTS:
            widget.bind(ev, lambda e: (scroller.wheel(e), "break")[1])

    # ---- näkymä ------------------------------------------------------------
    def _build_all(self):
        if self.main is not None:
            self._capture_form()
            self.main.destroy()
        self.configure(bg=C["bg"])
        self._style()
        self.main = tk.Frame(self, bg=C["bg"])
        self.main.pack(fill="both", expand=True)
        self._build()
        self._restore_form()

    def _build(self):
        S = self.S
        head = tk.Frame(self.main, bg=C["bg"])
        head.pack(fill="x", padx=S(22), pady=(S(16), S(12)))
        tk.Frame(head, bg=C["accent"], width=S(5), height=S(38)).pack(side="left", padx=(0, S(12)))
        tt = tk.Frame(head, bg=C["bg"])
        tt.pack(side="left")
        tk.Label(tt, text=APP_NAME, bg=C["bg"], fg=C["text"], font=self.f["title"]).pack(anchor="w")
        tk.Label(tt, text="Nordnet-sijoitukset, opintolaina ja oma raha  ·  talletukset, kaupat, osingot "
                          "ja lainat yhdessä paikassa", bg=C["bg"], fg=C["muted"],
                 font=self.f["small"]).pack(anchor="w")
        self.theme_var = tk.StringVar(value=self.theme)
        Segmented(head, [("light", "Vaalea"), ("dark", "Tumma")], self.theme_var, self.sc, self.f["small"],
                  lambda: self.after_idle(self._theme_changed)).pack(side="right")
        self.undo_lbl = tk.Label(head, text="Kumoa", bg=C["bg"], fg=C["accent"], font=self.f["bodyb"],
                                 cursor="hand2")
        self.undo_lbl.bind("<Button-1>", lambda e: self.undo())
        self.status = tk.Label(head, text="", bg=C["bg"], fg=C["faint"], font=self.f["small"])
        self.status.pack(side="right", padx=(S(14), S(16)))

        body = tk.Frame(self.main, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=S(22), pady=(0, S(16)))
        self.left = ScrollFrame(body, C["bg"], S(430))
        self.left.pack(side="left", fill="y")
        self.right = ScrollFrame(body, C["bg"], autohide=True)
        self.right.pack(side="left", fill="both", expand=True, padx=(S(16), 0))
        self._build_inputs(self.left.inner)
        self._build_outputs(self.right.inner)
        self._show_saved_status()

    def _build_inputs(self, root):
        S = self.S
        pad = dict(pady=(0, S(12)), padx=(0, S(10)))
        acc_ids = [a["id"] for a in self.data["accounts"]]

        _, b, self.form_title = self.card(root, "Uusi kirjaus", **pad)
        self.kind = tk.StringVar(value="deposit")
        self.label(b, "Nordnet", fg=C["faint"], font="h").pack(anchor="w", pady=(0, S(4)))
        ChipGrid(b, NORDNET_KINDS, self.kind, self.sc, self.f["small"]).pack(fill="x")
        self.label(b, "Laina", fg=C["faint"], font="h").pack(anchor="w", pady=(S(10), S(4)))
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
        self.fv["loan"].set(self.data["loans"][0]["name"] if self.data["loans"] else "Opintolaina")
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
                self._wheel_passthrough(cb, self.left)
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
        self.form_info = self.note(b, fg=C["muted"])
        self.form_info.pack(anchor="w", fill="x", pady=(S(8), 0))
        btns = tk.Frame(b, bg=C["card"])
        btns.pack(fill="x", pady=(S(12), 0))
        self.btn_submit = self.button(btns, "Lisää kirjaus", self.submit, "primary")
        self.btn_submit.pack(side="left")
        self.btn_cancel = self.button(btns, "Tyhjennä", self.cancel_edit)
        self.btn_cancel.pack(side="left", padx=(S(8), 0))
        self.btn_delete = self.button(btns, "Poista", self.delete_current, "danger")
        for v in self.fv.values():
            v.trace_add("write", lambda *a: self.later("form", 80, self._update_form_info))
        self.kind.trace_add("write", lambda *a: self._show_fields())
        self._show_fields()

        _, b, _ = self.card(root, "Muistiinpanot", **pad)
        self.notes = tk.Text(b, height=6, wrap="word", bg=C["input"], fg=C["text"], insertbackground=C["text"],
                             relief="flat", font=self.f["body"], highlightthickness=1,
                             highlightbackground=C["border"], highlightcolor=C["accent"],
                             selectbackground=C["accent"], selectforeground=C["on_accent"],
                             padx=S(8), pady=S(6), undo=True)
        self.notes.pack(fill="x")
        self.notes.insert("1.0", self.data.get("notes", ""))
        self.notes.bind("<KeyRelease>", lambda e: self.later("notes", 800, self._save_notes))

        _, b, _ = self.card(root, "Tilit ja asetukset", **pad)
        self.label(b, "Tili", font="small").grid(row=0, column=0, sticky="w")
        self.label(b, "Nimi", font="small").grid(row=0, column=1, sticky="w", padx=(S(8), 0))
        self.label(b, "Numero", font="small").grid(row=0, column=2, sticky="w", padx=(S(8), 0))
        self.acc_vars = []
        for r, acc in enumerate(self.data["accounts"], start=1):
            self.label(b, acc["id"], fg=C["text"], font="bodyb").grid(row=r, column=0, sticky="w", pady=S(3))
            nv, numv = tk.StringVar(value=acc.get("name", "")), tk.StringVar(value=acc.get("number", ""))
            self.entry(b, nv, width=20, justify="left").grid(row=r, column=1, sticky="we", padx=(S(8), 0),
                                                            ipady=S(2))
            self.entry(b, numv, width=10, justify="left").grid(row=r, column=2, sticky="we", padx=(S(8), 0),
                                                              ipady=S(2))
            for v in (nv, numv):
                v.trace_add("write", lambda *a: self._accounts_changed())
            self.acc_vars.append((acc, nv, numv))
        b.columnconfigure(1, weight=1)
        fxrow = tk.Frame(b, bg=C["card"])
        fxrow.grid(row=len(self.data["accounts"]) + 1, column=0, columnspan=3, sticky="we", pady=(S(10), 0))
        self.label(fxrow, "Varakurssi arvioihin: 1 EUR =").pack(side="left")
        self.label(fxrow, "USD", fg=C["faint"], font="small").pack(side="right", padx=(S(6), 0))
        self.fx_var = tk.StringVar(value=fmt_input(self.data["settings"]["fx"].get("USD", 1.12)))
        self.fx_entry = self.entry(fxrow, self.fx_var, width=8)
        self.fx_entry.pack(side="right", ipady=S(2))
        self.fx_var.trace_add("write", lambda *a: self.later("fx", 400, self._fx_changed))
        self.fx_info = self.note(b)
        self.fx_info.grid(row=len(self.data["accounts"]) + 2, column=0, columnspan=3, sticky="w", pady=(S(6), 0))
        self.label(b, "Tietotiedosto", font="small").grid(row=len(self.data["accounts"]) + 3, column=0,
                                                          columnspan=3, sticky="w", pady=(S(10), 0))
        self.note(b, self.store.path, fg=C["faint"]).grid(row=len(self.data["accounts"]) + 4, column=0,
                                                         columnspan=3, sticky="w")
        row = tk.Frame(b, bg=C["card"])
        row.grid(row=len(self.data["accounts"]) + 5, column=0, columnspan=3, sticky="w", pady=(S(8), 0))
        self.button(row, "Avaa kansio", self.open_folder).pack(side="left")
        self.button(row, "Vie CSV-tiedostoon", self.export_csv).pack(side="left", padx=(S(8), 0))

        tk.Label(root, text="Tiedot tallentuvat vain tälle koneelle, ja jokaisesta päivästä jää "
                            "varmuuskopio. Arvio-merkityt (≈) summat lasketaan valuuttakurssista – täydennä "
                            "tarkka summa Nordnetin tapahtumista. Ei sijoitusneuvontaa.",
                 bg=C["bg"], fg=C["faint"], font=self.f["tiny"], justify="left",
                 wraplength=self.S(400)).pack(anchor="w", padx=(S(2), S(10)), pady=(0, S(8)))

    def _build_outputs(self, root):
        S = self.S
        grid = tk.Frame(root, bg=C["bg"])
        grid.pack(fill="x")
        self.tiles: Dict[str, Tuple[tk.Label, tk.Label]] = {}
        for i, (key, title, col) in enumerate(TILES):
            r, c = divmod(i, 4)
            t = tk.Frame(grid, bg=C["card"], highlightthickness=1, highlightbackground=C["border"])
            t.grid(row=r, column=c, sticky="nsew", padx=(0 if c == 0 else S(10), 0),
                   pady=(0 if r == 0 else S(10), 0))
            top = tk.Frame(t, bg=C["card"])
            top.pack(fill="x", padx=S(14), pady=(S(10), 0))
            if col:
                tk.Frame(top, bg=C[col], width=S(8), height=S(8)).pack(side="left", padx=(0, S(7)))
            self.label(top, title, font="small").pack(side="left")
            v = self.label(t, "–", fg=C["text"], font="kpi")
            v.pack(anchor="w", padx=S(14))
            sub = self.label(t, "", font="tiny", justify="left", anchor="w")
            sub.pack(anchor="w", fill="x", padx=S(14), pady=(0, S(10)))
            sub.bind("<Configure>", lambda e, l=sub: l.configure(wraplength=max(80, e.width - 4)))
            self.tiles[key] = (v, sub)
        for c in range(4):
            grid.columnconfigure(c, weight=1, uniform="tile")

        bar = tk.Frame(root, bg=C["bg"])
        bar.pack(fill="x", pady=(S(14), S(10)))
        self.tab_var = tk.StringVar(value=self._tab)
        Segmented(bar, [("overview", "Yleiskuva"), ("log", "Tapahtumat"), ("hold", "Omistukset")],
                  self.tab_var, self.sc, self.f["body"], self._show_tab).pack(side="left")
        self.pages: Dict[str, tk.Frame] = {}

        # --- yleiskuva: rahan lähteet, lainat ja kuvaaja ---
        pg = self.pages["overview"] = tk.Frame(root, bg=C["bg"])
        row = tk.Frame(pg, bg=C["bg"])
        row.pack(fill="x")
        cols = [tk.Frame(row, bg=C["bg"]) for _ in range(3)]
        for c, fr in enumerate(cols):
            fr.grid(row=0, column=c, sticky="nsew", padx=(S(12) if c else 0, 0))
        row.columnconfigure(0, weight=1, uniform="src")
        row.columnconfigure(1, weight=1, uniform="src")
        row.rowconfigure(0, weight=1)
        _, b, _ = self.card(cols[0], "Mistä raha on tullut", fill="both", expand=True)
        self.bar_src = StackBar(b, self.sc)
        self.bar_src.pack(fill="x", pady=(S(2), S(8)))
        self.leg_src = tk.Frame(b, bg=C["card"])
        self.leg_src.pack(fill="x")
        _, self.loan_box, _ = self.card(cols[2], "Lainat", fill="both", expand=True)
        _, b, _ = self.card(cols[1], "Missä raha on nyt", fill="both", expand=True)
        self.bar_use = StackBar(b, self.sc)
        self.bar_use.pack(fill="x", pady=(S(2), S(8)))
        self.leg_use = tk.Frame(b, bg=C["card"])
        self.leg_use.pack(fill="x")
        self.src_note = self.note(b, wrap=200)
        self.src_note.pack(anchor="w", fill="x", pady=(S(6), 0))
        self.src_note.bind("<Configure>", lambda e: self.src_note.configure(wraplength=max(120, e.width - 4)))

        _, b, _ = self.card(pg, "Kehitys", fill="both", expand=True, pady=(S(12), 0))
        leg = tk.Frame(b, bg=C["card"])
        leg.pack(fill="x", pady=(0, S(6)))
        for name, col, style in Chart.LEGEND:
            sym = {"area": "■", "line": "━", "dash": "╌╌"}[style]
            self.label(leg, sym, fg=C[col], font="small").pack(side="left", padx=(0, S(4)))
            self.label(leg, name, font="small").pack(side="left", padx=(0, S(14)))
        self.chart = Chart(b, self.sc, self.f)
        self.chart.configure(height=S(300))
        self.chart.pack(fill="both", expand=True)

        # --- tapahtumat ---
        pg = self.pages["log"] = tk.Frame(root, bg=C["bg"])
        _, b, _ = self.card(pg, "Tapahtumat", fill="both", expand=True)
        top = tk.Frame(b, bg=C["card"])
        top.pack(fill="x", pady=(0, S(8)))
        self.filter_var = tk.StringVar(value=self._filter)
        Segmented(top, ["Kaikki", "Nordnet", "Lainat"], self.filter_var, self.sc, self.f["small"],
                  lambda: self.refresh_tree()).pack(side="left")
        self.count_lbl = self.label(top, "", font="small")
        self.count_lbl.pack(side="right")
        self.label(top, "Muokkaa kirjausta klikkaamalla riviä.", fg=C["faint"], font="small").pack(
            side="right", padx=(0, S(14)))
        self.tree_holder = tk.Frame(b, bg=C["card"], height=S(360))
        self.tree_holder.pack(fill="x")
        self.tree_holder.pack_propagate(False)
        self.tree = ttk.Treeview(self.tree_holder, columns=[c[0] for c in TREE_COLS], show="headings",
                                 style="Log.Treeview", selectmode="browse")
        for key, title, w, anchor in TREE_COLS:
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=S(w), minwidth=S(80 if key == "desc" else 40), anchor=anchor,
                             stretch=key == "desc")
        tsb = ttk.Scrollbar(self.tree_holder, orient="vertical", command=self.tree.yview,
                            style="Card.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("odd", background=C["card2"])
        self.tree.tag_configure("est", foreground=C["muted"])
        self.tree.bind("<<TreeviewSelect>>", self._tree_select)
        for ev in WHEEL_EVENTS:
            self.tree.bind(ev, self._tree_wheel)

        # --- omistukset ---
        pg = self.pages["hold"] = tk.Frame(root, bg=C["bg"])
        _, b, _ = self.card(pg, "Omistukset (hankinta-arvo)", fill="x")
        self.hold_box = tk.Frame(b, bg=C["card"])
        self.hold_box.pack(fill="x")
        _, b, _ = self.card(pg, "Tuotot ja kulut", fill="x", pady=(S(12), 0))
        self.stats_box = tk.Frame(b, bg=C["card"])
        self.stats_box.pack(fill="x")
        self._show_tab()

    def _show_tab(self):
        tab = self._tab = self.tab_var.get()
        for k, pg in self.pages.items():
            if k != tab:
                pg.pack_forget()
        self.pages[tab].pack(fill="both", expand=True)
        grow = {"overview": (self.chart, self.S(260)), "log": (self.tree_holder, self.S(280))}.get(tab)
        self.right.grow, self.right.grow_min = grow if grow else (None, 0)
        self.right.canvas.yview_moveto(0)
        self.right._schedule_fit()

    def _tree_wheel(self, e):
        if self.tree.yview() != (0.0, 1.0):
            self.tree.yview_scroll(wheel_step(e), "units")
            return "break"
        return None

    # ---- teema -------------------------------------------------------------
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

    # ---- lomake ------------------------------------------------------------
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
        """Lukee lomakkeen. Palauttaa kirjauksen, virheelliset kentät ja virheviestin."""
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
                msg = "Valitse eri tilit siirron lähteeksi ja kohteeksi."
        if "security" in fields:
            e["security"] = self.fv["security"].get().strip()
            if not e["security"] and kind in ("buy", "sell"):
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
            e["amount"] = num("amount", required=False)   # None = arvioidaan
        else:
            e["amount"] = num("amount", allow_zero=kind in ("value", "loan_pay"))
        if "loan_part" in fields:
            e["loan_part"] = num("loan_part", required=False) or 0.0
            if e.get("amount") is not None and e["loan_part"] > e["amount"] + 1e-9:
                bad.append("loan_part")
                msg = "Opintolainan osuus ei voi olla suurempi kuin summa."
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
            msg = "Tarkista punaiset kentät."
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
                txt, col = "Opintolainan osuus ei voi olla suurempi kuin summa.", C["red"]
            else:
                txt = f"Opintolainaa {fmt_eur(lp)}  ·  omaa rahaa {fmt_eur(a - lp)}"
        elif kind in ("buy", "sell"):
            q, p, fees, ccy = num("qty"), num("price"), num("fees") or 0.0, fv["currency"].get() or "EUR"
            acc = fv["account"].get()
            if q and p:
                gross = q * p
                if a:
                    eur = a
                    fx = (a - fees) / gross if kind == "buy" else (a + fees) / gross
                    txt = f"Kaupan arvo {fmt_num(gross, 2)} {ccy}"
                    if ccy != "EUR" and fx > 0:
                        txt += f"  ·  1 EUR = {fmt_num(1 / fx, 4)} {ccy} (sis. valuutanvaihtokulun)"
                else:
                    rate = s.rates.get(ccy, 1.0)
                    eur = gross * rate + (fees if kind == "buy" else -fees)
                    txt = f"Summa ≈ {fmt_eur(eur)}"
                    if ccy != "EUR":
                        txt += f" (1 EUR ≈ {fmt_num(1 / rate, 4)} {ccy}, {s.rate_src.get(ccy, 'varakurssi')})"
                    txt += ". Täydennä tarkka summa, kun kauppa näkyy Nordnetin tapahtumissa."
                cash = s.cash.get(acc, 0.0)
                old = next((x for x in self.data["entries"] if x["id"] == self.editing), None)
                if old and old["kind"] in ("buy", "sell") and old.get("account") == acc:
                    oe = s.amounts.get(old["id"], (0.0, False))[0]
                    cash += oe if old["kind"] == "buy" else -oe
                after = cash - eur if kind == "buy" else cash + eur
                txt += f"\nKäteistä jää {acc}-tilille {'≈ ' if not a or s.estimates else ''}{fmt_eur(after)}."
                if after < -0.005:
                    col = C["red"]
        elif kind == "transfer":
            if fv["account"].get() == fv["to_account"].get():
                txt, col = "Valitse eri tilit siirron lähteeksi ja kohteeksi.", C["red"]
            else:
                txt = f"Käteistä {fv['account'].get()}-tilillä nyt {fmt_eur(s.cash.get(fv['account'].get(), 0))}."
        elif kind == "value" and a is not None:
            acc = fv["account"].get()
            net = s.acc_net.get(acc, 0.0)
            book = s.cash.get(acc, 0.0) + s.acc_cost.get(acc, 0.0)
            txt = (f"{acc}: nettotalletukset {fmt_eur(net)} → tuotto {fmt_eur(a - net, sign=True)}"
                   f"{' (' + fmt_pct((a - net) / net) + ')' if net > 0 else ''}.\n"
                   f"Hankinta-arvo + käteinen {fmt_eur(book)}.")
        elif kind == "loan_pay":
            tot = (a or 0) + (num("interest") or 0) + (num("fees") or 0)
            if tot:
                txt = f"Pankille yhteensä {fmt_eur(tot)}."
        elif kind == "loan_draw" and a:
            txt = "Muista merkitä talletuksiin, paljonko lainasta siirrät Nordnetiin."
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
            msg = "Muutokset tallennettu."
        else:
            e["id"] = new_id()
            self.data["entries"].append(e)
            msg = f"{KIND_TABLE[e['kind']]} lisätty."
        if e.get("loan") and e["loan"] not in [ln["name"] for ln in self.data["loans"]]:
            self.data["loans"].append({"name": e["loan"]})
        self._save()
        self._reset_form()
        self.refresh()
        self.flash(msg)

    def _reset_form(self):
        """Tyhjentää summat; päivä, tili, laji, valuutta ja kulut jäävät seuraavaa kirjausta varten."""
        for k in ("amount", "loan_part", "qty", "price", "interest", "note", "security"):
            self.fv[k].set("")
        self.editing = None
        self._set_edit_mode(False)
        self._clear_marks()
        if self.tree.selection():
            self.tree.selection_remove(*self.tree.selection())

    def _set_edit_mode(self, on: bool):
        self.form_title.configure(text="MUOKKAA KIRJAUSTA" if on else "UUSI KIRJAUS",
                                  fg=C["accent"] if on else C["muted"])
        self.btn_submit.configure(text="Tallenna muutokset" if on else "Lisää kirjaus")
        self.btn_cancel.configure(text="Peruuta" if on else "Tyhjennä")
        if on:
            self.btn_delete.pack(side="right")
        else:
            self.btn_delete.pack_forget()

    def cancel_edit(self):
        self._reset_form()
        self._update_form_info()

    def _load_entry(self, eid: str):
        e = next((x for x in self.data["entries"] if x["id"] == eid), None)
        if e is None:
            return
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
        self.left.canvas.yview_moveto(0)

    def delete_current(self):
        idx = next((i for i, x in enumerate(self.data["entries"]) if x["id"] == self.editing), None)
        if idx is None:
            return
        e = self.data["entries"].pop(idx)
        self._undo = (idx, e)
        self._save()
        self._reset_form()
        self.refresh()
        self.flash(f"{KIND_TABLE[e['kind']]} {fmt_date(e['date'])} poistettu.", undo=True)

    def undo(self):
        if not self._undo:
            return
        idx, e = self._undo
        self._undo = None
        self.data["entries"].insert(min(idx, len(self.data["entries"])), e)
        self._save()
        self.refresh()
        self.flash("Poisto kumottu.")

    def _tree_select(self, _e=None):
        sel = self.tree.selection()
        if sel and sel[0] != self.editing:
            self._load_entry(sel[0])

    # ---- asetukset ja tallennus --------------------------------------------
    def _accounts_changed(self):
        for acc, nv, numv in self.acc_vars:
            acc["name"], acc["number"] = nv.get().strip(), numv.get().strip()
        self.later("save", 700, self._save)

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
        if self.store.exists and os.path.exists(self.store.path):
            t = dt.datetime.fromtimestamp(os.path.getmtime(self.store.path))
            when = f"{t.hour}.{t.minute:02d}" if t.date() == dt.date.today() else fmt_date(t.date())
            self.status.configure(text=f"Tallennettu {when}", fg=C["faint"])
        else:
            self.status.configure(text="Ei vielä tallennettuja tietoja", fg=C["faint"])
        self.undo_lbl.pack_forget()

    def flash(self, msg: str, bad: bool = False, undo: bool = False, sticky: bool = False):
        self.status.configure(text=msg, fg=C["red"] if bad else C["text"])
        if undo:
            self.undo_lbl.pack(side="right", before=self.status)
        else:
            self.undo_lbl.pack_forget()
        if not sticky:
            self.later("flash", 8000 if undo else 4000, self._show_saved_status)

    def open_folder(self):
        folder = os.path.dirname(self.store.path)
        os.makedirs(folder, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except OSError as ex:
            self.flash(f"Kansiota ei voitu avata: {ex}", bad=True)

    def export_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Vie kirjaukset", defaultextension=".csv",
            initialfile=f"salkkukartta-{dt.date.today().isoformat()}.csv",
            filetypes=[("CSV (Excel)", "*.csv"), ("Kaikki tiedostot", "*.*")])
        if not path:
            return
        s = self.summary

        def n(x, dec=2):
            return "" if x is None else fmt_input(round(x, dec) if dec is not None else x)

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(["Päivä", "Laji", "Tili", "Kohdetili", "Laina", "Arvopaperi", "Määrä", "Kurssi",
                            "Valuutta", "Kulut", "Summa EUR", "Arvio", "Opintolainaa", "Korko", "Muistiinpano"])
                for e in sorted_entries(self.data):
                    amt, est = e.get("amount"), False
                    if e["kind"] in ("buy", "sell"):
                        amt, est = s.amounts.get(e["id"], (amt, False))
                    w.writerow([fmt_date(e["date"]), KIND_TABLE[e["kind"]], e.get("account", ""),
                                e.get("to_account", ""), e.get("loan", ""), e.get("security", ""),
                                n(e.get("qty"), None), n(e.get("price"), None), e.get("currency", ""),
                                n(e.get("fees")), n(amt), "kyllä" if est else "", n(e.get("loan_part")),
                                n(e.get("interest")), e.get("note", "")])
            self.flash(f"Viety: {os.path.basename(path)}")
        except OSError as ex:
            self.flash(f"Vienti epäonnistui: {ex}", bad=True)

    def _close(self):
        """Odottavat tallennukset tehdään ennen sulkemista."""
        pending = False
        for name in ("notes", "save", "fx"):
            job = self._jobs.pop(name, None)
            if job:
                self.after_cancel(job)
                pending = True
                if name == "fx":
                    self._fx_changed()
        text = self.notes.get("1.0", "end-1c")
        if text != self.data.get("notes", ""):
            self.data["notes"] = text
            pending = True
        if pending:
            self.store.save()
        self.destroy()

    # ---- päivitys ----------------------------------------------------------
    def refresh(self):
        self.summary = s = ledger(self.data)
        self._refresh_tiles(s)
        self._refresh_sources(s)
        self._refresh_loans(s)
        self.refresh_tree()
        self._refresh_holdings(s)
        self.chart.set_data(s)
        secs = sorted({e.get("security", "") for e in self.data["entries"] if e.get("security")})
        self.fcombo["security"].configure(values=secs)
        self.fcombo["loan"].configure(values=[ln["name"] for ln in self.data["loans"]])
        usd = s.rate_src.get("USD", "varakurssi")
        self.fx_info.configure(
            text=f"Arvioissa käytetään nyt 1 EUR = {fmt_num(1 / s.rates.get('USD', 1), 4)} USD "
                 f"({'viimeisin kauppa: ' + usd if usd != 'varakurssi' else 'varakurssi'}). "
                 "Varakurssia käytetään vain, jos vahvistettua USD-kauppaa ei ole.")
        self._update_form_info()

    def _tile(self, key: str, value: str, sub: str = "", fg: str = None, subfg: str = None):
        v, sl = self.tiles[key]
        v.configure(text=value, fg=fg or C["text"])
        sl.configure(text=sub, fg=subfg or C["muted"])

    def _refresh_tiles(self, s: Summary):
        est = "≈ " if s.estimates else ""
        net = s.net_in
        dep = f"talletettu {fmt_eur(s.deposited)}"
        if s.withdrawn > 0.005:
            dep += f" · nostettu {fmt_eur(s.withdrawn)}"
        self._tile("net", fmt_eur(net), dep)
        share = (lambda x: f"{fmt_pct(x / net)} talletuksista") if net > 0.005 else (lambda x: "")
        self._tile("loan", fmt_eur(s.loan_in), share(s.loan_in))
        self._tile("own", fmt_eur(s.own_in), share(s.own_in))
        loans = [ln for ln in s.loans.values() if ln.active]
        if loans:
            drawn = sum(ln.drawn for ln in loans)
            cap = sum(ln.capitalized for ln in loans)
            sub = f"nostettu {fmt_eur(drawn)}" + (f" · korot {fmt_eur(cap)}" if cap > 0.005 else "")
            self._tile("debt", fmt_eur(s.loan_balance), sub)
        else:
            self._tile("debt", fmt_eur(0), "ei lainakirjauksia")
        nsec = len({h.security for h in s.holdings})
        self._tile("cost", est + fmt_eur(s.cost_total),
                   f"{nsec} arvopaperia · kaupankäyntikulut {fmt_eur(s.trade_fees)}" if nsec else "ei omistuksia")
        neg = any(v < -0.005 for v in s.cash.values())
        shown = [(a, v) for a, v in s.cash.items() if abs(v) > 0.005] or list(s.cash.items())[:1]
        parts = " · ".join(f"{a} {fmt_eur(v)}" for a, v in shown)
        self._tile("cash", est + fmt_eur(s.cash_total),
                   "Saldo negatiivinen – tarkista kirjaukset" if neg else parts,
                   fg=C["red"] if neg else None, subfg=C["red"] if neg else None)
        if s.value is not None:
            gain = s.value - net
            self._tile("value", fmt_eur(s.value),
                       f"{fmt_eur(gain, sign=True)}{' (' + fmt_pct(gain / net) + ')' if net > 0 else ''} "
                       f"vs. talletukset · arvo {fmt_date(s.value_date)}",
                       subfg=C["good"] if gain >= 0 else C["red"])
        else:
            self._tile("value", "–", "Kirjaa tilin arvo Nordnetistä (Arvo), niin näet tuoton.")
        base = s.value if s.value is not None else s.book_value
        self._tile("worth", ("" if s.value is not None else est) + fmt_eur(base - s.loan_balance),
                   "salkun arvo − lainat" if s.value is not None else "hankinta-arvo + käteinen − lainat")

    def _legend(self, frame: tk.Frame, rows: List[Tuple[str, float, str]], total: float):
        for w in frame.winfo_children():
            w.destroy()
        S = self.S
        for r, (name, v, col) in enumerate(rows):
            tk.Frame(frame, bg=C[col], width=S(9), height=S(9)).grid(row=r, column=0, padx=(0, S(8)))
            self.label(frame, name, fg=C["text"], font="small").grid(row=r, column=1, sticky="w", pady=S(1))
            self.label(frame, fmt_eur(v), fg=C["text"], font="small").grid(row=r, column=2, sticky="e")
            self.label(frame, fmt_pct(v / total) if total > 0.005 else "", font="small", width=6,
                       anchor="e").grid(row=r, column=3, sticky="e")
        frame.columnconfigure(1, weight=1)

    def _refresh_sources(self, s: Summary):
        self.bar_src.set([(s.loan_in, C["loan"]), (s.own_in, C["own"])])
        self._legend(self.leg_src, [("Opintolaina", s.loan_in, "loan"), ("Oma raha", s.own_in, "own")],
                     max(0.0, s.loan_in) + max(0.0, s.own_in))
        stocks = s.cost_total - s.holding_fees
        fees = s.trade_fees + s.other_fees
        cash = max(0.0, s.cash_total)
        self.bar_use.set([(stocks, C["invest"]), (fees, C["debt"]), (cash, C["cash"])])
        self._legend(self.leg_use, [("Arvopaperit", stocks, "invest"),
                                    ("Kulut ja verot", fees, "debt"), ("Käteinen", cash, "cash")],
                     max(0.0, stocks) + fees + cash)
        notes = []
        if s.estimates:
            notes.append(f"≈ {s.estimates} kaupan summa on arvio – täydennä Summa EUR Nordnetin tapahtumista.")
        if s.dividends > 0.005 or abs(s.realized) > 0.005:
            notes.append(f"Osinkoja {fmt_eur(s.dividends)}, toteutuneita voittoja {fmt_eur(s.realized, sign=True)}.")
        self.src_note.configure(text="\n".join(notes))
        if not notes:
            self.src_note.pack_forget()
        elif not self.src_note.winfo_ismapped():
            self.src_note.pack(anchor="w", fill="x", pady=(self.S(6), 0))

    def _refresh_loans(self, s: Summary):
        box = self.loan_box
        for w in box.winfo_children():
            w.destroy()
        S = self.S
        loans = [ln for ln in s.loans.values() if ln.active]
        if not loans:
            self.note(box, "Ei lainakirjauksia. Kirjaa lainaerä valitsemalla\nLaina → Nosto.", wrap=260).pack(
                anchor="w")
            return
        r = 0
        for ln in loans:
            self.label(box, ln.name, fg=C["text"], font="bodyb").grid(row=r, column=0, columnspan=2, sticky="w",
                                                                     pady=(S(8) if r else 0, S(2)))
            r += 1
            rows = [("Nostettu", ln.drawn), ("Pääomitetut korot", ln.capitalized),
                    ("Lyhennetty", -ln.principal), ("Hyvitys (Kela)", -ln.credit)]
            for name, v in rows:
                if abs(v) > 0.005 or name == "Nostettu":
                    self.label(box, name, font="small").grid(row=r, column=0, sticky="w")
                    self.label(box, fmt_eur(v), fg=C["text"], font="small").grid(row=r, column=1, sticky="e",
                                                                                padx=(S(24), 0))
                    r += 1
            tk.Frame(box, bg=C["border"], height=1).grid(row=r, column=0, columnspan=2, sticky="we", pady=S(3))
            r += 1
            self.label(box, "Saldo", fg=C["text"], font="bodyb").grid(row=r, column=0, sticky="w")
            self.label(box, fmt_eur(ln.balance), fg=C["debt"], font="bodyb").grid(row=r, column=1, sticky="e")
            r += 1
            if ln.interest > 0.005 or ln.fees > 0.005:
                self.label(box, "Korkoja ja kuluja maksettu", font="small").grid(row=r, column=0, sticky="w")
                self.label(box, fmt_eur(ln.interest + ln.fees), fg=C["text"], font="small").grid(
                    row=r, column=1, sticky="e")
                r += 1
        drawn = sum(ln.drawn for ln in loans)
        if drawn > 0.005:
            self.note(box, f"Nordnetiin talletettu {fmt_eur(s.loan_in)} eli {fmt_pct(s.loan_in / drawn)} "
                           f"nostetusta lainasta.", wrap=260).grid(row=r, column=0, columnspan=2, sticky="w",
                                                                    pady=(S(8), 0))

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
            price = f"{fmt_price(e.get('price') or 0)} {e.get('currency', '')}"
            eur, est = s.amounts.get(e["id"], (amt, False))
            summa = ("≈ " if est else "") + fmt_num(-eur if k == "buy" else eur, 2)
        elif k == "loan_pay":
            summa = fmt_num(-(amt + float(e.get("interest") or 0) + float(e.get("fees") or 0)), 2)
        else:
            summa = fmt_num(KIND_SIGN.get(k, 0) * amt if KIND_SIGN.get(k, 0) else amt, 2)
        if k in ("buy", "sell", "loan_pay") and e.get("fees"):
            fees = fmt_num(e["fees"], 2)
        if k in ("deposit", "withdrawal"):
            lp = float(e.get("loan_part") or 0.0)
            own = amt - lp
            if lp > 0.005 and own > 0.005:
                fund = f"laina {fmt_money_short(lp)} · oma {fmt_money_short(own)}"
            else:
                fund = "opintolaina" if lp > 0.005 else "oma raha"
        elif k == "loan_pay" and e.get("interest"):
            fund = f"lyhennys {fmt_num(amt, 2)} · korko {fmt_num(e['interest'], 2)}"
        return (fmt_date(e["date"]), acc, KIND_TABLE[k], desc, qty, price, fees, summa, fund)

    def refresh_tree(self):
        s = self.summary
        flt = self.filter_var.get()
        self.tree.delete(*self.tree.get_children())
        ents = self.data["entries"]
        order = sorted(range(len(ents)), key=lambda i: (ents[i]["date"], i), reverse=True)
        n = 0
        for i in order:
            e = ents[i]
            is_loan = e["kind"] in LOAN_KIND_KEYS
            if (flt == "Nordnet" and is_loan) or (flt == "Lainat" and not is_loan):
                continue
            tags = ["odd"] if n % 2 else []
            if e["kind"] in ("buy", "sell") and s.amounts.get(e["id"], (0, False))[1]:
                tags.append("est")
            self.tree.insert("", "end", iid=e["id"], values=self._row_values(e, s), tags=tags)
            n += 1
        self.count_lbl.configure(text=f"{n} tapahtumaa")
        if self.editing and self.tree.exists(self.editing):
            self.tree.selection_set(self.editing)
            self.tree.see(self.editing)

    def _refresh_holdings(self, s: Summary):
        S = self.S
        for box in (self.hold_box, self.stats_box):
            for w in box.winfo_children():
                w.destroy()
        if not s.holdings:
            self.note(self.hold_box, "Ei omistuksia vielä. Kirjaa osto valitsemalla Nordnet → Osto.").pack(anchor="w")
        else:
            heads = ["Arvopaperi", "Tili", "Kpl", "Keskihinta", "Kulut", "Hankinta-arvo", "Osuus"]
            box = self.hold_box
            for c, h in enumerate(heads):
                self.label(box, h, font="bodyb", anchor="e" if c >= 2 else "w").grid(
                    row=0, column=c, sticky="we", padx=(S(10) if c else 0, 0), pady=(0, S(4)))
            total = sum(h.cost for h in s.holdings)
            for r, h in enumerate(s.holdings, start=1):
                bg = C["card2"] if r % 2 else C["card"]
                vals = [h.security, h.account, fmt_qty(h.qty), f"{fmt_price(h.avg_price)} {h.currency}",
                        fmt_eur(h.fees), ("≈ " if h.estimated else "") + fmt_eur(h.cost),
                        fmt_pct(h.cost / total) if total > 0 else ""]
                for c, v in enumerate(vals):
                    tk.Label(box, text=v, bg=bg, fg=C["muted"] if (c == 5 and h.estimated) else C["text"],
                             font=self.f["body"], anchor="e" if c >= 2 else "w").grid(
                        row=r, column=c, sticky="nsew", ipadx=S(5), ipady=S(3))
            r = len(s.holdings) + 1
            tk.Frame(box, bg=C["border"], height=1).grid(row=r, column=0, columnspan=len(heads), sticky="we",
                                                         pady=(S(2), S(2)))
            for c, v in ((0, "Yhteensä"), (4, fmt_eur(s.holding_fees)),
                         (5, ("≈ " if s.estimates else "") + fmt_eur(total))):
                self.label(box, v, fg=C["text"], font="bodyb", anchor="e" if c >= 2 else "w").grid(
                    row=r + 1, column=c, sticky="we", ipadx=S(5))
            box.columnconfigure(0, weight=3)
            for c in range(1, len(heads)):
                box.columnconfigure(c, weight=1)
            if s.estimates:
                self.note(box, "≈ = summa arvioitu valuuttakurssista, koska Summa EUR puuttuu.", wrap=600).grid(
                    row=r + 2, column=0, columnspan=len(heads), sticky="w", pady=(S(8), 0))
        stats = [("Osingot (netto)", s.dividends), ("Toteutuneet voitot ja tappiot", s.realized),
                 ("Kaupankäyntikulut", -s.trade_fees), ("Muut kulut ja verot", -s.other_fees)]
        for r, (name, v) in enumerate(stats):
            self.label(self.stats_box, name).grid(row=r, column=0, sticky="w", pady=S(2))
            self.label(self.stats_box, fmt_eur(v, sign=True), fg=C["text"]).grid(row=r, column=1, sticky="e")
        self.stats_box.columnconfigure(0, weight=1)


def main():
    path = DATA_FILE
    if "--tiedosto" in sys.argv:
        i = sys.argv.index("--tiedosto")
        if i + 1 < len(sys.argv):
            path = os.path.abspath(sys.argv[i + 1])
    try:  # terävä teksti Windowsin skaalatuilla näytöillä
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        # Oma tehtäväpalkkikuvake Pythonin logon sijaan (ks. myös set_window_app_id)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass
    App(path).mainloop()


if __name__ == "__main__":
    main()
