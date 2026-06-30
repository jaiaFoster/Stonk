"""
app/services/universe_discovery_service.py — Expanded ticker universe discovery.

Strategy-agnostic service that builds a curated universe of liquid, optionable
tickers with upcoming earnings. Sources:

- S&P 500 constituents (Wikipedia, weekly refresh, embedded fallback)
- Russell 1000 supplement (embedded ~200 high-liquidity mid-caps)
- Cross-referenced with upcoming earnings from existing EarningsProvider
- Price/volume filtered via existing MarketDataHub cache

The service caches results daily in SQLite. Downstream strategies consume the
universe with their own exclusion/filter rules — this service has no opinion
about which strategy uses the results.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

import requests

from app import config

LogFn = Callable[[str], None]

_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_SP500_FETCH_TIMEOUT = 15

# ~200 liquid Russell 1000 names not in S&P 500.  Curated for options
# liquidity — names that realistically produce calendar/vertical candidates.
_RUSSELL_SUPPLEMENT: list[str] = [
    "CRDO", "HOOD", "SOFI", "MSTR", "CART", "RKLB", "DUOL", "IOT", "APP",
    "CELH", "CAVA", "TOST", "DRS", "SAIA", "FND", "MANH", "WFRD", "WING",
    "PCVX", "ELF", "FORM", "ZETA", "AFRM", "BMRN", "LSCC", "BILL", "ASAN",
    "CFLT", "RXRX", "JOBY", "FLNC", "INTA", "ESTC", "GTLB", "PI", "BRZE",
    "SEMR", "HIMS", "RELY", "STEP", "CVNA", "CWAN", "VKTX", "INSM", "SATS",
    "TWST", "KRYS", "CRNX", "DOCS", "RMBS", "SLAB", "PAYO", "QTWO", "OLLI",
    "LNTH", "TGTX", "CALM", "BLD", "COOP", "JBT", "SPSC", "WTS", "FSS",
    "NUVB", "GKOS", "CHRD", "TMDX", "ALNY", "MDGL", "ROIV", "NTRA", "EXAS",
    "GSHD", "PRCT", "RVMD", "ITCI", "AXSM", "BPMC", "ARWR", "SRPT",
    "IONS", "INCY", "NBIX", "UTHR", "RARE", "ALKS", "PTCT", "PCVX",
    "MRVL", "SMCI", "VRT", "GEV", "OSCR", "LYFT", "RUN", "ENPH", "SEDG",
    "FSLR", "ARRY", "RIVN", "LCID", "NIO", "XPEV", "LI", "ZK", "FUTU",
    "TIGR", "BNTX", "MRNA", "NVAX", "SGEN", "DNA", "BEAM", "CRSP", "EDIT",
    "NTLA", "VERV", "PRME", "ACHR", "LUNR", "MNTS", "ASTS", "RCAT",
    "RDW", "ALAB", "GDS", "GRAB", "SE", "MELI", "NU", "STNE", "PAGS",
    "XP", "GLOB", "DLO", "DDOG", "NET", "ZS", "CRWD", "S", "OKTA",
    "MNDY", "ZI", "PATH", "AI", "BBAI", "PLTR", "SNOW", "MDB", "DKNG",
    "PENN", "CHDN", "RSI", "GENI", "DIS", "NFLX", "ROKU", "TTWO",
    "EA", "RBLX", "U", "MTCH", "BMBL", "PINS", "SNAP", "RDDT",
    "COIN", "SQ", "AFRM", "UPST", "PYPL", "ADYEY", "FOUR", "TOST",
    "HQY", "PAYC", "WK", "TYL", "PCOR", "BSY", "GWRE", "APPN", "SQSP",
    "CWAN", "WIX", "SHOP", "GDDY", "WDAY",
]

# Embedded S&P 500 fallback — top ~100 most liquid names by volume.
_SP500_FALLBACK: list[str] = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK.B",
    "UNH", "JNJ", "JPM", "V", "XOM", "PG", "MA", "HD", "AVGO", "CVX",
    "MRK", "ABBV", "LLY", "PEP", "COST", "KO", "ADBE", "WMT", "CRM",
    "MCD", "CSCO", "TMO", "ACN", "ABT", "DHR", "LIN", "TXN", "NKE",
    "NEE", "UNP", "QCOM", "PM", "BMY", "RTX", "LOW", "ORCL", "AMD",
    "INTC", "AMGN", "INTU", "MS", "GS", "BLK", "ISRG", "SBUX", "MDT",
    "ADP", "GILD", "SYK", "BKNG", "VRTX", "ADI", "CB", "MMC", "REGN",
    "DE", "CME", "LRCX", "KLAC", "ZTS", "PLD", "SCHW", "C", "CI",
    "NOW", "MDLZ", "TMUS", "BA", "AMAT", "DUK", "BDX", "SLB", "ICE",
    "SO", "EOG", "CL", "NOC", "GD", "USB", "CMG", "MCK", "MPC",
    "EMR", "PXD", "PSA", "HCA", "SRE", "WM", "APD", "NSC", "TT",
    "PNC", "ORLY", "AZO", "CTAS", "PAYX", "MNST", "FTNT", "PANW",
    "SNPS", "CDNS", "NXPI", "MCHP",
]


class _WikiTableParser(HTMLParser):
    """Minimal HTML table parser to extract S&P 500 ticker symbols."""

    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._in_tbody = False
        self._in_row = False
        self._in_cell = False
        self._col_idx = 0
        self._current_text = ""
        self._tickers: list[str] = []
        self._table_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table_count += 1
            if self._table_count == 1:
                self._in_table = True
        elif tag == "tbody" and self._in_table:
            self._in_tbody = True
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._col_idx = 0
        elif tag == "td" and self._in_row:
            self._in_cell = True
            self._current_text = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_cell:
            self._in_cell = False
            if self._col_idx == 0:
                ticker = self._current_text.strip()
                if ticker and re.match(r"^[A-Z.]+$", ticker):
                    self._tickers.append(ticker)
            self._col_idx += 1
        elif tag == "tr":
            self._in_row = False
        elif tag == "tbody":
            self._in_tbody = False
        elif tag == "table" and self._in_table:
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._col_idx == 0:
            self._current_text += data

    @property
    def tickers(self) -> list[str]:
        return self._tickers


def _fetch_sp500_from_wikipedia(logger: LogFn) -> list[str]:
    try:
        resp = requests.get(
            _SP500_URL,
            timeout=_SP500_FETCH_TIMEOUT,
            headers={"User-Agent": "StonkApp/1.0 (earnings-universe-builder)"},
        )
        resp.raise_for_status()
        parser = _WikiTableParser()
        parser.feed(resp.text)
        tickers = parser.tickers
        if len(tickers) >= 400:
            logger(f"[universe_discovery] S&P 500: fetched {len(tickers)} tickers from Wikipedia")
            return tickers
        logger(f"[universe_discovery] S&P 500: Wikipedia returned only {len(tickers)} tickers, using embedded fallback")
        return list(_SP500_FALLBACK)
    except Exception as exc:
        logger(f"[universe_discovery] S&P 500: Wikipedia fetch failed ({exc}), using embedded fallback")
        return list(_SP500_FALLBACK)


# ---------------------------------------------------------------------------
# SQLite cache
# ---------------------------------------------------------------------------

@contextmanager
def _connect(db_path: str):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        yield conn
        conn.commit()
    finally:
        conn.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS universe_cache (
    ticker TEXT NOT NULL,
    earnings_date TEXT,
    avg_volume REAL,
    price REAL,
    optionable INTEGER DEFAULT 0,
    cached_date TEXT NOT NULL,
    source TEXT,
    PRIMARY KEY (ticker, cached_date)
);

CREATE TABLE IF NOT EXISTS constituent_cache (
    ticker TEXT NOT NULL,
    list_name TEXT NOT NULL,
    cached_date TEXT NOT NULL,
    PRIMARY KEY (ticker, list_name)
);
"""


