"""HTTPS ``urllib`` wrapper using the certifi CA bundle.

Avoids ``SSL: CERTIFICATE_VERIFY_FAILED`` on many Python installs where the
stdlib default trust store is empty or misconfigured (common on macOS).
"""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from typing import Any

import certifi


def ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def urlopen(
    req: urllib.request.Request,
    *,
    timeout: float | None = None,
) -> Any:
    ctx = ssl_context()
    if timeout is None:
        return urllib.request.urlopen(req, context=ctx)
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def urllib_error_message(exc: BaseException) -> str:
    """Short, UI-safe summary; avoids dumping raw ``urlopen`` / SSL tracebacks."""
    s = str(exc).strip() if exc else "request failed"
    low = s.lower()
    if "certificate_verify_failed" in low or (
        "ssl" in low and "certificate" in low and "verify" in low
    ):
        return "HTTPS certificate verification failed."
    if "nodename nor servname" in low or "name or service not known" in low:
        return "Could not resolve host."
    if "timed out" in low or "timeout" in low:
        return "Request timed out."
    if "no route to host" in low or "network is unreachable" in low:
        return "Network unreachable."
    if len(s) > 160:
        return s[:159].rstrip() + "…"
    return s
