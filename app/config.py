from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal, cast
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"
    app_role: Literal["api", "market", "strategy", "execution", "position", "news", "scheduler"] = (
        "api"
    )
    log_level: str = "INFO"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    database_url: str = "sqlite:///./data/ai_tader.db"
    redis_url: str | None = None

    api_key_hashes: str = ""
    admin_key_hashes: str = ""
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    allowed_origins: str = ""
    max_request_bytes: int = Field(default=1_048_576, ge=1_024, le=10_485_760)
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)

    trading_mode: Literal["sim", "live"] = "sim"
    broker_backend: Literal["paper", "saxo"] = "paper"
    live_trading_enabled: bool = False
    trading_kill_switch: bool = True
    live_confirmation_token_hash: SecretStr | None = None
    worker_live_confirmation_token: SecretStr | None = None
    token_encryption_key: SecretStr | None = None

    max_risk_per_trade: float = Field(default=0.005, gt=0, le=0.02)
    max_open_positions: int = Field(default=10, ge=1, le=100)
    max_sector_exposure: float = Field(default=0.20, gt=0, le=1)
    max_daily_loss: float = Field(default=0.02, gt=0, le=0.10)
    max_drawdown: float = Field(default=0.10, gt=0, le=0.50)
    max_spread_pct: float = Field(default=0.005, gt=0, le=0.05)
    earnings_blackout_days: int = Field(default=2, ge=0, le=30)
    atr_stop_multiple: float = Field(default=2.0, gt=0, le=10)
    reward_risk_ratio: float = Field(default=2.0, ge=1, le=10)
    signal_max_age_minutes: int = Field(default=90, ge=1, le=1_440)
    max_market_data_age_seconds: int = Field(default=120, ge=5, le=3_600)
    trailing_activation_pct: float = Field(default=0.08, gt=0, le=1)
    trailing_distance_atr: float = Field(default=2.0, gt=0, le=10)
    trailing_step_atr: float = Field(default=0.5, gt=0, le=5)
    trading_universe_json: str = "[]"
    auto_execute_signals: bool = False
    market_horizon_minutes: int = Field(default=60, ge=1, le=10_080)
    market_candle_count: int = Field(default=120, ge=62, le=1_200)

    azure_openai_api_key: SecretStr | None = None
    azure_openai_endpoint: str | None = None
    openai_api_version: str = "2025-04-01-preview"
    azure_openai_mini_deployment: str = "gpt-5.4-mini"
    openai_api_key: SecretStr | None = None
    openai_mini_model: str = "gpt-5.4-mini"
    openai_sol_model: str = "gpt-5.4"
    ai_monthly_budget_dkk: float = Field(default=1000.0, gt=0)
    ai_sol_max_ratio: float = Field(default=0.05, ge=0, le=0.05)
    ai_azure_mini_input_dkk_per_m: float = Field(default=0.0, ge=0)
    ai_azure_mini_output_dkk_per_m: float = Field(default=0.0, ge=0)
    ai_openai_mini_input_dkk_per_m: float = Field(default=0.0, ge=0)
    ai_openai_mini_output_dkk_per_m: float = Field(default=0.0, ge=0)
    ai_openai_sol_input_dkk_per_m: float = Field(default=0.0, ge=0)
    ai_openai_sol_output_dkk_per_m: float = Field(default=0.0, ge=0)

    saxo_app_key: SecretStr | None = None
    saxo_app_secret: SecretStr | None = None
    saxo_redirect_uri: str | None = None
    saxo_account_key: SecretStr | None = None
    saxo_access_token: SecretStr | None = None
    saxo_refresh_token: SecretStr | None = None
    saxo_sim_auth_base_url: str = "https://sim.logonvalidation.net"
    saxo_live_auth_base_url: str = "https://live.logonvalidation.net"
    saxo_sim_api_base_url: str = "https://gateway.saxobank.com/sim/openapi"
    saxo_live_api_base_url: str = "https://gateway.saxobank.com/openapi"

    @field_validator("allowed_hosts", "allowed_origins")
    @classmethod
    def reject_wildcard_security_lists(cls, value: str) -> str:
        if "*" in value.split(","):
            raise ValueError("wildcards are forbidden in host and origin allowlists")
        return value

    @field_validator(
        "saxo_sim_auth_base_url",
        "saxo_live_auth_base_url",
        "saxo_sim_api_base_url",
        "saxo_live_api_base_url",
    )
    @classmethod
    def validate_saxo_url(cls, value: str) -> str:
        parsed = urlparse(value)
        allowed = {
            "sim.logonvalidation.net",
            "live.logonvalidation.net",
            "gateway.saxobank.com",
        }
        if parsed.scheme != "https" or parsed.hostname not in allowed:
            raise ValueError("Saxo endpoints must use HTTPS and an approved Saxo hostname")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Saxo endpoint contains forbidden URL components")
        return value.rstrip("/")

    @field_validator("azure_openai_endpoint")
    @classmethod
    def validate_azure_endpoint(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".openai.azure.com")
        ):
            raise ValueError("AZURE_OPENAI_ENDPOINT must be an HTTPS Azure OpenAI endpoint")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Azure endpoint contains forbidden URL components")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_production_safety(self) -> Settings:
        if self.trading_mode == "live":
            if self.broker_backend != "saxo":
                raise ValueError("LIVE mode requires BROKER_BACKEND=saxo")
            if not self.live_trading_enabled:
                raise ValueError("LIVE mode requires LIVE_TRADING_ENABLED=true")
            if self.trading_kill_switch:
                raise ValueError("LIVE mode cannot start while TRADING_KILL_SWITCH=true")
            if not self.live_confirmation_token_hash:
                raise ValueError("LIVE mode requires LIVE_CONFIRMATION_TOKEN_HASH")
            if self.auto_execute_signals and not self.worker_live_confirmation_token:
                raise ValueError("LIVE automatic execution requires WORKER_LIVE_CONFIRMATION_TOKEN")
            if not all(
                (
                    self.saxo_app_key,
                    self.saxo_app_secret,
                    self.saxo_account_key,
                )
            ):
                raise ValueError("LIVE mode requires Saxo application and account credentials")
            if not self.token_encryption_key:
                raise ValueError("LIVE mode requires TOKEN_ENCRYPTION_KEY")
        if self.environment == "production":
            if not self.api_key_hash_list or not self.admin_key_hash_list:
                raise ValueError("production requires both API_KEY_HASHES and ADMIN_KEY_HASHES")
            if self.database_url.startswith("sqlite"):
                raise ValueError("production requires PostgreSQL")
            if not self.redis_url:
                raise ValueError("production requires Redis for distributed rate limiting")
        azure_configured = bool(self.azure_openai_api_key) and bool(self.azure_openai_endpoint)
        if bool(self.azure_openai_api_key) != bool(self.azure_openai_endpoint):
            raise ValueError(
                "Azure OpenAI key and endpoint must either both be set or both be absent"
            )
        if (azure_configured or self.openai_api_key) and not (
            azure_configured and self.openai_api_key
        ):
            raise ValueError(
                "AI requires Azure for Mini primary and OpenAI for Mini fallback and SOL"
            )
        if azure_configured and not all(
            (
                self.ai_azure_mini_input_dkk_per_m,
                self.ai_azure_mini_output_dkk_per_m,
                self.ai_openai_mini_input_dkk_per_m,
                self.ai_openai_mini_output_dkk_per_m,
                self.ai_openai_sol_input_dkk_per_m,
                self.ai_openai_sol_output_dkk_per_m,
            )
        ):
            raise ValueError("AI pricing must be configured when Azure OpenAI is enabled")
        return self

    @property
    def api_key_hash_list(self) -> tuple[str, ...]:
        return self._split_hashes(self.api_key_hashes)

    @property
    def admin_key_hash_list(self) -> tuple[str, ...]:
        return self._split_hashes(self.admin_key_hashes)

    @property
    def trading_universe(self) -> tuple[dict[str, Any], ...]:
        try:
            raw = json.loads(self.trading_universe_json)
        except json.JSONDecodeError as exc:
            raise ValueError("TRADING_UNIVERSE_JSON must be valid JSON") from exc
        if not isinstance(raw, list) or len(raw) > 500:
            raise ValueError("TRADING_UNIVERSE_JSON must contain at most 500 instruments")
        result: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("each universe entry must be an object")
            entry = cast(dict[str, Any], item)
            symbol = entry.get("symbol")
            uic = entry.get("uic")
            sector = entry.get("sector")
            asset_type = entry.get("asset_type", "Stock")
            if (
                not isinstance(symbol, str)
                or not symbol
                or len(symbol) > 32
                or not isinstance(uic, int)
                or isinstance(uic, bool)
                or uic <= 0
                or not isinstance(sector, str)
                or not sector
                or not isinstance(asset_type, str)
            ):
                raise ValueError("invalid trading universe entry")
            result.append(
                {
                    "symbol": symbol.upper(),
                    "uic": uic,
                    "sector": sector,
                    "asset_type": asset_type,
                }
            )
        return tuple(result)

    @property
    def allowed_host_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]

    @property
    def allowed_origin_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def normalized_database_url(self) -> str:
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self.database_url

    @staticmethod
    def _split_hashes(value: str) -> tuple[str, ...]:
        items = tuple(item.strip().lower() for item in value.split(",") if item.strip())
        for item in items:
            if len(item) != 64 or any(ch not in "0123456789abcdef" for ch in item):
                raise ValueError("API key hashes must be lowercase SHA-256 hex digests")
        return items


@lru_cache
def get_settings() -> Settings:
    return Settings()
