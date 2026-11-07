# -*- coding: utf-8 -*-
"""
Stock quotes from Yahoo Finance (free, no API key).

TradingView has no public API for price data, so quotes come from Yahoo Finance's public
chart endpoint. US exchanges are real time, some other exchanges are delayed ~15 minutes.
tradingview_url() opens the same stock's chart in the browser.

PriceService fetches in a background thread (the UI never freezes) and caches the latest
quotes and the price history on disk, so they are shown even without a connection.

Update schedule while the app is open:
- live prices: streamed in real time (several times a second) from Yahoo's price streamer;
  if the stream is unavailable, the quotes below are the fallback
- quotes + yesterday's close + today's 5-minute prices (1D chart): every 60 s
- 1W chart (30-minute prices for 5 days): every 5 min, only while 1W is selected
- daily closes for the history chart: every 6 h
- a failed request is retried after 15 s
"""

import base64
import datetime as dt
import json
import os
import re
import socket
import ssl
import struct
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from typing import Callable, Dict, List, Optional, Set, Tuple

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PortfolioManager/1.0"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
HISTORY_MAX_AGE = 6 * 3600     # daily closes are refreshed every 6 hours
WEEK_MAX_AGE = 5 * 60          # 1W intraday prices every 5 minutes

# Yahoo exchange codes per currency (preferred exchange when searching a symbol)
EXCHANGES_BY_CCY = {
    "USD": {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS", "NAS", "NYS"},
    "NOK": {"OSL"}, "SEK": {"STO"}, "DKK": {"CPH"}, "GBP": {"LSE"},
    "EUR": {"HEL", "GER", "AMS", "PAR", "MIL", "MCE", "BRU", "FRA"},
    "CAD": {"TOR", "VAN", "NEO", "CNQ"},
}
# Yahoo exchange code -> TradingView exchange
TV_EXCHANGE = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NAS": "NASDAQ", "NYQ": "NYSE",
               "NYS": "NYSE", "ASE": "AMEX", "PCX": "AMEX", "OSL": "OSL", "STO": "OMXSTO", "HEL": "OMXHEX",
               "CPH": "OMXCOP", "GER": "XETR", "TOR": "TSX", "LSE": "LSE", "AMS": "EURONEXT",
               "PAR": "EURONEXT"}
YAHOO_SUFFIX = re.compile(r"\.(OL|HE|ST|CO|DE|TO|L|AS|PA|MI|F|V|NE)$")


@dataclass
class Quote:
    symbol: str
    price: float
    prev: float
    currency: str = ""
    time: float = 0.0
    name: str = ""
    exchange: str = ""

    @property
    def change_pct(self) -> float:
        return self.price / self.prev - 1 if self.prev else 0.0


