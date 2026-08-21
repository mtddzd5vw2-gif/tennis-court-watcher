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


FUNCTION_PATH = "/functions/v1/dispatch-line-notifications"
REQUEST_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 16 * 1024
RESPONSE_FIELD_ORDER = (
    "claimed_count",
    "accepted_count",
    "retry_count",
    "permanent_failure_count",
    "cancelled_count",
    "quota_consumption",
    "quota_limit",
    "quota_exhausted",
)
RESPONSE_FIELDS = set(RESPONSE_FIELD_ORDER)


class LineNotificationDispatchError(RuntimeError):
    """Raised when the LINE delivery worker cannot be invoked safely."""


def dispatch_line_notifications(
    supabase_url: str,
    worker_secret: str,
    *,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> dict[str, int | bool]:
    try:
        base_url = matching._validated_supabase_url(supabase_url)
    except matching.NotificationRuleFetchError:
        raise LineNotificationDispatchError(
            "SUPABASE_URL must be an HTTPS project URL"
        ) from None
    if not isinstance(worker_secret, str) or len(worker_secret.strip()) < 32:
        raise LineNotificationDispatchError(
            "LINE_DELIVERY_WORKER_SECRET is required"
        )
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        raise LineNotificationDispatchError("request timeout must be positive")

    request = urllib.request.Request(
        f"{base_url}{FUNCTION_PATH}",
        data=b'{"batch_size":10}',
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    request.add_unredirected_header(
        "Authorization", f"Bearer {worker_secret.strip()}"
    )
    open_request = opener if opener is not None else matching._open_without_redirects
    response: Any = None
    try:
        response = open_request(request, timeout=timeout)
        if getattr(response, "status", 200) != 200:
            raise LineNotificationDispatchError("LINE dispatch request failed")
        body = response.read(MAX_RESPONSE_BYTES + 1)
    except LineNotificationDispatchError:
        raise
    except Exception:
        raise LineNotificationDispatchError("LINE dispatch request failed") from None
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise LineNotificationDispatchError("LINE dispatch response is invalid")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LineNotificationDispatchError(
            "LINE dispatch response is invalid"
        ) from None
    return _validate_metrics(value)


def _validate_metrics(value: Any) -> dict[str, int | bool]:
    if not isinstance(value, Mapping) or set(value) != RESPONSE_FIELDS:
        raise LineNotificationDispatchError("LINE dispatch response is invalid")
    normalized: dict[str, int | bool] = {}
    for field in RESPONSE_FIELD_ORDER:
        if field == "quota_exhausted":
            continue
        field_value = value[field]
        if (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or field_value < 0
        ):
            raise LineNotificationDispatchError("LINE dispatch response is invalid")
        normalized[field] = field_value
    if not isinstance(value["quota_exhausted"], bool):
        raise LineNotificationDispatchError("LINE dispatch response is invalid")
    normalized["quota_exhausted"] = value["quota_exhausted"]
    if (
        int(normalized["accepted_count"])
        + int(normalized["retry_count"])
        + int(normalized["permanent_failure_count"])
        + int(normalized["cancelled_count"])
        > int(normalized["claimed_count"])
        or int(normalized["quota_limit"]) < 1
        or int(normalized["quota_limit"]) > 200
        or bool(normalized["quota_exhausted"])
        != (
            int(normalized["quota_consumption"])
            >= int(normalized["quota_limit"])
        )
    ):
        raise LineNotificationDispatchError("LINE dispatch response has invalid counts")
    return normalized


def main() -> int:
    try:
        metrics = dispatch_line_notifications(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("LINE_DELIVERY_WORKER_SECRET", ""),
        )
    except LineNotificationDispatchError:
        print("LINE notification dispatch failed", file=sys.stderr)
        return 1
    print(
        " ".join(
            f"{key}={str(metrics[key]).lower() if isinstance(metrics[key], bool) else metrics[key]}"
            for key in RESPONSE_FIELD_ORDER
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