def _ensure_schema(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def _db_path() -> str:
    return str(getattr(config, "UNIVERSE_DISCOVERY_DB_PATH", None) or _default_db_path())


def _default_db_path() -> str:
    import os
    if os.path.isdir("/app/data"):
        return "/app/data/universe_discovery.sqlite3"
    return "data/universe_discovery.sqlite3"


# ---------------------------------------------------------------------------
# Constituent list management
# ---------------------------------------------------------------------------

def _get_constituent_tickers(logger: LogFn) -> list[str]:
    db = _db_path()
    _ensure_schema(db)
    refresh_days = int(getattr(config, "UNIVERSE_DISCOVERY_CONSTITUENT_REFRESH_DAYS", 7) or 7)
    cutoff = (date.today() - timedelta(days=refresh_days)).isoformat()

    with _connect(db) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt, MAX(cached_date) AS latest FROM constituent_cache WHERE list_name = 'sp500'"
        ).fetchone()
        count = row["cnt"] if row else 0
        latest = row["latest"] if row else None

    if count >= 100 and latest and latest >= cutoff:
        with _connect(db) as conn:
            rows = conn.execute("SELECT DISTINCT ticker FROM constituent_cache").fetchall()
            cached = [r["ticker"] for r in rows]
        logger(f"[universe_discovery] constituent cache hit: {len(cached)} tickers (refreshed {latest})")
        return cached

    sp500 = _fetch_sp500_from_wikipedia(logger)
    russell = list(_RUSSELL_SUPPLEMENT)
    combined = list(dict.fromkeys(sp500 + russell))

    today_str = date.today().isoformat()
    with _connect(db) as conn:
        conn.execute("DELETE FROM constituent_cache")
        for ticker in combined:
            conn.execute(
                "INSERT OR REPLACE INTO constituent_cache (ticker, list_name, cached_date) VALUES (?, ?, ?)",
                (ticker, "sp500" if ticker in sp500 else "russell_supplement", today_str),
            )
    logger(f"[universe_discovery] constituent list rebuilt: {len(sp500)} S&P 500 + {len(russell)} Russell supplement = {len(combined)} unique")
    return combined


