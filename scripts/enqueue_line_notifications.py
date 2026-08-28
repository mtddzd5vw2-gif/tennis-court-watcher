from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__:
    from scripts import enqueue_email_notifications as email_enqueue
    from scripts import match_notification_rules as matching
else:
    import enqueue_email_notifications as email_enqueue
    import match_notification_rules as matching


RPC_PATH = "/rest/v1/rpc/enqueue_line_notification_candidates"
RPC_TIMEOUT_SECONDS = 20
MAX_RPC_RESPONSE_BYTES = 64 * 1024
MAX_CANDIDATES_PER_REQUEST = 500
RPC_COUNT_FIELDS = {
    "candidate_count",
    "eligible_candidate_count",
    "inserted_delivery_item_count",
    "inserted_message_count",
    "linked_item_count",
}
RPC_RESPONSE_FIELDS = RPC_COUNT_FIELDS | {"shadow_mode"}


class LineNotificationEnqueueError(RuntimeError):
    """Raised when LINE candidates cannot be enqueued safely."""


def build_enqueue_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    try:
        value = email_enqueue.build_enqueue_candidate(candidate)
    except email_enqueue.EmailNotificationEnqueueError:
        raise LineNotificationEnqueueError("notification candidate is invalid") from None
    value["channel"] = "line"
    return value


def build_enqueue_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(
        candidates,
        key=lambda candidate: (
            str(candidate.get("user_id", "")),
            str(candidate.get("date", "")),
            str(candidate.get("facility_id", "")),
            str(candidate.get("start_time", "")),
            str(candidate.get("end_time", "")),
            str(candidate.get("court_name", "")),
            str(candidate.get("slot_id", "")),
        ),
    )
    return [build_enqueue_candidate(candidate) for candidate in ordered]


