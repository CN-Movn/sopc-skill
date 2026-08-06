"""Stable OpenCode Go request metadata and error classification."""

from __future__ import annotations

from dataclasses import dataclass

UPSTREAM_BASE = "https://opencode.ai/zen/go/v1"
UPSTREAM_MODELS_URL = UPSTREAM_BASE + "/models"
UPSTREAM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://opencode.ai",
    "Referer": "https://opencode.ai/",
}


@dataclass(frozen=True)
class UpstreamFailure:
    code: str
    http_status: int | None
    client_status: int
    message: str


def classify_upstream_failure(status: int | None, body: str = "") -> UpstreamFailure:
    """Return a stable, non-secret classification for an upstream failure."""

    value = int(status or 0)
    lowered = body.lower()
    waf_markers = (
        "error code: 1010",
        "cloudflare ray id",
        "cf-ray",
        "attention required",
        "access denied | cloudflare",
        "the owner of this website has banned your access",
    )
    auth_markers = (
        "invalid api key",
        "invalid_api_key",
        "invalid token",
        "invalid_token",
        "authentication failed",
        "authentication_error",
        "unauthorized",
        "token is invalid",
        "api key is invalid",
    )
    if value == 403 and any(marker in lowered for marker in waf_markers):
        return UpstreamFailure(
            "upstream_waf_blocked",
            value,
            502,
            "OpenCode Go access was blocked by Cloudflare or an upstream WAF.",
        )
    if value == 401 or (value == 403 and any(marker in lowered for marker in auth_markers)):
        return UpstreamFailure(
            "upstream_key_invalid",
            value,
            401,
            "OpenCode Go rejected the configured upstream key.",
        )
    if value == 403:
        return UpstreamFailure(
            "upstream_forbidden",
            value,
            502,
            "OpenCode Go returned a non-authentication forbidden response.",
        )
    if value == 429:
        return UpstreamFailure(
            "upstream_rate_limited",
            value,
            429,
            "OpenCode Go rate limited the request.",
        )
    if value >= 500:
        return UpstreamFailure(
            "upstream_service_unavailable",
            value,
            502,
            "OpenCode Go is temporarily unavailable.",
        )
    if value <= 0:
        return UpstreamFailure(
            "upstream_network_error",
            None,
            502,
            "The OpenCode Go network request failed.",
        )
    return UpstreamFailure(
        "upstream_request_failed",
        value,
        502,
        "OpenCode Go rejected the request.",
    )
