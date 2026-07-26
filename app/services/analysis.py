from __future__ import annotations

from app.ai.client import RoutedMarketAnalyzer
from app.domain.entities import AIAnalysis, Candle, Decision, FeatureSet
from app.domain.indicators import compute_features, is_interesting
from app.infrastructure.repositories import Repository


class AnalysisService:
    def __init__(self, analyzer: RoutedMarketAnalyzer, repository: Repository) -> None:
        self.analyzer = analyzer
        self.repository = repository

    async def analyze(
        self,
        symbol: str,
        candles: list[Candle],
        benchmark_closes: list[float] | None,
        news: list[str] | None,
    ) -> tuple[FeatureSet, AIAnalysis]:
        features = compute_features(candles, benchmark_closes)
        if not is_interesting(features, candles[-1].close):
            analysis = AIAnalysis(
                decision=Decision.REJECT,
                confidence=1.0,
                reason="Deterministic scanner rejected the setup before AI review",
                risk_flags=("scanner_rejected",),
                model_tier="none",
                provider="none",
            )
        else:
            analysis = await self.analyzer.analyze(
                symbol=symbol,
                features=features,
                news=news,
            )
        self.repository.save_signal(
            symbol=symbol,
            decision=analysis.decision.value,
            confidence=analysis.confidence,
            reason=analysis.reason,
            risk_flags=list(analysis.risk_flags),
            feature_snapshot=features.to_safe_ai_payload(),
            model_tier=analysis.model_tier,
        )
        return features, analysis