def enqueue_candidate_batch(
    supabase_url: str,
    service_role_key: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    shadow_mode: bool,
    canary_user_id: str | None,
    use_allowlist: bool,
    allow_all: bool,
    timeout: int = RPC_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> dict[str, int | bool]:
    if len(candidates) < 1 or len(candidates) > MAX_CANDIDATES_PER_REQUEST:
        raise LineNotificationEnqueueError(
            "enqueue batch must contain between 1 and 500 candidates"
        )
    try:
        base_url = matching._validated_supabase_url(supabase_url)
    except matching.NotificationRuleFetchError:
        raise LineNotificationEnqueueError(
            "SUPABASE_URL must be an HTTPS project URL"
        ) from None
    if not isinstance(service_role_key, str) or not service_role_key.strip():
        raise LineNotificationEnqueueError("SUPABASE_SERVICE_ROLE_KEY is required")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        raise LineNotificationEnqueueError("RPC timeout must be positive")
    _validate_rollout_controls(
        shadow_mode, canary_user_id, use_allowlist, allow_all
    )

    body = json.dumps(
        {
            "p_candidates": list(candidates),
            "p_shadow_mode": shadow_mode,
            "p_canary_user_id": canary_user_id,
            "p_use_allowlist": use_allowlist,
            "p_allow_all": allow_all,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{RPC_PATH}",
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    key = service_role_key.strip()
    request.add_unredirected_header("Authorization", f"Bearer {key}")
    request.add_unredirected_header("apikey", key)
    open_request = opener if opener is not None else matching._open_without_redirects

    response: Any = None
    try:
        response = open_request(request, timeout=timeout)
        status = getattr(response, "status", 200)
        if not isinstance(status, int) or status < 200 or status >= 300:
            raise LineNotificationEnqueueError("enqueue request failed")
        response_body = response.read(MAX_RPC_RESPONSE_BYTES + 1)
    except LineNotificationEnqueueError:
        raise
    except Exception:
        raise LineNotificationEnqueueError("enqueue request failed") from None
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    if not isinstance(response_body, bytes):
        raise LineNotificationEnqueueError("enqueue response is not valid JSON")
    if len(response_body) > MAX_RPC_RESPONSE_BYTES:
        raise LineNotificationEnqueueError("enqueue response is too large")
    try:
        value = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LineNotificationEnqueueError(
            "enqueue response is not valid JSON"
        ) from None
    return _validate_aggregate_response(
        value,
        expected_candidate_count=len(candidates),
        expected_shadow_mode=shadow_mode,
    )


def enqueue_candidates(
    supabase_url: str,
    service_role_key: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    shadow_mode: bool,
    canary_user_id: str | None,
    use_allowlist: bool,
    allow_all: bool,
    timeout: int = RPC_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> dict[str, int | bool]:
    _validate_rollout_controls(
        shadow_mode, canary_user_id, use_allowlist, allow_all
    )
    totals: dict[str, int | bool] = {
        field: 0 for field in RPC_COUNT_FIELDS
    }
    totals["shadow_mode"] = shadow_mode
    for offset in range(0, len(candidates), MAX_CANDIDATES_PER_REQUEST):
        aggregate = enqueue_candidate_batch(
            supabase_url,
            service_role_key,
            candidates[offset : offset + MAX_CANDIDATES_PER_REQUEST],
            shadow_mode=shadow_mode,
            canary_user_id=canary_user_id,
            use_allowlist=use_allowlist,
            allow_all=allow_all,
            timeout=timeout,
            opener=opener,
        )
        for field in RPC_COUNT_FIELDS:
            totals[field] = int(totals[field]) + int(aggregate[field])
    return totals


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Match availability and safely enqueue user LINE notifications."
    )
    parser.add_argument("--availability", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        shadow_mode = _required_boolean_env("LINE_NOTIFICATION_SHADOW_MODE")
        use_allowlist = _required_boolean_env(
            "LINE_NOTIFICATION_USE_ALLOWLIST"
        )
        allow_all = _required_boolean_env("LINE_NOTIFICATION_ALLOW_ALL")
        canary_user_id = _optional_uuid_env("LINE_NOTIFICATION_CANARY_USER_ID")
        _validate_rollout_controls(
            shadow_mode, canary_user_id, use_allowlist, allow_all
        )
        supabase_url = os.environ.get("SUPABASE_URL", "")
        service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        rules = matching.fetch_notification_rules(supabase_url, service_role_key)
        availability = matching.load_availability_document(args.availability)
        slots = matching.extract_available_slots(availability)
        matches = matching.match_notification_rules(rules, availability)
        candidates = build_enqueue_candidates(matches)
        totals = enqueue_candidates(
            supabase_url,
            service_role_key,
            candidates,
            shadow_mode=shadow_mode,
            canary_user_id=canary_user_id,
            use_allowlist=use_allowlist,
            allow_all=allow_all,
        )
    except (
        matching.MatchingInputError,
        matching.NotificationRuleFetchError,
        LineNotificationEnqueueError,
    ):
        print("LINE notification enqueue failed", file=sys.stderr)
        return 1

    summary = {
        "rules_evaluated": len(rules),
        "slots_evaluated": len(slots),
        "match_candidates": len(matches),
        "enqueue_candidates": totals["candidate_count"],
        "eligible_candidates": totals["eligible_candidate_count"],
        "inserted_delivery_items": totals["inserted_delivery_item_count"],
        "inserted_messages": totals["inserted_message_count"],
        "linked_items": totals["linked_item_count"],
        "shadow_mode": str(bool(totals["shadow_mode"])).lower(),
    }
    print(" ".join(f"{key}={value}" for key, value in summary.items()))
    return 0


def _validate_aggregate_response(
    value: Any,
    *,
    expected_candidate_count: int,
    expected_shadow_mode: bool,
) -> dict[str, int | bool]:
    if not isinstance(value, list) or len(value) != 1:
        raise LineNotificationEnqueueError("enqueue response has an invalid format")
    aggregate = value[0]
    if not isinstance(aggregate, Mapping) or set(aggregate) != RPC_RESPONSE_FIELDS:
        raise LineNotificationEnqueueError("enqueue response has an invalid format")
    normalized: dict[str, int | bool] = {}
    for field in RPC_COUNT_FIELDS:
        field_value = aggregate[field]
        if (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or field_value < 0
        ):
            raise LineNotificationEnqueueError(
                "enqueue response has an invalid format"
            )
        normalized[field] = field_value
    if not isinstance(aggregate["shadow_mode"], bool):
        raise LineNotificationEnqueueError("enqueue response has an invalid format")
    normalized["shadow_mode"] = aggregate["shadow_mode"]
    if (
        normalized["candidate_count"] != expected_candidate_count
        or normalized["shadow_mode"] is not expected_shadow_mode
        or int(normalized["eligible_candidate_count"]) > expected_candidate_count
        or int(normalized["inserted_delivery_item_count"])
        > int(normalized["eligible_candidate_count"])
        or int(normalized["inserted_message_count"])
        > int(normalized["inserted_delivery_item_count"])
        or int(normalized["linked_item_count"])
        != int(normalized["inserted_delivery_item_count"])
        or (
            expected_shadow_mode
            and any(
                int(normalized[field]) != 0
                for field in (
                    "inserted_delivery_item_count",
                    "inserted_message_count",
                    "linked_item_count",
                )
            )
        )
    ):
        raise LineNotificationEnqueueError("enqueue response has invalid counts")
    return normalized


def _required_boolean_env(name: str) -> bool:
    value = os.environ.get(name, "")
    if value not in {"true", "false"}:
        raise LineNotificationEnqueueError(f"{name} must be true or false")
    return value == "true"


def _optional_uuid_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise LineNotificationEnqueueError(f"{name} must be a UUID") from None


def _validate_rollout_controls(
    shadow_mode: bool,
    canary_user_id: str | None,
    use_allowlist: bool,
    allow_all: bool,
) -> None:
    if (
        not isinstance(shadow_mode, bool)
        or not isinstance(use_allowlist, bool)
        or not isinstance(allow_all, bool)
    ):
        raise LineNotificationEnqueueError("LINE rollout controls are invalid")
    if canary_user_id is not None:
        try:
            canary_user_id = str(uuid.UUID(canary_user_id))
        except ValueError:
            raise LineNotificationEnqueueError(
                "LINE_NOTIFICATION_CANARY_USER_ID must be a UUID"
            ) from None
    rollout_mode_count = sum(
        (canary_user_id is not None, use_allowlist, allow_all)
    )
    if rollout_mode_count > 1:
        raise LineNotificationEnqueueError("LINE rollout controls are ambiguous")
    if not shadow_mode and rollout_mode_count != 1:
        raise LineNotificationEnqueueError(
            "live LINE enqueue requires canary, allowlist, or explicit allow-all"
        )


if __name__ == "__main__":
    raise SystemExit(main())