def get_constituent_ticker_set(log_print: LogFn | None = None) -> set[str]:
    """Return the full set of constituent tickers as uppercase strings."""
    logger = log_print or (lambda msg: None)
    try:
        tickers = _get_constituent_tickers(logger)
        return {t.upper().strip() for t in tickers if t}
    except Exception as exc:
        logger(f"[universe_discovery] constituent_ticker_set failed: {exc}")
        return set()


# ---------------------------------------------------------------------------
# Universe cache build + retrieval
# ---------------------------------------------------------------------------

def get_earnings_candidates(
    earnings_events: dict[str, dict[str, Any]] | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
    exclude_held: list[str] | None = None,
    max_tickers: int | None = None,
    log_print: LogFn | None = None,
) -> dict[str, Any]:
    """Return a curated list of liquid, optionable tickers with upcoming earnings.

    Returns a dict with `tickers`, `items`, `source`, `has_data`, `summary`.
    """
    logger = log_print or (lambda msg: print(msg, flush=True))
    max_tickers = max_tickers or int(getattr(config, "EARNINGS_DISCOVERY_UNIVERSE_MAX_CANDIDATES", 50) or 50)

    if not getattr(config, "UNIVERSE_DISCOVERY_ENABLED", True):
        logger("[universe_discovery] disabled by UNIVERSE_DISCOVERY_ENABLED=false")
        return _empty_result("disabled")

    today = date.today()
    today_str = today.isoformat()
    db = _db_path()
    _ensure_schema(db)

    cached = _load_cache(db, today_str)
    if cached is not None:
        logger(f"[universe_discovery] cache hit: {len(cached)} tickers for {today_str}")
        items = cached
    else:
        try:
            items = _build_cache(
                db, today_str, earnings_events, window_start, window_end, logger,
            )
        except Exception as exc:
            logger(f"[universe_discovery] cache build failed (non-fatal): {exc}")
            return _empty_result(f"cache_build_error: {exc}")

    exclude_set = {t.upper().strip() for t in (exclude_held or []) if t}
    filtered = [item for item in items if item["ticker"] not in exclude_set]

    filtered.sort(key=lambda x: (x.get("avg_volume") or 0), reverse=True)
    result_items = filtered[:max_tickers]

    logger(
        f"[universe_discovery] returning {len(result_items)} candidates "
        f"({len(items)} cached, {len(exclude_set)} held excluded)"
    )

    return {
        "source": "universe_discovery_v1",
        "has_data": bool(result_items),
        "enabled": True,
        "cached_date": today_str,
        "items": result_items,
        "tickers": [item["ticker"] for item in result_items],
        "summary": {
            "total_cached": len(items),
            "held_excluded": len(exclude_set),
            "returned": len(result_items),
            "max_tickers": max_tickers,
        },
    }