def _get(url: str, timeout: float = 10.0, tries: int = 3) -> dict:
    """GET + JSON. Yahoo occasionally refuses single requests, so retry."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for k in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except (OSError, ValueError):
            if k == tries - 1:
                raise
            time.sleep(0.8 * (k + 1))
    raise OSError("no response")


def _points(r: dict) -> List[Tuple[float, float]]:
    closes = r.get("indicators", {}).get("quote", [{}])[0].get("close") or []
    return [(float(t), float(c)) for t, c in zip(r.get("timestamp") or [], closes) if c is not None]


def _bars(r: dict) -> List[Tuple[float, float, float]]:
    """(start time, low, high) of each bar – used to find when a trade at a given price happened."""
    q = r.get("indicators", {}).get("quote", [{}])[0]
    return [(float(t), float(lo), float(hi)) for t, lo, hi in zip(r.get("timestamp") or [], q.get("low") or [],
                                                                    q.get("high") or [])
            if lo is not None and hi is not None]


def fetch_quote(sym: str) -> Tuple[Quote, List[Tuple[float, float]], List[Tuple[float, float, float]]]:
    """Latest price, yesterday's close and today's 5-minute prices (closes and low/high bars)."""
    d = _get(CHART_URL.format(sym=urllib.parse.quote(sym)) + "?range=1d&interval=5m")
    r = d["chart"]["result"][0]
    m = r["meta"]
    q = Quote(sym, float(m["regularMarketPrice"]), float(m.get("chartPreviousClose") or m.get("previousClose") or 0),
              m.get("currency") or "", float(m.get("regularMarketTime") or 0),
              m.get("longName") or m.get("shortName") or "", m.get("exchangeName") or "")
    return q, _points(r), _bars(r)


def fetch_intraday(sym: str, rng: str = "5d", interval: str = "30m") -> List[Tuple[float, float]]:
    d = _get(CHART_URL.format(sym=urllib.parse.quote(sym)) + f"?range={rng}&interval={interval}")
    return _points(d["chart"]["result"][0])


def fetch_history(sym: str, start: dt.date) -> List[Tuple[str, float]]:
    """Daily closes from the start date to today: [(iso date, close)]."""
    p1 = int(time.mktime((start - dt.timedelta(days=7)).timetuple()))
    p2 = int(time.time()) + 86400
    d = _get(CHART_URL.format(sym=urllib.parse.quote(sym)) + f"?period1={p1}&period2={p2}&interval=1d")
    r = d["chart"]["result"][0]
    off = int(r["meta"].get("gmtoffset") or 0)
    return [(dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat(), c) for t, c in _points(r)]


def name_queries(name: str) -> List[str]:
    """Search terms from a Nordnet name: 'Recursion Pharmaceuticals A' -> 'Recursion Pharmaceuticals'."""
    words = [w for w in re.split(r"\s+", name.strip()) if w]
    drop = {"adr", "ads", "a", "b", "c", "oyj", "asa", "ab", "inc", "inc.", "corp", "corp.", "plc", "ltd", "sa"}
    while words and words[-1].lower() in drop:
        words.pop()
    words = [w for w in words if w.lower() not in ("adr", "ads")]
    out = []
    for n in (len(words), 3, 2, 1):
        q = " ".join(words[:n])
        if q and q not in out and (n > 1 or len(words) == 1):
            out.append(q)
    return out or [name]


def search_symbol(name: str, currency: str = "") -> Optional[Tuple[str, str, str]]:
    """Find a Yahoo symbol by security name: (symbol, exchange, name) or None."""
    want = EXCHANGES_BY_CCY.get(currency, set())
    for q in name_queries(name):
        d = _get(SEARCH_URL + "?" + urllib.parse.urlencode({"q": q, "quotesCount": 8, "newsCount": 0}))
        quotes = [x for x in d.get("quotes", []) if x.get("quoteType") in ("EQUITY", "ETF") and x.get("symbol")]
        if not quotes:
            continue
        best = next((x for x in quotes if x.get("exchange") in want), None) if want else None
        best = best or quotes[0]
        return best["symbol"], best.get("exchange", ""), best.get("shortname") or best.get("longname") or ""
    return None


# ---------------------------------------------------------------------------
#  Real-time stream (Yahoo's price streamer over a websocket, standard library only)
# ---------------------------------------------------------------------------

STREAM_HOST = "streamer.finance.yahoo.com"


def _varint(b: bytes, i: int) -> Tuple[int, int]:
    shift = res = 0
    while True:
        c = b[i]
        i += 1
        res |= (c & 0x7F) << shift
        shift += 7
        if not c & 0x80:
            return res, i


def decode_pricing(b: bytes) -> dict:
    """Decode Yahoo's PricingData protobuf: 1 = symbol, 2 = price, 3 = time (ms), 8 = change %."""
    out, i = {}, 0
    while i < len(b):
        key, i = _varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
            if f in (3, 9, 14):          # sint64 fields use zigzag encoding
                v = (v >> 1) ^ -(v & 1)
        elif wt == 5:
            v = struct.unpack("<f", b[i:i + 4])[0]
            i += 4
        elif wt == 1:
            v = struct.unpack("<d", b[i:i + 8])[0]
            i += 8
        elif wt == 2:
            n, i = _varint(b, i)
            v = b[i:i + n]
            i += n
        else:
            break
        out[f] = v
    return out


