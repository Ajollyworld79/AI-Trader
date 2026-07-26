from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.domain.entities import Candle
from app.domain.market import classify_market_regime
from app.infrastructure.database import Base, Database
from app.infrastructure.repositories import Repository
from app.news.alpha_vantage import AlphaVantageClient, MarketIntelligenceError
from tests.conftest import candle_series


@pytest.mark.anyio
async def test_alpha_vantage_fetches_bounded_news_and_earnings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        function = request.url.params["function"]
        if function == "NEWS_SENTIMENT":
            return httpx.Response(
                200,
                json={
                    "feed": [
                        {
                            "title": "Microsoft raises guidance",
                            "source": "Example Wire",
                            "url": "https://example.invalid/article",
                            "time_published": "20260726T120000",
                            "summary": "Demand remains strong.",
                            "ticker_sentiment": [
                                {
                                    "ticker": "MSFT",
                                    "relevance_score": "0.91",
                                    "ticker_sentiment_score": "0.42",
                                    "ticker_sentiment_label": "Bullish",
                                },
                                {
                                    "ticker": "OTHER",
                                    "relevance_score": "0.8",
                                    "ticker_sentiment_score": "0.2",
                                    "ticker_sentiment_label": "Bullish",
                                },
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            text=(
                "symbol,name,reportDate,fiscalDateEnding,estimate,currency\n"
                "MSFT,Microsoft,2026-08-01,2026-06-30,3.10,USD\n"
                "OTHER,Other,2026-08-02,2026-06-30,1.00,USD\n"
            ),
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = AlphaVantageClient("secret-key", http)
    news = await provider.fetch_news(symbols={"MSFT"}, lookback_hours=24, limit=20)
    earnings = await provider.fetch_earnings(symbols={"MSFT"})
    assert len(news) == 1
    assert news[0].symbol == "MSFT"
    assert news[0].payload["url_sha256"] != "https://example.invalid/article"
    assert len(earnings) == 1
    assert earnings[0].report_date == date(2026, 8, 1)
    await http.aclose()


@pytest.mark.anyio
async def test_alpha_vantage_rate_limit_payload_fails_closed() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"Note": "rate limit"})
        )
    )
    provider = AlphaVantageClient("secret-key", http)
    with pytest.raises(MarketIntelligenceError):
        await provider.fetch_news(symbols={"MSFT"}, lookback_hours=24, limit=20)
    await http.aclose()


@pytest.mark.anyio
async def test_alpha_vantage_keeps_market_wide_macro_news() -> None:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "feed": [
                        {
                            "title": "Central bank changes rates",
                            "source": "Example Wire",
                            "url": "https://example.invalid/macro",
                            "time_published": "20260726T120000",
                            "summary": "A macro event affects the broad market.",
                            "overall_sentiment_label": "Neutral",
                            "overall_sentiment_score": "0.0",
                            "topics": [{"topic": "Economy - Monetary"}],
                            "ticker_sentiment": [],
                        }
                    ]
                },
            )
        )
    )
    provider = AlphaVantageClient("secret-key", http)
    news = await provider.fetch_news(symbols={"MSFT"}, lookback_hours=24, limit=20)
    assert len(news) == 1
    assert news[0].symbol is None
    assert news[0].sentiment == "Neutral"
    await http.aclose()


def test_market_regime_is_deterministic() -> None:
    candles = candle_series(120)
    bullish = classify_market_regime(candles, extreme_volatility=5)
    assert bullish.regime == "bullish_trending"
    assert bullish.to_payload()["data_quality_ok"] is True
    volatile = classify_market_regime(candles, extreme_volatility=0.000001)
    assert volatile.regime == "extreme_volatility"
    descending = [
        Candle(
            timestamp=item.timestamp,
            open=200 - index * 0.5,
            high=200.5 - index * 0.5,
            low=199.0 - index * 0.5,
            close=199.5 - index * 0.5,
            volume=item.volume,
        )
        for index, item in enumerate(candles)
    ]
    bearish = classify_market_regime(descending, extreme_volatility=5)
    assert bearish.regime == "bearish_trending"


def test_market_benchmark_configuration_is_strict() -> None:
    configured = Settings(market_benchmark_json='{"symbol":"spy","uic":1234,"asset_type":"Stock"}')
    assert configured.market_benchmark == {
        "symbol": "SPY",
        "uic": 1234,
        "asset_type": "Stock",
    }
    assert Settings().market_benchmark is None
    with pytest.raises(ValueError):
        _ = Settings(market_benchmark_json="not-json").market_benchmark
    with pytest.raises(ValueError):
        _ = Settings(market_benchmark_json="[]").market_benchmark
    with pytest.raises(ValueError):
        _ = Settings(market_benchmark_json='{"symbol":"SPY","uic":true}').market_benchmark
    with pytest.raises(ValidationError):
        Settings(
            trading_mode="live",
            broker_backend="saxo",
            live_trading_enabled=True,
            trading_kill_switch=False,
        )


def test_repository_deduplicates_and_reads_market_intelligence(settings: Settings) -> None:
    database = Database(settings)
    Base.metadata.create_all(database.engine)
    repository = Repository(database)
    now = datetime.now(UTC)
    values = {
        "external_id": "news-unique",
        "symbol": "MSFT",
        "headline": "Headline",
        "source": "test",
        "published_at": now,
        "sentiment": "Bullish",
        "payload": {"summary": "Summary"},
    }
    assert repository.save_news(**values)
    assert repository.save_news(**values) == ""
    assert repository.recent_news_context("MSFT", max_age_hours=24)

    earnings = {
        "external_id": "earnings-unique",
        "symbol": "MSFT",
        "report_date": datetime.now(UTC).date() + timedelta(days=10),
        "fiscal_date_ending": None,
        "estimate": 3.1,
        "currency": "USD",
        "source": "alpha_vantage",
        "payload": {},
    }
    assert repository.save_earnings(**earnings)
    assert repository.save_earnings(**earnings) == ""
    assert repository.days_to_next_earnings("MSFT") == 10

    regime = {
        "benchmark_symbol": "SPY",
        "as_of": now,
        "regime": "bullish_trending",
        "close": 500,
        "volatility": 0.2,
        "adx": 30,
        "data_quality_ok": True,
        "payload": {},
    }
    assert repository.save_market_regime(**regime)
    assert repository.save_market_regime(**regime) == ""
    summary = repository.market_intelligence_summary()
    assert summary["news_items"] == 1
    assert summary["earnings_events"] == 1
    market_regime = summary["market_regime"]
    assert isinstance(market_regime, dict)
    assert market_regime["regime"] == "bullish_trending"
    database.engine.dispose()
