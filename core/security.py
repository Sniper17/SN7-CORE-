"""
SN7 request security/audit logging.

This module only observes requests and writes sanitized audit lines to stdout.
It does not participate in OAuth, authentication, authorization, or routing.
"""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import request, session


_SUSPICIOUS_PATHS = (
    re.compile(r"(^|/)\.env(?:$|[./])", re.I),
    re.compile(r"(^|/)phpinfo(?:\.php)?$", re.I),
    re.compile(r"(^|/)(?:wp-admin|wp-login|administrator)(?:/|$)", re.I),
    re.compile(r"(^|/)(?:server-status|server-info)(?:/|$)", re.I),
    re.compile(r"\.(?:bak|old|sql|zip|tar|gz|log)$", re.I),
    re.compile(r"(^|/)(?:service-account|credentials|gcp-(?:key|credentials)|firebase-(?:key|adminsdk))\.json$", re.I),
)

_AUTH_PATHS = (
    "/kick/login",
    "/kick/callback",
    "/twitch/login",
    "/twitch/callback",
    "/youtube/login",
    "/youtube/callback",
    "/api/session/logout",
)

_PROTECTED_PREFIXES = (
    "/api/commands/",
    "/api/settings/",
    "/api/ranking/",
    "/api/economy/",
    "/api/duel/",
    "/api/music/",
    "/api/minigames/",
    "/api/automations/",
    "/api/obs/",
)


def _clean(value, limit=220):
    # Prevent control characters/newlines from becoming log-injection payloads.
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()[:limit]


def _header(name):
    return _clean(request.headers.get(name))


def _safe_referer():
    raw = _header("Referer")
    if not raw:
        return "-"
    try:
        parsed = urlsplit(raw)
        # Never copy OAuth codes/tokens from a Referer into application logs.
        return _clean(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")
    except ValueError:
        return "-"


def client_ip():
    """
    Resolve the client IP for audit purposes only.

    Render documents X-Forwarded-For as the client-IP header and also forwards
    Cloudflare's CF-Ray/CF-Connecting-IP information. This value is NEVER
    used for authentication, authorization, or rate limiting.
    """
    cf_ip = _header("CF-Connecting-IP")
    xff = _header("X-Forwarded-For")
    if cf_ip:
        return cf_ip
    if xff:
        return xff.split(",")[0].strip()
    return str(request.remote_addr or "unknown")


def _query_keys():
    # Never write OAuth codes, state values, tokens, cookies, or arbitrary
    # query-string values to the audit log.
    return ",".join(sorted(set(request.args.keys()))) or "-"


def _classify(path, status=None):
    for pattern in _SUSPICIOUS_PATHS:
        if pattern.search(path):
            return "SUSPICIOUS_PATH"
    if path in _AUTH_PATHS:
        return "OAUTH_OR_SESSION"
    if any(path.startswith(prefix) for prefix in _PROTECTED_PREFIXES):
        if status is not None and status in (401, 403):
            return "AUTH_DENIED"
        return "PROTECTED_API"
    if path in {"/", "/dashboard", "/perfil", "/privacy", "/terms"}:
        return "PUBLIC_PAGE"
    return "REQUEST"


def audit_request_start():
    """Log a sanitized request start without touching request behavior."""
    if request.path.startswith("/static/"):
        return

    session_id = session.get("sn7_broadcaster_id", session.get("kick_broadcaster_id"))
    try:
        session_id = str(int(session_id)) if session_id is not None else "-"
    except (TypeError, ValueError):
        session_id = "-"

    print(
        "[SN7-SECURITY] "
        f"event=request_start "
        f"time={datetime.now(timezone.utc).isoformat()} "
        f"ip={client_ip()} "
        f"xff={_header('X-Forwarded-For') or '-'} "
        f"cf_connecting_ip={_header('CF-Connecting-IP') or '-'} "
        f"cf_ray={_header('CF-Ray') or '-'} "
        f"rndr_id={_header('Rndr-Id') or '-'} "
        f"method={request.method} "
        f"path={request.path} "
        f"query_keys={_query_keys()} "
        f"session={session_id} "
        f"ua={_clean(_header('User-Agent')) or '-'} "
        f"referer={_safe_referer()} "
        f"class={_classify(request.path)}",
        flush=True,
    )


def audit_request_end(response):
    """Log the sanitized result and highlight suspicious/denied requests."""
    if request.path.startswith("/static/"):
        return response

    status = int(response.status_code)
    session_id = session.get("sn7_broadcaster_id", session.get("kick_broadcaster_id"))
    try:
        session_id = str(int(session_id)) if session_id is not None else "-"
    except (TypeError, ValueError):
        session_id = "-"

    classification = _classify(request.path, status)
    if status >= 400 or classification in {"SUSPICIOUS_PATH", "AUTH_DENIED", "OAUTH_OR_SESSION"}:
        level = "warning" if status >= 400 or classification in {"SUSPICIOUS_PATH", "AUTH_DENIED"} else "info"
        print(
            "[SN7-SECURITY] "
            f"event=request_end "
            f"level={level} "
            f"time={datetime.now(timezone.utc).isoformat()} "
            f"ip={client_ip()} "
            f"cf_ray={_header('CF-Ray') or '-'} "
            f"rndr_id={_header('Rndr-Id') or '-'} "
            f"method={request.method} "
            f"path={request.path} "
            f"status={status} "
            f"session={session_id} "
            f"class={classification}",
            flush=True,
        )
    return response
