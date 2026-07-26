from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
MAX_NEWS_RESPONSE_BYTES = 2_000_000
MAX_EARNINGS_RESPONSE_BYTES = 5_000_000


class MarketIntelligenceError(RuntimeError):
    """Raised when a provider response cannot safely be used."""


@dataclass(frozen=True)
class NewsRecord:
    external_id: str
    symbol: str | None
    headline: str
    source: str
    published_at: datetime
    sentiment: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class EarningsRecord:
    external_id: str
    symbol: str
    report_date: date
    fiscal_date_ending: date | None
    estimate: float | None
    currency: str | None
    source: str
    payload: dict[str, Any]


class AlphaVantageClient:
    """Fetches one bounded global news feed and one earnings calendar per refresh."""

    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None) -> None:
        if not api_key.strip():
            raise ValueError("Alpha Vantage API key is required")
        self.api_key = api_key
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            follow_redirects=False,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def fetch_news(
        self,
        *,
        symbols: set[str],
        lookback_hours: int,
        limit: int,
    ) -> list[NewsRecord]:
        now = datetime.now(UTC)
        response = await self.client.get(
            ALPHA_VANTAGE_URL,
            params={
                "function": "NEWS_SENTIMENT",
                "time_from": (now - timedelta(hours=lookback_hours)).strftime("%Y%m%dT%H%M"),
                "sort": "LATEST",
                "limit": min(limit, 1_000),
                "apikey": self.api_key,
            },
        )
        self._validate_response(response, MAX_NEWS_RESPONSE_BYTES)
        try:
            body = response.json()
        except ValueError as exc:
            raise MarketIntelligenceError("Alpha Vantage returned invalid news JSON") from exc
        if not isinstance(body, dict):
            raise MarketIntelligenceError("Alpha Vantage returned an invalid news payload")
        self._raise_provider_error(body)
        feed = body.get("feed", [])
        if not isinstance(feed, list):
            raise MarketIntelligenceError("Alpha Vantage news feed is not a list")
        allowed_symbols = {symbol.upper() for symbol in symbols}
        records: list[NewsRecord] = []
        for raw in feed[:limit]:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            title = self._clean_text(item.get("title"), 500)
            source = self._clean_text(item.get("source"), 100)
            published_at = self._parse_timestamp(item.get("time_published"))
            if not title or not source or published_at is None:
                continue
            summary = self._clean_text(item.get("summary"), 1_000)
            url_hash = hashlib.sha256(str(item.get("url", "")).encode()).hexdigest()
            ticker_rows = item.get("ticker_sentiment", [])
            matched = False
            if isinstance(ticker_rows, list):
                for ticker_raw in ticker_rows:
                    if not isinstance(ticker_raw, dict):
                        continue
                    ticker = str(ticker_raw.get("ticker", "")).upper()
                    if ticker not in allowed_symbols:
                        continue
                    matched = True
                    records.append(
                        self._news_record(
                            symbol=ticker,
                            title=title,
                            source=source,
                            published_at=published_at,
                            summary=summary,
                            url_hash=url_hash,
                            sentiment=self._clean_text(
                                ticker_raw.get("ticker_sentiment_label"), 16
                            ),
                            relevance=self._finite_float(ticker_raw.get("relevance_score")),
                            sentiment_score=self._finite_float(
                                ticker_raw.get("ticker_sentiment_score")
                            ),
                        )
                    )
            topics = item.get("topics", [])
            market_wide = isinstance(topics, list) and any(
                isinstance(topic, dict)
                and str(topic.get("topic", "")).lower().replace(" - ", "_").replace(" ", "_")
                in {"economy_macro", "economy_monetary", "financial_markets"}
                for topic in topics
            )
            if not matched and market_wide:
                records.append(
                    self._news_record(
                        symbol=None,
                        title=title,
                        source=source,
                        published_at=published_at,
                        summary=summary,
                        url_hash=url_hash,
                        sentiment=self._clean_text(item.get("overall_sentiment_label"), 16),
                        relevance=None,
                        sentiment_score=self._finite_float(item.get("overall_sentiment_score")),
                    )
                )
        return records

    async def fetch_earnings(
        self, *, symbols: set[str], horizon: str = "3month"
    ) -> list[EarningsRecord]:
        response = await self.client.get(
            ALPHA_VANTAGE_URL,
            params={
                "function": "EARNINGS_CALENDAR",
                "horizon": horizon,
                "apikey": self.api_key,
            },
        )
        self._validate_response(response, MAX_EARNINGS_RESPONSE_BYTES)
        text = response.text
        if text.lstrip().startswith("{"):
            try:
                body = response.json()
            except ValueError as exc:
                raise MarketIntelligenceError(
                    "Alpha Vantage returned invalid earnings JSON"
                ) from exc
            if isinstance(body, dict):
                self._raise_provider_error(body)
            raise MarketIntelligenceError("Alpha Vantage returned JSON instead of earnings CSV")
        allowed_symbols = {symbol.upper() for symbol in symbols}
        records: list[EarningsRecord] = []
        for row in csv.DictReader(io.StringIO(text)):
            symbol = str(row.get("symbol", "")).upper()
            if symbol not in allowed_symbols:
                continue
            report_date = self._parse_date(row.get("reportDate"))
            if report_date is None:
                continue
            fiscal_date = self._parse_date(row.get("fiscalDateEnding"))
            external_id = hashlib.sha256(
                f"alpha_vantage|{symbol}|{report_date.isoformat()}".encode()
            ).hexdigest()
            records.append(
                EarningsRecord(
                    external_id=external_id,
                    symbol=symbol,
                    report_date=report_date,
                    fiscal_date_ending=fiscal_date,
                    estimate=self._finite_float(row.get("estimate")),
                    currency=self._clean_text(row.get("currency"), 8) or None,
                    source="alpha_vantage",
                    payload={
                        "name": self._clean_text(row.get("name"), 200),
                    },
                )
            )
        return records

    @staticmethod
    def _news_record(
        *,
        symbol: str | None,
        title: str,
        source: str,
        published_at: datetime,
        summary: str,
        url_hash: str,
        sentiment: str,
        relevance: float | None,
        sentiment_score: float | None,
    ) -> NewsRecord:
        external_id = hashlib.sha256(
            f"alpha_vantage|{url_hash}|{symbol or 'MARKET'}".encode()
        ).hexdigest()
        return NewsRecord(
            external_id=external_id,
            symbol=symbol,
            headline=title,
            source=source,
            published_at=published_at,
            sentiment=sentiment or None,
            payload={
                "summary": summary,
                "url_sha256": url_hash,
                "relevance_score": relevance,
                "sentiment_score": sentiment_score,
            },
        )

    @staticmethod
    def _validate_response(response: httpx.Response, max_bytes: int) -> None:
        response.raise_for_status()
        if len(response.content) > max_bytes:
            raise MarketIntelligenceError("Alpha Vantage response exceeded the size limit")

    @staticmethod
    def _raise_provider_error(body: dict[str, Any]) -> None:
        for key in ("Error Message", "Note", "Information"):
            if body.get(key):
                raise MarketIntelligenceError(f"Alpha Vantage provider error: {key}")

    @staticmethod
    def _clean_text(value: object, maximum: int) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(value.replace("\x00", " ").split())[:maximum]

    @staticmethod
    def _parse_timestamp(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            return None

    @staticmethod
    def _parse_date(value: object) -> date | None:
        if not isinstance(value, str):
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _finite_float(value: object) -> float | None:
        try:
            parsed = float(str(value))
        except (TypeError, ValueError):
            return None
        if parsed != parsed or parsed in (float("inf"), float("-inf")):
            return None
        return parsed
