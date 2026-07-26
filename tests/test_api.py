from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.conftest import API_KEY, candle_payload


def test_health_is_public_but_api_is_authenticated(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/v1/system/status").status_code == 401
        assert (
            client.get(
                "/v1/system/status",
                headers={"Authorization": "Bearer incorrect"},
            ).status_code
            == 401
        )


def test_status_and_dashboard_do_not_expose_secrets(
    settings: Settings, auth_headers: dict[str, str]
) -> None:
    with TestClient(create_app(settings)) as client:
        status_response = client.get("/v1/system/status", headers=auth_headers)
        assert status_response.status_code == 200
        payload = status_response.json()
        assert payload["trading_kill_switch"] is True
        assert "api_key" not in status_response.text.lower()
        dashboard = client.get("/v1/dashboard", headers=auth_headers)
        assert dashboard.status_code == 200
        assert dashboard.json()["ai_budget_dkk"] == 1000


def test_analysis_uses_deterministic_scanner_without_ai(
    settings: Settings, auth_headers: dict[str, str]
) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/analysis",
            headers=auth_headers,
            json={"symbol": "MSFT", "candles": candle_payload()},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["model_tier"] in {"none"}
        assert "features" in body
        assert "quantity" not in body
        assert "stop_loss" not in body


def test_order_endpoint_requires_admin_and_respects_kill_switch(
    settings: Settings,
    auth_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    payload = {
        "proposal": {
            "symbol": "MSFT",
            "sector": "Technology",
            "entry_price": 100,
            "stop_loss": 95,
            "take_profit": 110,
            "spread_pct": 0.001,
            "days_to_earnings": 20,
            "data_quality_ok": True,
            "uic": 1234,
        },
        "portfolio": {
            "equity": 100000,
            "cash": 80000,
            "peak_equity": 100000,
            "daily_pnl": 0,
            "open_positions": 0,
            "gross_exposure": 0,
            "sector_exposure": {},
        },
    }
    headers = {**auth_headers, "Idempotency-Key": "order-key-123456789"}
    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/orders", headers=headers, json=payload).status_code == 403
        admin = {**admin_headers, "Idempotency-Key": "order-key-123456789"}
        response = client.post("/v1/orders", headers=admin, json=payload)
        assert response.status_code == 403
        assert response.json()["detail"] == "trading kill switch is active"


def test_paper_order_flow_is_idempotent_and_risk_checked(
    settings: Settings, admin_headers: dict[str, str]
) -> None:
    enabled = settings.model_copy(update={"trading_kill_switch": False})
    payload = {
        "proposal": {
            "symbol": "MSFT",
            "sector": "Technology",
            "entry_price": 100,
            "stop_loss": 95,
            "take_profit": 110,
            "spread_pct": 0.001,
            "days_to_earnings": 20,
            "data_quality_ok": True,
            "uic": 1234,
        },
        "portfolio": {
            "equity": 100000,
            "cash": 80000,
            "peak_equity": 100000,
            "daily_pnl": 0,
            "open_positions": 0,
            "gross_exposure": 0,
            "sector_exposure": {},
        },
    }
    headers = {**admin_headers, "Idempotency-Key": "order-key-123456789"}
    with TestClient(create_app(enabled)) as client:
        response = client.post("/v1/orders", headers=headers, json=payload)
        assert response.status_code == 201
        assert len(response.json()["related_order_ids"]) == 2
        duplicate = client.post("/v1/orders", headers=headers, json=payload)
        assert duplicate.status_code == 409


def test_request_validation_rejects_unknown_fields_and_oversized_symbol(
    settings: Settings, auth_headers: dict[str, str]
) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/risk/evaluate",
            headers=auth_headers,
            json={"unexpected": "field"},
        )
        assert response.status_code == 422


def test_risk_broker_and_backtest_endpoints(
    settings: Settings,
    auth_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    risk_payload = {
        "proposal": {
            "symbol": "MSFT",
            "sector": "Technology",
            "entry_price": 100,
            "stop_loss": 95,
            "take_profit": 110,
            "spread_pct": 0.001,
            "days_to_earnings": 20,
            "data_quality_ok": True,
            "uic": 1234,
        },
        "portfolio": {
            "equity": 100000,
            "cash": 80000,
            "peak_equity": 100000,
            "daily_pnl": 0,
            "open_positions": 0,
            "gross_exposure": 0,
            "sector_exposure": {},
        },
    }
    with TestClient(create_app(settings)) as client:
        risk = client.post("/v1/risk/evaluate", headers=auth_headers, json=risk_payload)
        assert risk.status_code == 200
        assert risk.json()["approved"] is True
        assert client.get("/v1/broker/account", headers=admin_headers).status_code == 200
        portfolio = client.get("/v1/broker/portfolio", headers=admin_headers)
        assert portfolio.status_code == 200
        assert client.get("/v1/broker/auth/start", headers=admin_headers).status_code == 409
        backtest = client.post(
            "/v1/backtests",
            headers=admin_headers,
            json={
                "name": "deterministic-test",
                "symbol": "MSFT",
                "candles": candle_payload(180),
            },
        )
        assert backtest.status_code == 200
        assert len(backtest.json()["id"]) == 36


def test_rate_limit_and_request_size_are_enforced(settings: Settings) -> None:
    limited = settings.model_copy(update={"rate_limit_per_minute": 1, "max_request_bytes": 1024})
    headers = {"Authorization": f"Bearer {API_KEY}"}
    with TestClient(create_app(limited)) as client:
        assert client.get("/v1/system/status", headers=headers).status_code == 200
        assert client.get("/v1/system/status", headers=headers).status_code == 429
        oversized = client.post(
            "/v1/analysis",
            headers={**headers, "Content-Length": "2048"},
            content=b"{}",
        )
        assert oversized.status_code == 413
