# -*- coding: utf-8 -*-
"""
Portfolio Manager data model and calculations (no UI).

- Store: JSON file with atomic saves and a daily backup
- ledger(): walks the entries in date order -> balances, holdings, daily series
- valuate() / value_history() / intraday_values(): market value from quotes (prices.py)
- formatting and input parsing (English display, accepts Finnish-style input too)
"""

import datetime as dt
import json
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


def resource_path(*parts: str) -> str:
    """Path inside the app folder (e.g. icon/icon-256.png)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)


APP_NAME = "Portfolio Manager"
DATA_FILE = resource_path("data", "portfolio.json")
# where versions before the English file names kept the data (moved to data/ on first start)
LEGACY_DATA_DIR = resource_path("tiedot")
BACKUPS_KEPT = 30
MIN_DAYS = 14          # the timeline covers at least two weeks

# ---------------------------------------------------------------------------
#  Entry kinds
# ---------------------------------------------------------------------------

NORDNET_KINDS = [("deposit", "Deposit"), ("buy", "Buy"), ("sell", "Sell"), ("dividend", "Dividend"),
                 ("withdrawal", "Withdraw"), ("transfer", "Transfer"), ("fee", "Fee"), ("transfer_in", "Shares in")]
LOAN_KINDS = [("loan_draw", "Drawdown"), ("loan_cap", "Interest cap."), ("loan_pay", "Payment"),
              ("loan_credit", "Kela credit")]
LOAN_KIND_KEYS = {k for k, _ in LOAN_KINDS}

KIND_TABLE = {
    "deposit": "Deposit", "withdrawal": "Withdrawal", "transfer": "Transfer", "buy": "Buy", "sell": "Sell",
    "dividend": "Dividend", "fee": "Fee", "transfer_in": "Shares in", "loan_draw": "Loan drawdown",
    "loan_cap": "Capitalized int.", "loan_pay": "Loan payment", "loan_credit": "Loan credit",
}
# sign of the amount in the transactions table (0 = shown without a sign)
KIND_SIGN = {"deposit": 1, "withdrawal": -1, "transfer": 0, "dividend": 1, "fee": -1, "transfer_in": 0,
             "loan_draw": 1, "loan_cap": 0, "loan_credit": 0}

CURRENCIES = ["USD", "EUR", "SEK", "NOK", "DKK", "CAD", "GBP"]
# Fallback rates (1 EUR = x) for estimates, used only when a currency has no confirmed trade
# and no live rate.
FX_DEFAULT = {"USD": 1.12, "SEK": 11.2, "NOK": 11.7, "DKK": 7.46, "CAD": 1.58, "GBP": 0.86}
# Nordnet's currency exchange fee, added to the mid rate when a trade's EUR amount is estimated
FX_FEE = 0.0025


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def default_data() -> dict:
    return {
        "version": 3,
        "accounts": [
            {"id": "OST", "name": "Equity savings account", "number": ""},
            {"id": "AOT", "name": "Stock & fund portfolio", "number": ""},
        ],
        "loans": [{"name": "Student loan"}],
        "securities": {},          # name -> {"symbol": "NVDA", "exchange": "NMS"}
        # holdings outside Nordnet without purchase history: platform, name, symbol, qty, avg cost, currency
        "external": [],
        "entries": [],
        "notes": "",
        "settings": {"theme": "", "fx": dict(FX_DEFAULT), "live": True, "interval": 60},
    }


def normalize(data: dict) -> dict:
    """Fill in missing fields (older or hand-edited files)."""
    base = default_data()
    for k, v in base.items():
        data.setdefault(k, v)
    st = data["settings"]
    for k, v in base["settings"].items():
        st.setdefault(k, v)
    for k, v in FX_DEFAULT.items():
        st["fx"].setdefault(k, v)
    if not data["accounts"]:
        data["accounts"] = base["accounts"]
    # version 1: manual "value" entries were replaced by live quotes
    data["entries"] = [e for e in data["entries"] if e.get("kind") != "value"]
    for e in data["entries"]:
        e.setdefault("id", new_id())
    data["version"] = 3
    return data


def _merge_duplicates(pairs) -> dict:
    """JSON object hook: a hand-edited file may contain the same key twice (e.g. two "NORDEA GLOBAL E"
    entries). Plain json keeps only the last one; here two objects are merged and the first one's
    values win, so a symbol is never lost to an almost empty duplicate."""
    out: dict = {}
    for k, v in pairs:
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = {**v, **out[k]}
        elif k not in out:
            out[k] = v
    return out


class Store:
    """JSON file: atomic save and a daily backup."""

    def __init__(self, path: str = DATA_FILE):
        self.path = os.path.abspath(path)
        self.error = ""
        self.exists = os.path.exists(self.path)
        self.data = self.load()

    def load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return normalize(json.load(f, object_pairs_hook=_merge_duplicates))
        except FileNotFoundError:
            return default_data()
        except (OSError, ValueError) as ex:
            # Never overwrite a broken file: keep a copy of it.
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            try:
                shutil.copy2(self.path, f"{self.path}.broken-{stamp}")
            except OSError:
                pass
            self.error = f"Could not read your data ({ex}). The old file was copied aside."
            return default_data()

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    @property
    def backup_dir(self) -> str:
        return os.path.join(self.folder, "backups")

    def _backup(self):
        if not os.path.exists(self.path):
            return
        os.makedirs(self.backup_dir, exist_ok=True)
        name = os.path.join(self.backup_dir, f"portfolio-{dt.date.today().isoformat()}.json")
        if os.path.exists(name):
            return
        shutil.copy2(self.path, name)
        olds = sorted(f for f in os.listdir(self.backup_dir)
                      if f.startswith("portfolio-") and f.endswith(".json"))
        for f in olds[:-BACKUPS_KEPT]:
            os.remove(os.path.join(self.backup_dir, f))

    def save(self) -> bool:
        try:
            os.makedirs(self.folder, exist_ok=True)
            self._backup()
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self.error = ""
            self.exists = True
            return True
        except OSError as ex:
            self.error = f"Saving failed: {ex}"
            return False


# ---------------------------------------------------------------------------
#  Bookkeeping
# ---------------------------------------------------------------------------

@dataclass
class Holding:
    account: str
    security: str
    currency: str = "EUR"
    qty: float = 0.0
    cost: float = 0.0          # cost basis in EUR including fees
    cost_ccy: float = 0.0      # cost basis in the trading currency, excluding fees
    cost_ccy_fees: float = 0.0  # cost basis in the trading currency including fees (Nordnet's way)
    fees: float = 0.0
    estimated: bool = False    # EUR amount of some buy is an estimate
    missing_cost: bool = False  # shares transferred in without a cost basis

    @property
    def avg_price(self) -> float:
        """Average purchase price including fees, as Nordnet's "average price" (keskikurssi):
        fees are converted to the trading currency at the trade's own exchange rate."""
        return self.cost_ccy_fees / self.qty if abs(self.qty) > 1e-9 else 0.0


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
    loan_in: float = 0.0       # student loan money in Nordnet (deposits − withdrawals)
    own_in: float = 0.0        # own money in Nordnet
    gift_in: float = 0.0       # shares transferred in (gifts), at their cost basis
    ext_in: float = 0.0        # holdings outside Nordnet (Binance, Nordea) at their cost basis, EUR
    cash: Dict[str, float] = field(default_factory=dict)
    acc_cost: Dict[str, float] = field(default_factory=dict)
    acc_net: Dict[str, float] = field(default_factory=dict)    # net contributions per account
    cost_total: float = 0.0
    holdings: List[Holding] = field(default_factory=list)
    trade_fees: float = 0.0
    other_fees: float = 0.0
    dividends: float = 0.0
    realized: float = 0.0
    loans: Dict[str, LoanState] = field(default_factory=dict)
    rates: Dict[str, float] = field(default_factory=dict)      # EUR per unit of currency
    rate_src: Dict[str, str] = field(default_factory=dict)     # where the rate comes from
    amounts: Dict[str, Tuple[float, bool]] = field(default_factory=dict)  # trade EUR amount, estimated?
    trade_rate: Dict[str, float] = field(default_factory=dict)  # trade id -> EUR per unit (incl. FX fee)
    estimates: int = 0
    sec_ccy: Dict[str, str] = field(default_factory=dict)      # trading currency per security
    unit_eur: Dict[str, float] = field(default_factory=dict)   # latest per-share price in EUR
    days: List[dt.date] = field(default_factory=list)
    series: Dict[str, list] = field(default_factory=lambda: {k: [] for k in
                                                              ("own", "loan", "gift", "cost", "debt", "cash")})
    hold_days: List[Dict[str, float]] = field(default_factory=list)  # shares per security each day

    @property
    def net_in(self) -> float:
        return self.loan_in + self.own_in + self.gift_in + self.ext_in

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

    def day_index(self, d: dt.date) -> Optional[int]:
        if not self.days:
            return None
        i = (d - self.days[0]).days
        return max(0, min(len(self.days) - 1, i))


