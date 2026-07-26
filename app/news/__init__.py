"""Bounded, read-only market-intelligence providers."""

from app.news.alpha_vantage import AlphaVantageClient, EarningsRecord, NewsRecord

__all__ = ["AlphaVantageClient", "EarningsRecord", "NewsRecord"]