def _load_cache(db_path: str, date_str: str) -> list[dict[str, Any]] | None:
    ttl_hours = int(getattr(config, "UNIVERSE_DISCOVERY_CACHE_TTL_HOURS", 20) or 20)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM universe_cache WHERE cached_date = ?", (date_str,)
        ).fetchall()
    if not rows:
        return None
    return [dict(r) for r in rows]


def _build_cache(
    db_path: str,
    date_str: str,
    earnings_events: dict[str, dict[str, Any]] | None,
    window_start: date | None,
    window_end: date | None,
    logger: LogFn,
) -> list[dict[str, Any]]:
    constituents = _get_constituent_tickers(logger)
    constituent_set = {t.upper() for t in constituents}

    today = date.today()
    ws = window_start or (today + timedelta(days=int(config.EARNINGS_DISCOVERY_START_DAYS or 4)))
    we = window_end or (today + timedelta(days=int(config.EARNINGS_DISCOVERY_END_DAYS or 21)))

    earnings_map = _build_earnings_map(earnings_events, ws, we, logger)

    tickers_with_earnings = [t for t in constituent_set if t in earnings_map]
    logger(
        f"[universe_discovery] {len(tickers_with_earnings)} constituents have earnings "
        f"in {ws.isoformat()}..{we.isoformat()} window"
    )

    min_price = float(getattr(config, "UNIVERSE_MIN_PRICE", 10.0) or 10.0)
    max_price = float(getattr(config, "UNIVERSE_MAX_PRICE", 1000.0) or 1000.0)
    min_avg_vol = int(getattr(config, "UNIVERSE_MIN_AVG_VOLUME", 500000) or 500000)

    items: list[dict[str, Any]] = []
    new_provider_calls = 0

    sorted_tickers = sorted(tickers_with_earnings)
    quotes = _batch_quotes(sorted_tickers, logger)

    for ticker in sorted_tickers:
        event = earnings_map.get(ticker) or {}
        price, avg_volume = _get_price_volume(ticker, logger, quotes=quotes)
        if price is not None:
            new_provider_calls += 1

        price_val = price or 0.0
        vol_val = avg_volume or 0

        if price is not None and (price_val < min_price or price_val > max_price):
            continue
        if avg_volume is not None and vol_val < min_avg_vol:
            continue

        items.append({
            "ticker": ticker,
            "earnings_date": event.get("earnings_date") or event.get("date"),
            "avg_volume": avg_volume,
            "price": price,
            "optionable": 1,
            "cached_date": date_str,
            "source": "universe_discovery_v1",
        })

    with _connect(db_path) as conn:
        conn.execute("DELETE FROM universe_cache WHERE cached_date = ?", (date_str,))
        for item in items:
            conn.execute(
                "INSERT OR REPLACE INTO universe_cache "
                "(ticker, earnings_date, avg_volume, price, optionable, cached_date, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    item["ticker"], item.get("earnings_date"), item.get("avg_volume"),
                    item.get("price"), item.get("optionable", 0), date_str, item.get("source"),
                ),
            )

    logger(
        f"[universe_discovery] cache built: {len(items)} tickers for {date_str} "
        f"({new_provider_calls} new quote lookups)"
    )
    return items


def _build_earnings_map(
    earnings_events: dict[str, dict[str, Any]] | None,
    window_start: date,
    window_end: date,
    logger: LogFn,
) -> dict[str, dict[str, Any]]:
    if not earnings_events:
        try:
            from app.providers.earnings_provider import get_provider
            provider = get_provider()
            if provider.is_configured:
                raw_events = provider.get_earnings_calendar_range(
                    window_start, window_end,
                )
                result: dict[str, dict[str, Any]] = {}
                for ev in raw_events:
                    ticker = str(ev.get("ticker") or ev.get("symbol") or "").upper().strip()
                    if ticker:
                        result[ticker] = ev
                logger(f"[universe_discovery] fetched {len(result)} earnings events from provider")
                return result
            logger("[universe_discovery] earnings provider not configured, skipping earnings cross-reference")
            return {}
        except Exception as exc:
            logger(f"[universe_discovery] earnings provider fetch failed: {exc}")
            return {}

    result: dict[str, dict[str, Any]] = {}
    for ticker, event in earnings_events.items():
        if not isinstance(event, dict):
            continue
        ed = _parse_date(event.get("earnings_date") or event.get("date"))
        if ed and window_start <= ed <= window_end:
            result[ticker.upper().strip()] = event
    return result