def excluded_securities(data: dict) -> set:
    """Securities the user has hidden from the portfolio (Settings → Hide)."""
    return {name for name, rec in data.get("securities", {}).items() if rec.get("excluded")}


def iso_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def fx_rates(entries: List[dict], settings: dict) -> Tuple[Dict[str, float], Dict[str, str]]:
    """EUR per unit of currency: from the latest confirmed trade (includes Nordnet's
    currency exchange fee), otherwise the fallback rate from the settings."""
    rates = {c: 1.0 / v for c, v in settings.get("fx", {}).items() if v}
    src = {c: "fallback rate" for c in rates}
    rates["EUR"], src["EUR"] = 1.0, ""
    for e in entries:  # in date order: the latest one wins
        if e["kind"] in ("buy", "sell") and e.get("amount") is not None and e.get("currency", "EUR") != "EUR":
            gross = float(e.get("qty") or 0) * float(e.get("price") or 0)
            fees = float(e.get("fees") or 0)
            net = e["amount"] - fees if e["kind"] == "buy" else e["amount"] + fees
            if gross > 0 and net > 0:
                rates[e["currency"]] = net / gross
                src[e["currency"]] = f"{e.get('security', 'trade')} {fmt_date(e['date'])}"
    return rates, src


def estimate_rate(ccy: str, date: str, rates: Dict[str, float], fx_mid=None,
                  trade: Optional[dict] = None) -> Tuple[float, str]:
    """EUR per unit for a trade whose EUR amount is not known yet: the mid rate when the trade
    happened (fx_mid may use the trade's price to find the moment) plus Nordnet's exchange fee;
    otherwise the latest confirmed trade's rate."""
    if not ccy or ccy == "EUR":
        return 1.0, ""
    mid = fx_mid(ccy, date, trade) if fx_mid else None
    if mid:
        return mid * (1 + FX_FEE), f"EUR/{ccy} on {fmt_date(date)} + {FX_FEE * 100:.2f}% exchange fee"
    return rates.get(ccy, 1.0), ""


