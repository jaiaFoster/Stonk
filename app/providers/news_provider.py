"""
app/providers/news_provider.py — Structured, relevance-scored news fetching.

This provider upgrades news from simple headline strings into structured article
objects shaped like:

{
    "ticker": "META",
    "title": "...",
    "source": "...",
    "url": "...",
    "published_at": "...",
    "relevance_score": 0.82,
}

The provider intentionally keeps the public function name `get_news_for_tickers`
so the rest of the app can continue calling the same API while receiving richer
data for future advisor scoring and storage.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import requests

from app import config
from app.models.news_item import NewsItem
from app.utils.log_safety import sanitize_for_log

NEWS_API_URL = "https://newsapi.org/v2/everything"
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_PAGE_SIZE = 10
DEFAULT_MAX_ARTICLES_PER_TICKER = 3
MIN_RELEVANCE_SCORE = 0.35

# Company-aware profiles prevent ambiguous ticker-only searches like:
# - HOOD -> kitchen range hood articles
# - META -> meta-analysis / metadata articles
# - SOL  -> solar / general unrelated terms
TICKER_PROFILES: dict[str, dict[str, Any]] = {
    "NVDA": {
        "company": "NVIDIA",
        "aliases": ["Nvidia", "NVIDIA Corporation"],
        "search_terms": ["NVIDIA", "NVDA stock", "Nvidia shares"],
    },
    "ORCL": {
        "company": "Oracle",
        "aliases": ["Oracle Corporation"],
        "search_terms": ["Oracle", "ORCL stock", "Oracle shares"],
    },
    "PYPL": {
        "company": "PayPal",
        "aliases": ["PayPal Holdings"],
        "search_terms": ["PayPal", "PYPL stock", "PayPal shares"],
    },
    "GOOGL": {
        "company": "Alphabet",
        "aliases": ["Google", "Alphabet Inc"],
        "search_terms": ["Alphabet", "Google", "GOOGL stock", "Google shares"],
    },
    "SOFI": {
        "company": "SoFi Technologies",
        "aliases": ["SoFi"],
        "search_terms": ["SoFi Technologies", "SOFI stock", "SoFi shares"],
    },
    "QBTS": {
        "company": "D-Wave Quantum",
        "aliases": ["D-Wave", "D Wave Quantum"],
        "search_terms": ["D-Wave Quantum", "QBTS stock", "D-Wave shares"],
    },
    "HOOD": {
        "company": "Robinhood Markets",
        "aliases": ["Robinhood"],
        "search_terms": ["Robinhood Markets", "HOOD stock", "Robinhood stock"],
        "irrelevant_terms": [
            "range hood",
            "kitchen hood",
            "cooker hood",
            "vent hood",
            "extractor hood",
            "neighborhood",
            "hoodie",
            "hooded",
        ],
    },
    "AMZN": {
        "company": "Amazon",
        "aliases": ["Amazon.com", "Amazon.com Inc"],
        "search_terms": ["Amazon", "AMZN stock", "Amazon shares"],
    },
    "IBM": {
        "company": "IBM",
        "aliases": ["International Business Machines"],
        "search_terms": ["IBM", "IBM stock", "International Business Machines"],
    },
    "META": {
        "company": "Meta Platforms",
        "aliases": ["Facebook", "Instagram", "WhatsApp"],
        "search_terms": ["Meta Platforms", "META stock", "Meta shares", "Facebook parent"],
        "irrelevant_terms": [
            "meta-analysis",
            "metaanalysis",
            "metadata",
            "metabolic",
            "metabolism",
            "metastatic",
            "metastasis",
            "metaheuristic",
            "causal-toolkit",
            "pypi",
            "python package",
        ],
    },
    "FANUY": {
        "company": "FANUC",
        "aliases": ["Fanuc Corporation"],
        "search_terms": ["FANUC", "FANUY stock", "Fanuc shares"],
    },
    "SMR": {
        "company": "NuScale Power",
        "aliases": ["NuScale"],
        "search_terms": ["NuScale Power", "SMR stock", "NuScale shares"],
        "irrelevant_terms": ["small modular reactor"],
    },
    "JPM": {
        "company": "JPMorgan Chase",
        "aliases": ["JPMorgan", "JP Morgan", "Chase"],
        "search_terms": ["JPMorgan Chase", "JPM stock", "JPMorgan shares"],
    },
    "NKE": {
        "company": "Nike",
        "aliases": ["Nike Inc"],
        "search_terms": ["Nike", "NKE stock", "Nike shares"],
    },
    "VST": {
        "company": "Vistra",
        "aliases": ["Vistra Corp"],
        "search_terms": ["Vistra", "VST stock", "Vistra shares"],
    },
    "BTC": {
        "company": "Bitcoin",
        "aliases": ["BTC"],
        "search_terms": ["Bitcoin", "BTC crypto", "Bitcoin ETF"],
        "asset_type": "crypto",
    },
    "SOL": {
        "company": "Solana",
        "aliases": ["SOL"],
        "search_terms": ["Solana", "SOL crypto", "Solana blockchain"],
        "asset_type": "crypto",
        "irrelevant_terms": ["solar", "solenoid", "solution", "sol gel"],
    },
}

MARKET_TERMS = {
    "analyst",
    "analysts",
    "bank",
    "buyback",
    "ceo",
    "cfo",
    "company",
    "dividend",
    "downgrade",
    "earnings",
    "eps",
    "estimate",
    "estimates",
    "forecast",
    "guidance",
    "investment",
    "investor",
    "investors",
    "market",
    "markets",
    "nasdaq",
    "nyse",
    "option",
    "options",
    "price target",
    "profit",
    "quarter",
    "rating",
    "revenue",
    "sec",
    "share",
    "shareholder",
    "shareholders",
    "shares",
    "stock",
    "stocks",
    "upgrade",
    "wall street",
}

CRYPTO_TERMS = {
    "bitcoin",
    "blockchain",
    "crypto",
    "cryptocurrency",
    "defi",
    "digital asset",
    "etf",
    "exchange",
    "solana",
    "token",
    "tokens",
}

GENERAL_IRRELEVANT_TERMS = {
    "recipe",
    "recipes",
    "sports highlights",
    "movie review",
    "celebrity gossip",
}


StructuredNewsMap = dict[str, list[dict[str, Any]]]


class NewsProviderRateLimitError(RuntimeError):
    """Raised when NewsAPI rate limits the current run."""


class NewsProviderAccessError(RuntimeError):
    """Raised when NewsAPI denies or rejects the request."""


def get_news_for_tickers(tickers: list[str]) -> StructuredNewsMap:
    """
    Fetch structured, relevance-scored news for each ticker.

    The provider is intentionally API-budget aware:
    - caps the number of tickers fetched per run with NEWS_MAX_TICKERS_PER_RUN
    - uses smaller page sizes by default
    - stops immediately on HTTP 429 instead of hammering the API
    - never prints API keys or full provider URLs in logs
    """
    normalized_tickers = [str(t).upper().strip() for t in tickers if str(t).strip()]
    news_map: StructuredNewsMap = {ticker: [] for ticker in normalized_tickers}

    if not config.NEWS_API_KEY:
        print("NEWS_API_KEY is not set; skipping news fetch.", flush=True)
        return news_map

    max_tickers = max(0, int(getattr(config, "NEWS_MAX_TICKERS_PER_RUN", 8) or 8))
    tickers_to_fetch = normalized_tickers[:max_tickers] if max_tickers else []

    if len(tickers_to_fetch) < len(normalized_tickers):
        print(
            f"NewsAPI budget guard: fetching {len(tickers_to_fetch)}/{len(normalized_tickers)} ticker(s) "
            f"this run. Set NEWS_MAX_TICKERS_PER_RUN to adjust.",
            flush=True,
        )

    end_date = date.today()
    start_date = end_date - timedelta(days=DEFAULT_LOOKBACK_DAYS)

    for ticker in tickers_to_fetch:
        try:
            raw_articles = _fetch_raw_articles(
                ticker=ticker,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            )
            scored_articles = _score_and_filter_articles(ticker, raw_articles)
            news_map[ticker] = scored_articles[:DEFAULT_MAX_ARTICLES_PER_TICKER]

        except NewsProviderRateLimitError as e:
            print(f"News fetch stopped: {sanitize_for_log(e, [config.NEWS_API_KEY])}", flush=True)
            break
        except Exception as e:
            print(f"News fetch error for {ticker}: {sanitize_for_log(e, [config.NEWS_API_KEY])}", flush=True)
            news_map[ticker] = []

    return news_map


def get_headlines_for_tickers(tickers: list[str]) -> dict[str, list[str]]:
    """
    Compatibility helper for any old code that still expects title strings.

    The main app now uses structured news dictionaries, but this helper makes it
    easy to flatten the new format if needed.
    """
    structured_news = get_news_for_tickers(tickers)
    flattened: dict[str, list[str]] = {}

    for ticker, articles in structured_news.items():
        flattened[ticker] = [str(article.get("title", "")) for article in articles if article.get("title")]
        if not flattened[ticker]:
            flattened[ticker] = ["No relevant company news found."]

    return flattened


def _fetch_raw_articles(ticker: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    query = build_query_for_ticker(ticker)
    page_size = max(1, int(getattr(config, "NEWS_PAGE_SIZE", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE))

    try:
        response = requests.get(
            NEWS_API_URL,
            params={
                "q": query,
                "from": start_date,
                "to": end_date,
                "sortBy": "relevancy",
                "language": "en",
                "pageSize": page_size,
            },
            headers={"X-Api-Key": config.NEWS_API_KEY or ""},
            timeout=10,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"NewsAPI request failed: {sanitize_for_log(e, [config.NEWS_API_KEY])}") from e

    if response.status_code == 429:
        raise NewsProviderRateLimitError(
            "NewsAPI returned HTTP 429 Too Many Requests. Skipping remaining news calls for this run."
        )

    if response.status_code in {401, 403}:
        raise NewsProviderAccessError(
            f"NewsAPI returned HTTP {response.status_code}. Check NEWS_API_KEY and plan access."
        )

    if response.status_code >= 400:
        raise RuntimeError(_safe_provider_error("NewsAPI", response))

    data = response.json()
    if data.get("status") == "error":
        message = sanitize_for_log(data.get("message", "NewsAPI returned an unknown error"), [config.NEWS_API_KEY])
        code = data.get("code", "unknown")
        raise RuntimeError(f"NewsAPI error {code}: {message}")

    articles = data.get("articles", [])
    return articles if isinstance(articles, list) else []


def _safe_provider_error(provider_name: str, response: requests.Response) -> str:
    try:
        data = response.json()
        message = data.get("message") or data.get("error") or response.reason
    except Exception:
        message = response.reason
    return f"{provider_name} returned HTTP {response.status_code}: {sanitize_for_log(message, [config.NEWS_API_KEY])}"


def build_query_for_ticker(ticker: str) -> str:
    """Build a NewsAPI query that is company-aware instead of ticker-only."""
    profile = _profile_for_ticker(ticker)
    search_terms = profile.get("search_terms") or [ticker]
    quoted_terms = " OR ".join(_quote_term(term) for term in search_terms)

    if profile.get("asset_type") == "crypto":
        context_terms = "crypto OR cryptocurrency OR blockchain OR market OR ETF OR token"
    else:
        context_terms = "stock OR shares OR earnings OR revenue OR analyst OR market OR company"

    return f"({quoted_terms}) AND ({context_terms})"


def _score_and_filter_articles(ticker: str, articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profile = _profile_for_ticker(ticker)
    seen_titles: set[str] = set()
    scored: list[dict[str, Any]] = []

    for article in articles:
        title = str(article.get("title") or "").strip()
        if not title:
            continue

        title_key = _normalize_title(title)
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        score = calculate_relevance_score(ticker, article, profile)
        if score < MIN_RELEVANCE_SCORE:
            continue

        source = article.get("source") or {}
        source_name = source.get("name") if isinstance(source, dict) else None

        item = NewsItem(
            ticker=ticker,
            title=title,
            source=str(source_name or "Unknown source"),
            url=str(article.get("url") or ""),
            published_at=str(article.get("publishedAt") or ""),
            relevance_score=score,
        )
        scored.append(item.to_dict())

    scored.sort(
        key=lambda item: (
            float(item.get("relevance_score", 0.0) or 0.0),
            str(item.get("published_at", "")),
        ),
        reverse=True,
    )
    return scored


def calculate_relevance_score(
    ticker: str,
    article: dict[str, Any],
    profile: dict[str, Any] | None = None,
) -> float:
    """
    Score article relevance from 0.0 to 1.0.

    This is intentionally transparent and lightweight. It is not a machine
    learning model; it is a rule-based filter to keep obviously irrelevant
    headlines from polluting future advisor logic.
    """
    profile = profile or _profile_for_ticker(ticker)
    title = str(article.get("title") or "")
    description = str(article.get("description") or "")
    content = str(article.get("content") or "")
    source = article.get("source") or {}
    source_name = source.get("name") if isinstance(source, dict) else ""

    full_text = f"{title} {description} {content} {source_name}".lower()
    title_text = title.lower()

    company = str(profile.get("company") or ticker)
    aliases = [company, *profile.get("aliases", []), ticker]
    asset_type = profile.get("asset_type", "stock")

    score = 0.0

    # Company/asset identity match is the most important signal.
    for alias in aliases:
        alias_lower = str(alias).lower().strip()
        if not alias_lower:
            continue
        if alias_lower in title_text:
            score += 0.38
            break
        if alias_lower in full_text:
            score += 0.24
            break

    # Exact ticker match helps, but only modestly because ticker strings can be ambiguous.
    if _contains_exact_term(full_text, ticker.lower()):
        score += 0.12

    # Business/market context. This prevents random mentions from ranking highly.
    context_terms = CRYPTO_TERMS if asset_type == "crypto" else MARKET_TERMS
    matched_context_terms = [term for term in context_terms if term in full_text]
    score += min(0.28, len(matched_context_terms) * 0.055)

    # NewsAPI source/title match with clear stock phrasing gets a small boost.
    if any(phrase in full_text for phrase in ("stock", "shares", "earnings", "price target")):
        score += 0.08

    if asset_type == "crypto" and any(term in full_text for term in CRYPTO_TERMS):
        score += 0.08

    # Penalize known false positives.
    irrelevant_terms = set(profile.get("irrelevant_terms", [])) | GENERAL_IRRELEVANT_TERMS
    for term in irrelevant_terms:
        if term in full_text:
            score -= 0.45

    # Special case: ticker META is extremely noisy in scientific / software contexts.
    if ticker.upper() == "META" and re.search(r"\bmeta[- ]?(analysis|analytic|data|heuristic|static|stasis|bolic)", full_text):
        score -= 0.45

    # Special case: HOOD is noisy because of appliance / clothing language.
    if ticker.upper() == "HOOD" and re.search(r"\b(range|kitchen|cooker|vent|extractor|hoodie|hooded)\b", full_text):
        score -= 0.45

    return round(max(0.0, min(1.0, score)), 2)


def _profile_for_ticker(ticker: str) -> dict[str, Any]:
    ticker = ticker.upper().strip()
    return TICKER_PROFILES.get(
        ticker,
        {
            "company": ticker,
            "aliases": [],
            "search_terms": [f"{ticker} stock", f"{ticker} shares", ticker],
        },
    )


def _quote_term(term: str) -> str:
    escaped = str(term).replace('"', "")
    return f'"{escaped}"'


def _contains_exact_term(text: str, term: str) -> bool:
    if not term:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text, flags=re.IGNORECASE) is not None


def _normalize_title(title: str) -> str:
    lowered = title.lower().strip()
    lowered = re.sub(r"\s+", " ", lowered)
    lowered = re.sub(r"[^a-z0-9 ]+", "", lowered)
    return lowered
