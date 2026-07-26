from __future__ import annotations

import hashlib
import json
from typing import Any

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
        sector: str = "Unknown",
        uic: int | None = None,
        asset_type: str = "Stock",
        days_to_earnings: int | None = None,
    ) -> tuple[FeatureSet, AIAnalysis, str]:
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
        feature_snapshot: dict[str, Any] = features.to_safe_ai_payload()
        self.repository.save_feature(
            symbol=symbol,
            as_of=features.as_of,
            values=dict(feature_snapshot),
            input_hash=hashlib.sha256(
                json.dumps(feature_snapshot, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )
        feature_snapshot["_execution"] = {
            "sector": sector,
            "uic": uic,
            "asset_type": asset_type,
            "days_to_earnings": days_to_earnings,
        }
        signal_id = self.repository.save_signal(
            symbol=symbol,
            decision=analysis.decision.value,
            confidence=analysis.confidence,
            reason=analysis.reason,
            risk_flags=list(analysis.risk_flags),
            feature_snapshot=feature_snapshot,
            model_tier=analysis.model_tier,
        )
        return features, analysis, signal_id