def trade_eur(e: dict, rates: Dict[str, float], fx_mid=None) -> Tuple[float, bool, float]:
    """EUR amount of a trade (positive), whether it is an estimate, and the exchange rate
    (EUR per unit of the trading currency, including the exchange fee)."""
    gross = float(e.get("qty") or 0) * float(e.get("price") or 0)
    fees = float(e.get("fees") or 0)
    ccy = e.get("currency", "EUR") or "EUR"
    if e.get("amount") is not None:
        amt = float(e["amount"])
        net = amt - fees if e["kind"] == "buy" else amt + fees
        rate = net / gross if ccy != "EUR" and gross > 0 and net > 0 else 1.0
        return amt, False, rate
    rate = estimate_rate(ccy, e["date"], rates, fx_mid, e)[0]
    return (gross * rate + fees if e["kind"] == "buy" else gross * rate - fees), True, rate


def sorted_entries(data: dict) -> List[dict]:
    ents = data["entries"]
    return [ents[i] for i in sorted(range(len(ents)), key=lambda i: (ents[i]["date"], i))]


def ledger(data: dict, today: Optional[dt.date] = None, fx_mid=None) -> Summary:
    """Walk the entries in date order: balances, holdings and daily series.
    fx_mid(ccy, iso date, entry) -> EUR per unit (mid) is used to estimate trades without a EUR amount."""
    today = today or dt.date.today()
    hidden = excluded_securities(data)   # "Hide from portfolio": left out of every number and view
    entries = [e for e in sorted_entries(data) if e.get("security") not in hidden]
    s = Summary()
    s.rates, s.rate_src = fx_rates(entries, data.get("settings", {}))
    accounts = [a["id"] for a in data.get("accounts", [])]
    for a in accounts:
        s.cash[a] = s.acc_cost[a] = s.acc_net[a] = 0.0
    for ln in data.get("loans", []):
        s.loans[ln["name"]] = LoanState(ln["name"])
    hold: Dict[Tuple[str, str], Holding] = {}
    qty_now: Dict[str, float] = {}
    changed = [True]

    def add(d: dict, k: str, x: float):
        d[k] = d.get(k, 0.0) + x

    def holding(acc: str, e: dict) -> Holding:
        sec = (e.get("security") or "").strip() or "?"
        h = hold.get((acc, sec))
        if h is None:
            h = hold[(acc, sec)] = Holding(acc, sec, e.get("currency") or s.sec_ccy.get(sec, "EUR"))
        if e.get("currency"):
            s.sec_ccy[sec] = e["currency"]
            h.currency = e["currency"]
        s.sec_ccy.setdefault(sec, h.currency)
        return h

    def apply(e: dict):
        k, acc = e["kind"], e.get("account", "")
        amt = float(e.get("amount") or 0.0)
        if k in ("deposit", "withdrawal"):
            sg = 1 if k == "deposit" else -1
            lp = float(e.get("loan_part") or 0.0)
            add(s.cash, acc, sg * amt)
            add(s.acc_net, acc, sg * amt)
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
        elif k in ("buy", "sell"):
            eur, est, rate = trade_eur(e, s.rates, fx_mid)
            s.amounts[e["id"]] = (eur, est)
            s.trade_rate[e["id"]] = rate
            s.estimates += int(est)
            h = holding(acc, e)
            q, p = float(e.get("qty") or 0), float(e.get("price") or 0)
            fees = float(e.get("fees") or 0)
            s.trade_fees += fees
            if q > 0:
                s.unit_eur[h.security] = (eur - fees if k == "buy" else eur + fees) / q
            if k == "buy":
                add(s.cash, acc, -eur)
                h.qty += q
                h.cost += eur
                h.cost_ccy += q * p
                h.cost_ccy_fees += q * p + (fees / rate if rate else 0.0)
                h.fees += fees
                h.estimated = h.estimated or est
                add(s.acc_cost, acc, eur)
                s.cost_total += eur
                add(qty_now, h.security, q)
            else:
                add(s.cash, acc, eur)
                frac = min(1.0, q / h.qty) if h.qty > 1e-9 else 0.0
                removed = h.cost * frac
                s.realized += eur - removed
                h.cost -= removed
                h.cost_ccy -= h.cost_ccy * frac
                h.cost_ccy_fees -= h.cost_ccy_fees * frac
                h.fees -= h.fees * frac
                h.qty -= q
                add(s.acc_cost, acc, -removed)
                s.cost_total -= removed
                add(qty_now, h.security, -q)
            changed[0] = True
        elif k == "transfer_in":
            # Shares moved in from elsewhere (e.g. a gift). The cost basis counts as a contribution.
            h = holding(acc, e)
            q, p = float(e.get("qty") or 0), float(e.get("price") or 0)
            h.qty += q
            h.cost += amt
            h.cost_ccy += q * p
            h.cost_ccy_fees += q * p
            h.missing_cost = h.missing_cost or amt <= 0
            if q > 0 and amt > 0:
                s.unit_eur.setdefault(h.security, amt / q)
            add(s.acc_cost, acc, amt)
            add(s.acc_net, acc, amt)
            s.cost_total += amt
            s.gift_in += amt
            add(qty_now, h.security, q)
            changed[0] = True
        elif k == "dividend":
            add(s.cash, acc, amt)
            s.dividends += amt
        elif k == "fee":
            add(s.cash, acc, -amt)
            s.other_fees += amt
        elif k in LOAN_KIND_KEYS:
            name = (e.get("loan") or "").strip() or "Loan"
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

    if entries:
        first, last = iso_date(entries[0]["date"]), max(iso_date(entries[-1]["date"]), today)
        start = min(first - dt.timedelta(days=1), last - dt.timedelta(days=MIN_DAYS - 1))
        j, ser = 0, s.series
        snapshot: Dict[str, float] = {}
        for i in range((last - start).days + 1):
            d = start + dt.timedelta(days=i)
            iso = d.isoformat()
            while j < len(entries) and entries[j]["date"] <= iso:
                apply(entries[j])
                j += 1
            if changed[0]:  # days without trades share the same dict
                snapshot = {k: v for k, v in qty_now.items() if abs(v) > 1e-9}
                changed[0] = False
            s.days.append(d)
            s.hold_days.append(snapshot)
            ser["own"].append(s.own_in)
            ser["loan"].append(s.loan_in)
            ser["gift"].append(s.gift_in)
            ser["cost"].append(s.cost_total)
            ser["debt"].append(s.loan_balance)
            ser["cash"].append(s.cash_total)
    # holdings outside Nordnet: no purchase history, only quantity and average cost
    today_iso = today.isoformat()
    for x in data.get("external", []):
        q, cp, ccy = float(x.get("qty") or 0), float(x.get("cost_price") or 0), x.get("currency") or "EUR"
        if q <= 0 or x.get("name") in hidden:
            continue
        mid = fx_mid(ccy, today_iso, None) if fx_mid and ccy != "EUR" else None
        rate = 1.0 if ccy == "EUR" else (mid or s.rates.get(ccy, 1.0))
        h = Holding(x.get("platform", "Other"), x["name"], ccy, q, q * cp * rate, q * cp, q * cp)
        h.missing_cost = cp <= 0
        hold[(h.account, h.security)] = h
        s.sec_ccy[h.security] = ccy
        s.ext_in += h.cost
        s.cost_total += h.cost
        add(s.acc_cost, h.account, h.cost)
        add(s.acc_net, h.account, h.cost)
        if s.series["own"]:  # no purchase dates: shown from today on
            s.series["cost"][-1] = s.cost_total
            s.series["own"][-1] += h.cost
    s.holdings = sorted((h for h in hold.values() if abs(h.qty) > 1e-9), key=lambda h: -h.cost)
    return s


