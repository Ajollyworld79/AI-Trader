from __future__ import annotations

from typing import Protocol

from app.domain.entities import BrokerOrder, BrokerOrderResult


class BrokerError(RuntimeError):
    pass


class BrokerAuthenticationError(BrokerError):
    pass


class BrokerValidationError(BrokerError):
    pass


class BrokerUnavailableError(BrokerError):
    pass


class Broker(Protocol):
    async def health(self) -> bool: ...

    async def account_info(self) -> dict[str, object]: ...

    async def portfolio(self) -> dict[str, object]: ...

    async def positions(self) -> dict[str, object]: ...

    async def orders(self) -> dict[str, object]: ...

    async def balance(self) -> dict[str, object]: ...

    async def precheck(self, order: BrokerOrder) -> dict[str, object]: ...

    async def place_bracket_order(self, order: BrokerOrder) -> BrokerOrderResult: ...