def _batch_quotes(tickers: list[str], logger: LogFn, chunk_size: int = 250) -> dict[str, dict[str, Any]]:
    """Fetch quotes for many tickers via Tradier's batched quotes endpoint.

    Tradier accepts a comma-separated symbol list per request; chunking keeps
    each request within a safe URL length for large (600+) universes.
    """
    if not tickers:
        return {}
    try:
        from app.providers.tradier_provider import TradierProvider
        provider = TradierProvider()
    except Exception as exc:
        logger(f"[universe_discovery] batch quote provider init failed (non-fatal): {exc}")
        return {}
    if not provider.is_configured:
        return {}
    quotes: dict[str, dict[str, Any]] = {}
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        try:
            quotes.update(provider.get_quotes(chunk) or {})
        except Exception as exc:
            logger(f"[universe_discovery] batch quote fetch failed for chunk (non-fatal): {exc}")
    return quotes


def _get_price_volume(ticker: str, logger: LogFn, quotes: dict[str, dict[str, Any]] | None = None) -> tuple[float | None, float | None]:
    cached = (quotes or {}).get(ticker.upper().strip())
    if cached:
        price = _first_num(cached.get("last"), cached.get("close"), cached.get("prevclose"))
        avg_vol = _first_num(cached.get("average_volume"), cached.get("volume"))
        return price, avg_vol
    return None, None


def _first_num(*values: Any) -> float | None:
    for v in values:
        try:
            if v is not None and v != "":
                f = float(v)
                if f > 0:
                    return f
        except (TypeError, ValueError):
            continue
    return None


def _parse_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def get_skew_candidates(
    exclude_held: list[str] | None = None,
    max_tickers: int | None = None,
    log_print: LogFn | None = None,
) -> list[str]:
    """Return liquid constituent tickers ranked for skew scanning.

    No earnings requirement — returns any constituent that passes the
    price/volume filter, ranked by average volume as a liquidity proxy.
    True per-ticker implied volatility would require an options-chain call
    per ticker, which is too expensive to run across the full constituent
    universe; average volume from a single batched quote call is the
    cheapest available signal.
    """
    logger = log_print or (lambda msg: print(msg, flush=True))
    max_tickers = max_tickers or int(getattr(config, "SKEW_UNIVERSE_MAX_CANDIDATES", 50) or 50)

    if not getattr(config, "UNIVERSE_DISCOVERY_ENABLED", True):
        logger("[universe_discovery] skew candidates disabled by UNIVERSE_DISCOVERY_ENABLED=false")
        return []

    try:
        constituents = _get_constituent_tickers(logger)
    except Exception as exc:
        logger(f"[universe_discovery] skew constituent fetch failed (non-fatal): {exc}")
        return []

    exclude_set = {t.upper().strip() for t in (exclude_held or []) if t}
    min_price = float(getattr(config, "UNIVERSE_MIN_PRICE", 10.0) or 10.0)
    max_price = float(getattr(config, "UNIVERSE_MAX_PRICE", 1000.0) or 1000.0)
    min_avg_vol = int(getattr(config, "UNIVERSE_MIN_AVG_VOLUME", 500000) or 500000)

    candidate_tickers = [t.upper().strip() for t in constituents if t.upper().strip() not in exclude_set]
    quotes = _batch_quotes(candidate_tickers, logger)

    scored: list[tuple[str, float]] = []
    for t in candidate_tickers:
        price, avg_volume = _get_price_volume(t, logger, quotes=quotes)
        price_val = price or 0.0
        vol_val = avg_volume or 0
        if price is not None and (price_val < min_price or price_val > max_price):
            continue
        if avg_volume is not None and vol_val < min_avg_vol:
            continue
        scored.append((t, vol_val))

    scored.sort(key=lambda x: x[1], reverse=True)
    result = [t for t, _vol in scored[:max_tickers]]
    logger(
        f"[universe_discovery] skew candidates: {len(result)} of {len(scored)} eligible "
        f"({len(exclude_set)} held excluded, cap {max_tickers}, ranked by avg volume)"
    )
    return result