# ---------------------------------------------------------------------------
#  Market value
# ---------------------------------------------------------------------------

@dataclass
class Position:
    holding: Holding
    symbol: str = ""
    name: str = ""
    price: Optional[float] = None
    prev: Optional[float] = None
    currency: str = ""
    fx: float = 1.0            # EUR per unit of the quote currency
    value: float = 0.0         # EUR
    day: float = 0.0           # EUR
    value_usd: float = 0.0
    day_usd: float = 0.0
    cost_usd: float = 0.0      # what was paid in USD including fees (Nordnet's average price × shares)
    live: bool = False
    weight: float = 0.0
    exchange: str = ""

    @property
    def ret(self) -> float:
        return self.value - self.holding.cost

    @property
    def ret_usd(self) -> float:
        return self.value_usd - self.cost_usd

    @property
    def ret_usd_pct(self) -> Optional[float]:
        return self.ret_usd / self.cost_usd if self.cost_usd > 0.005 else None

    @property
    def day_pct(self) -> Optional[float]:
        if self.live and self.prev:
            return self.price / self.prev - 1
        return None


@dataclass
class Valuation:
    positions: List[Position] = field(default_factory=list)
    holdings_value: float = 0.0    # EUR
    cash: float = 0.0              # EUR
    day: float = 0.0               # EUR
    holdings_usd: float = 0.0
    day_usd: float = 0.0
    cost_usd: float = 0.0
    usd_per_eur: float = 0.0       # 0 = no live EUR/USD rate
    missing: int = 0               # holdings without a quote (valued at cost)
    as_of: float = 0.0

    @property
    def total(self) -> float:
        return self.holdings_value + self.cash

    @property
    def total_usd(self) -> float:
        return self.total * self.usd_per_eur

    @property
    def day_pct(self) -> float:
        base = self.holdings_usd - self.day_usd
        return self.day_usd / base if base > 0.005 else 0.0