class Streamer:
    """Keeps a websocket open to Yahoo's streamer and calls on_tick(symbol, price, time) for
    every price update. Reconnects by itself; all socket I/O happens in its own thread."""

    def __init__(self, on_tick: Callable[[str, float, float], None]):
        self.on_tick = on_tick
        self.enabled = True
        self.want: Set[str] = set()
        self.subscribed: Set[str] = set()
        self.connected = False
        self.last_tick = 0.0
        self.error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock = None
        self._buf = b""
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="stream", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def set_symbols(self, symbols):
        with self._lock:
            self.want = set(symbols)

    # ---- websocket ----
    def _connect(self):
        raw = socket.create_connection((STREAM_HOST, 443), timeout=10)
        s = ssl.create_default_context().wrap_socket(raw, server_hostname=STREAM_HOST)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET /?version=2 HTTP/1.1\r\nHost: {STREAM_HOST}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                   f"Origin: https://finance.yahoo.com\r\nUser-Agent: {USER_AGENT}\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = s.recv(4096)
            if not chunk:
                raise ConnectionError("handshake closed")
            resp += chunk
        head, self._buf = resp.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        s.settimeout(1.0)
        self._sock = s

    def _send(self, op: int, data: bytes):
        mask = os.urandom(4)
        n = len(data)
        hdr = bytes([0x80 | op])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self._sock.sendall(hdr + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)   # raises socket.timeout after 1 s of silence
            if not chunk:
                raise ConnectionError("stream closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _frame(self) -> Tuple[int, bytes]:
        b1, b2 = self._read(2)
        op, n = b1 & 0x0F, b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._read(8))[0]
        mask = self._read(4) if b2 & 0x80 else None
        data = self._read(n)
        if mask:
            data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        return op, data

    def _sync_subscriptions(self):
        with self._lock:
            want = set(self.want)
        add, drop = want - self.subscribed, self.subscribed - want
        if add:
            self._send(0x1, json.dumps({"subscribe": sorted(add)}).encode())
        if drop:
            self._send(0x1, json.dumps({"unsubscribe": sorted(drop)}).encode())
        self.subscribed = want

    def _handle(self, text: str):
        try:
            payload = json.loads(text).get("message", text)
        except ValueError:
            payload = text
        d = decode_pricing(base64.b64decode(payload))
        sym, price = d.get(1, b"").decode(errors="replace"), d.get(2)
        if sym and price:
            self.last_tick = time.time()
            # the stream sends 32-bit floats: 1422.19 arrives as 1422.18994…, so keep 7 significant digits
            self.on_tick(sym, float(f"{price:.7g}"), (d.get(3) or 0) / 1000.0)

    def _run(self):
        backoff = 5
        while not self._stop.is_set():
            if not self.enabled or not self.want:
                time.sleep(1)
                continue
            try:
                self._connect()
                self.connected, self.error, self.subscribed, backoff = True, "", set(), 5
                while not self._stop.is_set() and self.enabled:
                    self._sync_subscriptions()
                    try:
                        op, data = self._frame()
                    except socket.timeout:
                        continue
                    if op == 0x9:                       # ping -> pong
                        self._send(0xA, data)
                    elif op == 0x8:                     # server closed
                        raise ConnectionError("closed by server")
                    elif op in (0x1, 0x2):              # Yahoo sends each update as one small frame
                        self._handle(data.decode(errors="replace"))
            except Exception as ex:  # network error or unexpected data: reconnect later
                self.error = str(ex)
            finally:
                self.connected = False
                try:
                    if self._sock:
                        self._sock.close()
                except OSError:
                    pass
                self._sock = None
            if not self._stop.is_set() and self.enabled:
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)


def tradingview_url(symbol: str, exchange: str = "") -> str:
    base = YAHOO_SUFFIX.sub("", symbol)
    prefix = TV_EXCHANGE.get(exchange, "")
    tv = f"{prefix}:{base}" if prefix else base
    return "https://www.tradingview.com/chart/?symbol=" + urllib.parse.quote(tv, safe="")


