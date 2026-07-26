from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import Settings
from app.security import extract_bearer, generate_key, hash_secret, secret_matches


def test_generated_key_and_constant_time_verification() -> None:
    generated = generate_key()
    assert generated.raw.startswith("ait_")
    assert len(generated.sha256) == 64
    assert secret_matches(generated.raw, (generated.sha256,))
    assert not secret_matches("wrong", (generated.sha256,))
    assert hash_secret(generated.raw) == generated.sha256


def test_extract_bearer_rejects_malformed_values() -> None:
    with pytest.raises(HTTPException):
        extract_bearer(None)
    with pytest.raises(HTTPException):
        extract_bearer("Basic abc")
    with pytest.raises(HTTPException):
        extract_bearer("Bearer " + "x" * 513)
    assert extract_bearer("Bearer valid") == "valid"


def test_production_requires_auth_database_and_redis() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production")


def test_live_mode_is_fail_closed() -> None:
    with pytest.raises(ValidationError):
        Settings(trading_mode="live")
    with pytest.raises(ValidationError):
        Settings(
            trading_mode="live",
            broker_backend="saxo",
            live_trading_enabled=True,
            trading_kill_switch=False,
        )


def test_external_endpoint_allowlists_reject_ssrf() -> None:
    with pytest.raises(ValidationError):
        Settings(saxo_sim_api_base_url="https://evil.example/api")
    with pytest.raises(ValidationError):
        Settings(azure_openai_endpoint="http://example.openai.azure.com")
    with pytest.raises(ValidationError):
        Settings(allowed_hosts="*")