def eur_per_unit(ccy: str, quotes: dict, rates: Dict[str, float]) -> float:
    """EUR per unit of currency: live rate (EURUSD=X), otherwise the rate from trades."""
    if not ccy or ccy == "EUR":
        return 1.0
    q = quotes.get(f"EUR{ccy}=X")
    if q is not None and q.price:
        return 1.0 / q.price
    return rates.get(ccy, 1.0)


def symbol_of(data: dict, security: str) -> str:
    return (data.get("securities", {}).get(security, {}).get("symbol") or "").strip()


def session_date(q) -> str:
    """Trading day of a quote (local date of its last price), e.g. Friday's session on a Saturday."""
    t = getattr(q, "time", 0) or 0
    return (dt.date.fromtimestamp(t) if t else dt.date.today()).isoformat()


def session_trades(s: "Summary", data: dict, security: str, day: str, account: Optional[str] = None):
    """Buys and sells of a security on a trading day: ([(qty, price)], [(qty, price)], fees in the
    trading currency). Fees are converted at each trade's own exchange rate."""
    buys, sells, fees = [], [], 0.0
    for e in data.get("entries", []):
        if (e.get("security") == security and e["date"] == day and e["kind"] in ("buy", "sell")
                and (account is None or e.get("account") == account)):
            (buys if e["kind"] == "buy" else sells).append((float(e.get("qty") or 0), float(e.get("price") or 0)))
            rate = s.trade_rate.get(e.get("id"), 0.0)
            fees += float(e.get("fees") or 0) / rate if rate else 0.0
    return buys, sells, fees


def day_change(qty_now: float, price: float, prev: float, buys, sells, fees: float = 0.0) -> float:
    """Today's gain in the quote currency. Shares held since yesterday move from yesterday's close;
    shares bought today move from their purchase price; shares sold today count up to the sale;
    today's trading fees are subtracted."""
    if not prev:
        return -fees
    qty_start = max(0.0, qty_now - sum(q for q, _ in buys) + sum(q for q, _ in sells))
    return (qty_start * (price - prev) + sum(q * (price - p) for q, p in buys)
            + sum(q * (p - prev) for q, p in sells) - fees)