class PriceService:
    """Fetches quotes in the background. The UI reads the results (snapshot()) and watches two
    counters: version changes on every price tick, rest_version only when quotes, history,
    intraday prices or symbol searches arrive from the regular requests."""

    def __init__(self, cache_path: str, interval: int = 60):
        self.cache_path = cache_path
        self.interval = interval
        self._enabled = True
        self.want_week = False
        self.stream = Streamer(self._on_tick)
        self.rest_version = 0
        self.lock = threading.Lock()
        self.quotes: Dict[str, Quote] = {}
        self.history: Dict[str, List[Tuple[str, float]]] = {}
        self.hist_meta: Dict[str, Tuple[float, str]] = {}      # symbol -> (fetched at, start date)
        self.intraday: Dict[str, Dict[str, list]] = {"1d": {}, "5d": {}, "1d_bars": {}}
        self.week_fetched = 0.0
        self.found: Dict[str, Optional[Tuple[str, str, str]]] = {}  # name -> search result
        self.symbols: List[str] = []
        self.hist_starts: Dict[str, dt.date] = {}
        self.searches: List[Tuple[str, str]] = []
        self.version = 0
        self.last_ok = 0.0
        self.last_try = 0.0
        self.error = ""
        self.busy = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._load_cache()

    # ---- cache -------------------------------------------------------------
    def _load_cache(self):
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                c = json.load(f)
            self.quotes = {k: Quote(**v) for k, v in c.get("quotes", {}).items()}
            self.history = {k: [tuple(p) for p in v] for k, v in c.get("history", {}).items()}
            self.hist_meta = {k: tuple(v) for k, v in c.get("hist_meta", {}).items()}
            self.intraday["1d"] = {k: [tuple(p) for p in v] for k, v in c.get("intraday_1d", {}).items()}
            self.last_ok = float(c.get("last_ok", 0))
        except (OSError, ValueError, TypeError):
            pass

    def save_cache(self):
        with self.lock:
            c = {"quotes": {k: asdict(v) for k, v in self.quotes.items()},
                 "history": self.history, "hist_meta": self.hist_meta,
                 "intraday_1d": self.intraday["1d"], "last_ok": self.last_ok}
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(c, f)
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

    # ---- control -----------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, on: bool):
        self._enabled = on
        self.stream.enabled = on

    @property
    def streaming(self) -> bool:
        """True while the real-time stream is connected and has delivered prices recently."""
        return self.stream.connected and time.time() - self.stream.last_tick < 120

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="quotes", daemon=True)
            self._thread.start()
        self.stream.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        self.stream.stop()
        self.save_cache()

    def request(self, symbols: List[str], hist_starts: Dict[str, dt.date], searches: List[Tuple[str, str]]):
        """What to fetch: quotes, daily history (from a start date) and symbol searches (name, currency)."""
        with self.lock:
            new = (set(symbols) - set(self.symbols)) or any(s[0] not in self.found for s in searches)
            self.symbols = sorted(set(symbols))
            self.hist_starts = dict(hist_starts)
            self.searches = [s for s in searches if s[0] not in self.found]
        self.stream.set_symbols(symbols)
        if new:  # new symbol or search: fetch now instead of on the next round
            self._wake.set()

    def _on_tick(self, sym: str, price: float, t: float):
        """A real-time price from the stream (called in the stream thread)."""
        with self.lock:
            q = self.quotes.get(sym)
            if q is not None:      # yesterday's close etc. come from the regular request first
                self.quotes[sym] = replace(q, price=price, time=t or time.time())
                self.version += 1

    def set_want_week(self, on: bool):
        """The 1W chart needs 30-minute prices for 5 days; fetched only while it is shown."""
        self.want_week = on
        if on and time.time() - self.week_fetched > WEEK_MAX_AGE:
            self._wake.set()

    def refresh_now(self):
        self._wake.set()

    def snapshot(self):
        with self.lock:
            return (dict(self.quotes), dict(self.history), dict(self.found),
                    {k: dict(v) for k, v in self.intraday.items()})

    # ---- background thread -------------------------------------------------
    def _run(self):
        while not self._stop.is_set():
            failed = False
            if self.enabled:
                failed = self._cycle()
            # failed requests are retried after 15 s, otherwise the normal interval
            self._wake.wait(15 if failed else self.interval)
            self._wake.clear()

    def _cycle(self) -> bool:
        self.busy = True
        self.last_try = time.time()
        errors = []
        with self.lock:
            searches, symbols, starts = list(self.searches), list(self.symbols), dict(self.hist_starts)
            self.searches = []
        for name, ccy in searches:
            try:
                res = search_symbol(name, ccy)
            except Exception as ex:  # network or format error: try again next round
                errors.append(f"{name}: {ex}")
                with self.lock:
                    self.searches.append((name, ccy))
                continue
            with self.lock:
                self.found[name] = res
                self.version += 1
                self.rest_version += 1
        ok = 0
        if symbols:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for sym, res in zip(symbols, pool.map(self._safe_quote, symbols)):
                    if isinstance(res, tuple):
                        ok += 1
                        with self.lock:
                            self.quotes[sym] = res[0]
                            self.intraday["1d"][sym] = res[1]
                            self.intraday["1d_bars"][sym] = res[2]
                    else:
                        errors.append(f"{sym}: {res}")
        now = time.time()
        if self.want_week and now - self.week_fetched > WEEK_MAX_AGE and symbols:
            with ThreadPoolExecutor(max_workers=6) as pool:
                res = list(pool.map(self._safe_week, symbols))
            with self.lock:
                for sym, pts in zip(symbols, res):
                    if isinstance(pts, list):
                        self.intraday["5d"][sym] = pts
            self.week_fetched = now
        for sym, start in starts.items():
            meta = self.hist_meta.get(sym)
            if meta and now - meta[0] < HISTORY_MAX_AGE and meta[1] <= start.isoformat():
                continue
            try:
                pts = fetch_history(sym, start)
            except Exception as ex:
                errors.append(f"{sym} (history): {ex}")
                continue
            with self.lock:
                self.history[sym] = pts
                self.hist_meta[sym] = (now, start.isoformat())
        with self.lock:
            if ok or not symbols:
                self.last_ok = now
            self.error = "; ".join(errors[:3]) if errors and not ok else ""
            self.version += 1
            self.rest_version += 1
        self.busy = False
        self.save_cache()
        return bool(errors)

    @staticmethod
    def _safe_quote(sym: str):
        try:
            return fetch_quote(sym)
        except Exception as ex:
            return ex

    @staticmethod
    def _safe_week(sym: str):
        try:
            return fetch_intraday(sym, "5d", "30m")
        except Exception as ex:
            return ex
