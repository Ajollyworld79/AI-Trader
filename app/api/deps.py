from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings
from app.infrastructure.rate_limit import RateLimiter
from app.security import extract_bearer, secret_matches


@dataclass(frozen=True)
class Principal:
    raw_key: str
    key_hash: str
    is_admin: bool = False


def get_container(request: Request) -> Any:
    return request.app.state.container


async def require_api_key(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    container = get_container(request)
    settings: Settings = container.settings
    raw_key = extract_bearer(authorization)
    if not settings.api_key_hash_list or not secret_matches(raw_key, settings.api_key_hash_list):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credential",
            headers={"WWW-Authenticate": "Bearer"},
        )
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    limiter: RateLimiter = container.rate_limiter
    client_ip = request.client.host if request.client else "unknown"
    if not await limiter.allow(f"{key_hash}:{client_ip}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": "60"},
        )
    return Principal(raw_key=raw_key, key_hash=key_hash)


async def require_admin(
    request: Request,
    principal: Annotated[Principal, Depends(require_api_key)],
    x_admin_key: Annotated[str | None, Header()] = None,
) -> Principal:
    settings: Settings = get_container(request).settings
    if (
        not x_admin_key
        or not settings.admin_key_hash_list
        or not secret_matches(x_admin_key, settings.admin_key_hash_list)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin access required")
    return Principal(raw_key=principal.raw_key, key_hash=principal.key_hash, is_admin=True)


ApiPrincipal = Annotated[Principal, Depends(require_api_key)]
AdminPrincipal = Annotated[Principal, Depends(require_admin)]