def valuate(s: Summary, data: dict, quotes: dict) -> Valuation:
    """Market value of the holdings in EUR and USD, and today's change."""
    v = Valuation(cash=s.cash_total)
    eur_usd = 1.0 / eur_per_unit("USD", quotes, s.rates)   # USD per EUR
    v.usd_per_eur = eur_usd
    for h in s.holdings:
        sym = symbol_of(data, h.security)
        q = quotes.get(sym) if sym else None
        p = Position(h, sym, h.security)
        trade_fx = eur_per_unit(h.currency, quotes, s.rates)
        p.cost_usd = h.cost_ccy_fees if h.currency == "USD" else h.cost_ccy_fees * trade_fx * eur_usd
        if q is not None and q.price:
            p.price, p.prev, p.currency = q.price, q.prev, q.currency or h.currency
            p.fx = eur_per_unit(p.currency, quotes, s.rates)
            buys, sells, fees = session_trades(s, data, h.security, session_date(q), h.account)
            day_ccy = day_change(h.qty, q.price, q.prev, buys, sells, fees)
            p.value = h.qty * q.price * p.fx
            p.day = day_ccy * p.fx
            if p.currency == "USD":
                p.value_usd = h.qty * q.price
                p.day_usd = day_ccy
            else:
                p.value_usd, p.day_usd = p.value * eur_usd, p.day * eur_usd
            p.live, p.exchange = True, getattr(q, "exchange", "")
            p.name = getattr(q, "name", "") or h.security
            v.as_of = max(v.as_of, getattr(q, "time", 0) or 0)
        else:
            p.currency = h.currency
            p.value = h.cost
            p.value_usd = p.cost_usd or h.cost * eur_usd
            v.missing += 1
        v.positions.append(p)
        v.holdings_value += p.value
        v.day += p.day
        v.holdings_usd += p.value_usd
        v.day_usd += p.day_usd
        v.cost_usd += p.cost_usd
    for p in v.positions:
        p.weight = p.value / v.holdings_value if v.holdings_value > 0.005 else 0.0
    v.positions.sort(key=lambda p: -p.value)
    return v


def _aligned(days: List[dt.date], points: List[Tuple[str, float]]) -> List[Optional[float]]:
    """Daily closes on the timeline: weekends and holidays use the previous close,
    days before the first known close use the first close."""
    if not points:
        return [None] * len(days)
    pts = sorted(points)
    out, k, last = [], 0, None
    for d in days:
        iso = d.isoformat()
        while k < len(pts) and pts[k][0] <= iso:
            last = pts[k][1]
            k += 1
        out.append(last)
    first = next((x for x in out if x is not None), pts[0][1])
    return [first if x is None else x for x in out]


def value_history(s: Summary, data: dict, history: Dict[str, List[Tuple[str, float]]],
                  quotes: dict) -> List[float]:
    """Portfolio value (holdings + cash) in EUR for every day of the timeline.
    Securities without price history are valued at their latest trade price."""
    n = len(s.days)
    if not n:
        return []
    secs = {sec for snap in s.hold_days for sec in snap}
    closes: Dict[str, List[Optional[float]]] = {}
    fxs: Dict[str, List[float]] = {}
    for sec in secs:
        sym = symbol_of(data, sec)
        q = quotes.get(sym) if sym else None
        ccy = (q.currency if q is not None and getattr(q, "currency", "") else s.sec_ccy.get(sec, "EUR"))
        if sym and history.get(sym):
            closes[sec] = _aligned(s.days, history[sym])
            if ccy not in fxs:
                fxh = history.get(f"EUR{ccy}=X")
                if ccy == "EUR":
                    fxs[ccy] = [1.0] * n
                elif fxh:
                    fxs[ccy] = [1.0 / x if x else s.rates.get(ccy, 1.0) for x in _aligned(s.days, fxh)]
                else:
                    fxs[ccy] = [eur_per_unit(ccy, quotes, s.rates)] * n
            closes[sec] = [c * fxs[ccy][i] if c is not None else None for i, c in enumerate(closes[sec])]
    out = []
    for i in range(n):
        tot = s.series["cash"][i]
        for sec, q in s.hold_days[i].items():
            unit = closes.get(sec, [None] * n)[i]
            if unit is None:
                unit = s.unit_eur.get(sec, 0.0)
            tot += q * unit
        out.append(tot)
    return out


