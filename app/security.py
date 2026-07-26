from __future__ import annotations

import argparse
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, status


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def secret_matches(value: str, accepted_hashes: tuple[str, ...]) -> bool:
    candidate = hash_secret(value)
    matched = False
    for expected in accepted_hashes:
        matched = hmac.compare_digest(candidate, expected) or matched
    return matched


def extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authorization scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if len(token) > 512:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credential")
    return token.strip()


@dataclass(frozen=True)
class GeneratedKey:
    raw: str
    sha256: str


def generate_key() -> GeneratedKey:
    raw = f"ait_{secrets.token_urlsafe(32)}"
    return GeneratedKey(raw=raw, sha256=hash_secret(raw))


def _main() -> None:
    parser = argparse.ArgumentParser(description="AI-Tader security utilities")
    parser.add_argument("command", choices=("generate-key", "hash"))
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    if args.command == "generate-key":
        generated = generate_key()
        print(f"client_key={generated.raw}")
        print(f"sha256={generated.sha256}")
        return
    if not args.value:
        parser.error("hash requires a value")
    print(hash_secret(args.value))


if __name__ == "__main__":
    _main()
