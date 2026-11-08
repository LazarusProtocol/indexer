from __future__ import annotations
from typing import Callable
from urllib.parse import urlparse
from django.conf import settings
from django.http import HttpRequest, HttpResponse


def _is_localhost_origin(origin: str) -> bool:
    try:
        parsed = urlparse(origin)
        # Accessing parsed.port validates that the port segment is strictly numeric (not e.g. :5173.evil.com)
        _ = parsed.port
        return (
            parsed.scheme in ("http", "https")
            and parsed.hostname in ("localhost", "127.0.0.1")
            and parsed.username is None
            and parsed.password is None
            and parsed.path in ("", "/")
        )
    except Exception:
        return False


class LazarusCORSMiddleware:
    """
    Hardened CORS middleware allowing legitimate Lazarus web origins
    while preventing arbitrary origin reflection and subdomain/authority prefix spoofing.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == "OPTIONS":
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        origin = request.headers.get("Origin", "")
        trusted = getattr(settings, "CSRF_TRUSTED_ORIGINS", [])
        is_debug = getattr(settings, "DEBUG", False)

        allowed_origin = "*"
        if origin:
            # Check if origin matches trusted origins (or strict hostname-verified localhost in debug mode)
            is_trusted = origin in trusted or (is_debug and _is_localhost_origin(origin))
            allowed_origin = origin if is_trusted else (trusted[0] if trusted else "")

        if allowed_origin:
            response["Access-Control-Allow-Origin"] = allowed_origin
        response["Vary"] = "Origin"
        response["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, Accept, X-Requested-With, X-Client-Address"
        )
        response["Access-Control-Expose-Headers"] = "X-Cache, Retry-After"
        return response