def intraday_pnl(s: Summary, data: dict, series: Dict[str, List[Tuple[float, float]]],
                 quotes: dict) -> Tuple[List[float], List[float]]:
    """Today's gain in EUR at each intraday point: (timestamps, gains). Uses the same rule as
    day_change(), so the last point matches today's change shown on the Overview."""
    secs = {}
    for h in s.holdings:
        sym = symbol_of(data, h.security)
        q = quotes.get(sym) if sym else None
        if q is not None and q.prev and series.get(sym):
            g = secs.setdefault(h.security, [sym, q, 0.0])
            g[2] += h.qty
    times = sorted({t for sym, _q, _n in secs.values() for t, _ in series[sym]})
    if not times:
        return [], []
    state = {}
    for sec, (sym, q, qty) in secs.items():
        buys, sells, fees = session_trades(s, data, sec, session_date(q))
        state[sec] = dict(pts=series[sym], k=0, price=q.prev, q=q, qty=qty, buys=buys, sells=sells, fees=fees,
                          fx=eur_per_unit(q.currency, quotes, s.rates))
    out = []
    for t in times:
        tot = 0.0
        for st in state.values():
            pts = st["pts"]
            while st["k"] < len(pts) and pts[st["k"]][0] <= t:
                st["price"] = pts[st["k"]][1]
                st["k"] += 1
            tot += day_change(st["qty"], st["price"], st["q"].prev, st["buys"], st["sells"], st["fees"]) * st["fx"]
        out.append(tot)
    return times, out


def intraday_values(s: Summary, data: dict, series: Dict[str, List[Tuple[float, float]]],
                    quotes: dict) -> Tuple[List[float], List[float], float, List[float]]:
    """Intraday portfolio value in EUR: (timestamps, values, base, contributions).
    series: symbol -> [(unix time, price)]. Holdings and cash are taken from the day of each
    timestamp; prices are carried forward between points. The base is the value at the first
    point's previous close (for 1D: yesterday's close). Contributions (net deposits) per point
    let the chart separate deposits from returns."""
    if not s.days:
        return [], [], 0.0, []
    held_syms = {}
    for snap in s.hold_days[-10:]:
        for sec in snap:
            sym = symbol_of(data, sec)
            if sym and series.get(sym):
                held_syms[sec] = sym
    times = sorted({t for sym in held_syms.values() for t, _ in series[sym]})
    if not times:
        return [], [], 0.0, []
    fx = {}
    for sec, sym in held_syms.items():
        q = quotes.get(sym)
        ccy = q.currency if q is not None and q.currency else s.sec_ccy.get(sec, "EUR")
        fx[sec] = eur_per_unit(ccy, quotes, s.rates)
    ptr = {sec: 0 for sec in held_syms}
    last = {}
    for sec, sym in held_syms.items():  # before the first point: previous close
        q = quotes.get(sym)
        last[sec] = q.prev if q is not None and q.prev else series[sym][0][1]
    vals, contrib = [], []
    base = None
    ser = s.series
    for t in times:
        d = dt.datetime.fromtimestamp(t).date()
        i = s.day_index(d)
        snap = s.hold_days[i]
        contrib.append(ser["own"][i] + ser["loan"][i] + ser["gift"][i])
        if base is None:
            base = s.series["cash"][i] + sum(qty * last.get(sec, s.unit_eur.get(sec, 0.0)) * fx.get(sec, 1.0)
                                             for sec, qty in snap.items())
        for sec, sym in held_syms.items():
            pts = series[sym]
            while ptr[sec] < len(pts) and pts[ptr[sec]][0] <= t:
                last[sec] = pts[ptr[sec]][1]
                ptr[sec] += 1
        tot = s.series["cash"][i]
        for sec, qty in snap.items():
            unit = last[sec] * fx[sec] if sec in last else s.unit_eur.get(sec, 0.0)
            tot += qty * unit
        vals.append(tot)
    return times, vals, base or 0.0, contrib


# ---------------------------------------------------------------------------
#  Formatting and input parsing
# ---------------------------------------------------------------------------

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
CCY_SYMBOL = {"EUR": "€", "USD": "$", "GBP": "£"}


def fmt_num(x: float, dec: int = 0) -> str:
    if abs(round(x, dec)) < 10 ** (-dec) / 2:
        x = 0.0
    s = f"{abs(x):,.{dec}f}"
    return ("−" if x < 0 else "") + s


def fmt_money(x: float, ccy: str = "EUR", dec: int = 2, sign: bool = False) -> str:
    """€1,234.56 / −$12.30 / +€5.00 / 50.70 NOK"""
    num = fmt_num(abs(x), dec)
    pre = "−" if round(x, dec) < 0 else ("+" if sign and round(x, dec) > 0 else "")
    sym = CCY_SYMBOL.get(ccy)
    return f"{pre}{sym}{num}" if sym else f"{pre}{num} {ccy}"