def get_ff_candidates(
    existing_tickers: list[str] | None = None,
    max_tickers: int | None = None,
    log_print: LogFn | None = None,
) -> list[str]:
    """Return constituent tickers for Forward Factor scanning.

    Established names (previously observed in FF journal) are prioritised.
    New discovery tickers are interleaved at a 1-in-5 ratio so the FF
    scanner gradually explores fresh names without overwhelming the budget.
    """
    logger = log_print or (lambda msg: print(msg, flush=True))
    max_tickers = max_tickers or int(getattr(config, "FF_UNIVERSE_MAX_TICKERS", 40) or 40)

    if not getattr(config, "UNIVERSE_DISCOVERY_ENABLED", True):
        logger("[universe_discovery] FF candidates disabled by UNIVERSE_DISCOVERY_ENABLED=false")
        return list(dict.fromkeys(existing_tickers or []))[:max_tickers]

    existing_set = set()
    ordered: list[str] = []
    for t in (existing_tickers or []):
        tu = t.upper().strip()
        if tu and tu not in existing_set:
            existing_set.add(tu)
            ordered.append(tu)

    try:
        constituents = _get_constituent_tickers(logger)
    except Exception as exc:
        logger(f"[universe_discovery] FF constituent fetch failed (non-fatal): {exc}")
        return ordered[:max_tickers]

    journal_scores = _load_ff_journal_scores(logger)

    min_price = float(getattr(config, "UNIVERSE_MIN_PRICE", 10.0) or 10.0)
    max_price = float(getattr(config, "UNIVERSE_MAX_PRICE", 1000.0) or 1000.0)
    min_avg_vol = int(getattr(config, "UNIVERSE_MIN_AVG_VOLUME", 500000) or 500000)

    established: list[tuple[str, float]] = []
    new_discovery: list[tuple[str, float]] = []

    candidate_tickers = [t.upper().strip() for t in constituents if t.upper().strip() not in existing_set]
    quotes = _batch_quotes(candidate_tickers, logger)

    for t in candidate_tickers:
        price, avg_volume = _get_price_volume(t, logger, quotes=quotes)
        price_val = price or 0.0
        vol_val = avg_volume or 0
        if price is not None and (price_val < min_price or price_val > max_price):
            continue
        if avg_volume is not None and vol_val < min_avg_vol:
            continue
        if t in journal_scores:
            established.append((t, journal_scores[t]))
        else:
            new_discovery.append((t, vol_val))

    established.sort(key=lambda x: x[1], reverse=True)
    new_discovery.sort(key=lambda x: x[1], reverse=True)

    remaining = max_tickers - len(ordered)
    if remaining <= 0:
        logger(f"[universe_discovery] FF candidates: existing tickers fill budget ({len(ordered)}/{max_tickers})")
        return ordered[:max_tickers]

    est_iter = iter(established)
    new_iter = iter(new_discovery)
    count = 0
    while remaining > 0:
        if count > 0 and count % 5 == 0:
            item = next(new_iter, None)
            if item:
                ordered.append(item[0])
                remaining -= 1
                count += 1
                continue
        item = next(est_iter, None)
        if item is None:
            item = next(new_iter, None)
        if item is None:
            break
        ordered.append(item[0])
        remaining -= 1
        count += 1

    logger(
        f"[universe_discovery] FF candidates: {len(ordered)} total "
        f"({len(existing_set)} existing + {len(established)} established + {len(new_discovery)} new discovery, cap {max_tickers})"
    )
    return ordered[:max_tickers]


def _load_ff_journal_scores(logger: LogFn) -> dict[str, float]:
    try:
        db_path = str(getattr(config, "FF_JOURNAL_DB_PATH", None) or "")
        if not db_path or not Path(db_path).exists():
            return {}
        with _connect(db_path) as conn:
            rows = conn.execute(
                "SELECT ticker, AVG(signal_score) AS avg_score, COUNT(*) AS obs "
                "FROM ff_journal WHERE signal_score IS NOT NULL "
                "GROUP BY ticker ORDER BY avg_score DESC"
            ).fetchall()
        return {r["ticker"]: float(r["avg_score"]) for r in rows if r["ticker"]}
    except Exception as exc:
        logger(f"[universe_discovery] FF journal score load failed (non-fatal): {exc}")
        return {}


def _empty_result(reason: str) -> dict[str, Any]:
    return {
        "source": "universe_discovery_v1",
        "has_data": False,
        "enabled": False,
        "cached_date": None,
        "items": [],
        "tickers": [],
        "summary": {"total_cached": 0, "held_excluded": 0, "returned": 0, "reason": reason},
    }
