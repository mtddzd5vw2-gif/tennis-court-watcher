from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any, Callable, Mapping

if __package__:
    from scripts import match_notification_rules as matching
else:
    import match_notification_rules as matching


FUNCTION_PATH = "/functions/v1/dispatch-email-notifications"
REQUEST_BODY = b'{"batch_size":10}'
REQUEST_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 64 * 1024
RESPONSE_FIELDS = (
    "claimed_count",
    "accepted_count",
    "retry_count",
    "permanent_failure_count",
    "cancelled_count",
)


class EmailNotificationDispatchError(RuntimeError):
    """Raised when the email delivery worker cannot be invoked safely."""


def _validate_aggregate_response(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(RESPONSE_FIELDS):
        raise EmailNotificationDispatchError(
            "dispatch response has an invalid format"
        )
    aggregate: dict[str, int] = {}
    for field in RESPONSE_FIELDS:
        field_value = value[field]
        if (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or field_value < 0
        ):
            raise EmailNotificationDispatchError(
                "dispatch response has an invalid format"
            )
        aggregate[field] = field_value
    return aggregate


def dispatch_email_notifications(
    supabase_url: str,
    worker_secret: str,
    *,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> dict[str, int]:
    try:
        base_url = matching._validated_supabase_url(supabase_url)
    except matching.NotificationRuleFetchError:
        raise EmailNotificationDispatchError(
            "SUPABASE_URL must be an HTTPS project URL"
        ) from None
    if not isinstance(worker_secret, str) or not worker_secret.strip():
        raise EmailNotificationDispatchError(
            "EMAIL_DELIVERY_WORKER_SECRET is required"
        )
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise EmailNotificationDispatchError("request timeout must be positive")

    request = urllib.request.Request(
        f"{base_url}{FUNCTION_PATH}",
        data=REQUEST_BODY,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    request.add_unredirected_header(
        "Authorization",
        f"Bearer {worker_secret.strip()}",
    )
    open_request = opener if opener is not None else matching._open_without_redirects

    response: Any = None
    try:
        response = open_request(request, timeout=timeout)
        status = getattr(response, "status", 200)
        if status != 200:
            raise EmailNotificationDispatchError("dispatch request failed")
        response_body = response.read(MAX_RESPONSE_BYTES + 1)
    except EmailNotificationDispatchError:
        raise
    except Exception:
        raise EmailNotificationDispatchError("dispatch request failed") from None
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    if not isinstance(response_body, bytes):
        raise EmailNotificationDispatchError("dispatch response is not valid JSON")
    if len(response_body) > MAX_RESPONSE_BYTES:
        raise EmailNotificationDispatchError("dispatch response is too large")
    try:
        response_value = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EmailNotificationDispatchError(
            "dispatch response is not valid JSON"
        ) from None
    return _validate_aggregate_response(response_value)


def main() -> int:
    try:
        aggregate = dispatch_email_notifications(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("EMAIL_DELIVERY_WORKER_SECRET", ""),
        )
    except EmailNotificationDispatchError:
        print("email notification dispatch failed", file=sys.stderr)
        return 1

    print(" ".join(f"{field}={aggregate[field]}" for field in RESPONSE_FIELDS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