def fmt_eur(x: float, dec: int = 2, sign: bool = False) -> str:
    return fmt_money(x, "EUR", dec, sign)


def fmt_usd(x: float, dec: int = 2, sign: bool = False) -> str:
    return fmt_money(x, "USD", dec, sign)


def fmt_money_short(x: float) -> str:
    """Whole euros without cents: 1,300 / 150.50"""
    return fmt_num(x) if abs(x - round(x)) < 0.005 else fmt_num(x, 2)


def fmt_pct(x: float, dec: int = 0, sign: bool = False) -> str:
    s = fmt_num(x * 100, dec)
    if sign and round(x * 100, dec) > 0:
        s = "+" + s
    return s + "%"


def fmt_qty(x: float) -> str:
    return fmt_num(x) if abs(x - round(x)) < 1e-9 else fmt_num(x, 4).rstrip("0").rstrip(".")


def fmt_price(x: float, ccy: str = "") -> str:
    """Price with 2–4 decimals: 186.3476 / 1,440.69, optionally with a currency."""
    s = f"{x:.4f}".rstrip("0")
    dec = max(2, len(s.split(".")[1]) if "." in s else 0)
    return fmt_money(x, ccy, dec) if ccy else fmt_num(x, dec)


def fmt_input(x: Optional[float]) -> str:
    """Number for an input field: no thousands separators, decimal point."""
    if x is None:
        return ""
    return f"{x:.6f}".rstrip("0").rstrip(".")


def fmt_date(iso) -> str:
    d = iso_date(iso) if isinstance(iso, str) else iso
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def parse_num(txt: str) -> Optional[float]:
    """'1,013.98' / '1 013,98' / '186.3476' / '€1,013.98' -> float. Empty -> None, bad -> ValueError.
    With both separators the last one is the decimal separator; a lone comma is a decimal comma
    (as in Nordnet's Finnish view)."""
    t = txt.strip()
    for ch in (" ", " ", " ", "€", "$", "%", "EUR", "USD"):
        t = t.replace(ch, "")
    t = t.replace("−", "-")
    if not t:
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif t.count(",") > 1:
        t = t.replace(",", "")
    else:
        t = t.replace(",", ".")
    v = float(t)
    if math.isnan(v) or math.isinf(v):
        raise ValueError(txt)
    return v


def parse_date(txt: str, today: Optional[dt.date] = None) -> dt.date:
    """'6 Nov 2026', '6 Nov', '2026-11-06', '6.11.2026', '6.11.', '6/11/2026', 'today',
    'yesterday' -> date (day before month)."""
    today = today or dt.date.today()
    t = txt.strip().lower()
    if t in ("", "t", "today", "now", "tänään"):
        return today
    if t in ("yesterday", "eilen"):
        return today - dt.timedelta(days=1)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})\s*([a-z]{3})[a-z]*\.?,?\s*(\d{2,4})?", t)
    if m:
        months = [x.lower() for x in MONTHS]
        if m[2] not in months:
            raise ValueError(txt)
        y = m[3] or str(today.year)
        y = "20" + y if len(y) == 2 else y
        return dt.date(int(y), months.index(m[2]) + 1, int(m[1]))
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
    """Time axis labels: days, weeks (Mondays) or months."""
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
            return [(i, f"{days[i].day} {MONTHS[days[i].month - 1]}") for i in idx]
    for m in (1, 2, 3, 6, 12, 24, 60):
        idx = [i for i, d in enumerate(days) if d.day == 1 and (d.month - 1) % m == 0
               and (m < 12 or (d.month == 1 and d.year % (m // 12) == 0))]
        if len(idx) <= max_n:
            return [(i, str(days[i].year) if m >= 12 or days[i].month == 1
                     else MONTHS[days[i].month - 1]) for i in idx]
    return []


def time_ticks(times: List[float], max_n: int, multi_day: bool) -> List[Tuple[int, str]]:
    """Labels for an intraday axis: hours (1D) or the start of each day (1W)."""
    if not times:
        return []
    out = []
    if multi_day:
        prev = None
        for i, t in enumerate(times):
            d = dt.datetime.fromtimestamp(t).date()
            if d != prev:
                out.append((i, f"{['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][d.weekday()]} {d.day}"))
                prev = d
        return out
    for step in (1, 2, 3, 4, 6):
        idx, prev = [], None
        for i, t in enumerate(times):
            h = dt.datetime.fromtimestamp(t)
            if h.minute < 5 and h.hour % step == 0 and h.hour != prev:
                idx.append((i, f"{h.hour}:00"))
                prev = h.hour
        if len(idx) <= max_n:
            return idx
    return out
