"""Optional password gate for public deployments.

Set APP_PASSWORD to require HTTP Basic auth on every page and API call (any
username, that password). Leave it unset for local use. The browser shows its
own login prompt and remembers it, so the frontend needs no changes.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets

from fastapi import Request
from fastapi.responses import Response

# Render (and most hosts) poll this; it must answer without credentials
PUBLIC_PATHS = {"/healthz"}


def _password_ok(header: str | None, password: str) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    _, _, supplied = decoded.partition(":")
    return secrets.compare_digest(supplied.encode(), password.encode())


async def basic_auth_middleware(request: Request, call_next):
    password = os.getenv("APP_PASSWORD")
    if not password or request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    if _password_ok(request.headers.get("authorization"), password):
        return await call_next(request)
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Trader"'},
        content="Login required",
    )
