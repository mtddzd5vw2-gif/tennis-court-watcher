from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

if __package__:
    from scripts import match_notification_rules as matching
else:
    import match_notification_rules as matching


RPC_PATH = "/rest/v1/rpc/enqueue_email_notification_candidates"
RPC_TIMEOUT_SECONDS = 20
MAX_RPC_RESPONSE_BYTES = 64 * 1024
MAX_CANDIDATES_PER_REQUEST = 500
RPC_RESPONSE_FIELDS = {
    "candidate_count",
    "inserted_delivery_item_count",
    "inserted_message_count",
    "linked_item_count",
}


class EmailNotificationEnqueueError(RuntimeError):
    """Raised when candidates cannot be converted or enqueued safely."""


def _required_string(candidate: Mapping[str, Any], field: str) -> str:
    value = candidate.get(field)
    if not isinstance(value, str) or not value.strip():
        raise EmailNotificationEnqueueError("notification candidate is invalid")
    return value


def build_enqueue_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one pure matching result to the queue RPC contract."""
    if not isinstance(candidate, Mapping):
        raise EmailNotificationEnqueueError("notification candidate is invalid")

    matched_rules = candidate.get("matched_rules")
    if not isinstance(matched_rules, list):
        raise EmailNotificationEnqueueError("notification candidate is invalid")

    matched_rule_ids: set[str] = set()
    for matched_rule in matched_rules:
        if not isinstance(matched_rule, Mapping):
            raise EmailNotificationEnqueueError("notification candidate is invalid")
        rule_id = matched_rule.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise EmailNotificationEnqueueError("notification candidate is invalid")
        matched_rule_ids.add(rule_id)
    if not matched_rule_ids:
        raise EmailNotificationEnqueueError("notification candidate is invalid")

    return {
        "user_id": _required_string(candidate, "user_id"),
        "channel": "email",
        "slot_id": _required_string(candidate, "slot_id"),
        "facility_id": _required_string(candidate, "facility_id"),
        "facility_name": _required_string(candidate, "facility_name"),
        "available_date": _required_string(candidate, "date"),
        "start_time": _required_string(candidate, "start_time"),
        "end_time": _required_string(candidate, "end_time"),
        "matched_rule_ids": sorted(matched_rule_ids),
        "payload": {
            "court_name": _required_string(candidate, "court_name"),
            "reservation_url": _required_string(candidate, "reservation_url"),
        },
    }


def build_enqueue_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build queue inputs in a deterministic order independent of caller order."""
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


def _validate_aggregate_response(
    value: Any,
    *,
    expected_candidate_count: int,
) -> dict[str, int]:
    if not isinstance(value, list) or len(value) != 1:
        raise EmailNotificationEnqueueError("enqueue response has an invalid format")
    aggregate = value[0]
    if not isinstance(aggregate, Mapping) or set(aggregate) != RPC_RESPONSE_FIELDS:
        raise EmailNotificationEnqueueError("enqueue response has an invalid format")

    normalized: dict[str, int] = {}
    for field in RPC_RESPONSE_FIELDS:
        field_value = aggregate[field]
        if (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or field_value < 0
        ):
            raise EmailNotificationEnqueueError(
                "enqueue response has an invalid format"
            )
        normalized[field] = field_value

    if (
        normalized["candidate_count"] != expected_candidate_count
        or normalized["inserted_delivery_item_count"] > expected_candidate_count
        or normalized["inserted_message_count"]
        > normalized["inserted_delivery_item_count"]
        or normalized["linked_item_count"]
        != normalized["inserted_delivery_item_count"]
    ):
        raise EmailNotificationEnqueueError("enqueue response has invalid counts")
    return normalized


def enqueue_candidate_batch(
    supabase_url: str,
    service_role_key: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    timeout: int = RPC_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> dict[str, int]:
    if len(candidates) < 1 or len(candidates) > MAX_CANDIDATES_PER_REQUEST:
        raise EmailNotificationEnqueueError(
            "enqueue batch must contain between 1 and 500 candidates"
        )
    try:
        base_url = matching._validated_supabase_url(supabase_url)
    except matching.NotificationRuleFetchError:
        raise EmailNotificationEnqueueError(
            "SUPABASE_URL must be an HTTPS project URL"
        ) from None
    if not isinstance(service_role_key, str) or not service_role_key.strip():
        raise EmailNotificationEnqueueError("SUPABASE_SERVICE_ROLE_KEY is required")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise EmailNotificationEnqueueError("RPC timeout must be positive")

    body = json.dumps(
        {"p_candidates": list(candidates)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{RPC_PATH}",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
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
            raise EmailNotificationEnqueueError("enqueue request failed")
        response_body = response.read(MAX_RPC_RESPONSE_BYTES + 1)
    except EmailNotificationEnqueueError:
        raise
    except Exception:
        raise EmailNotificationEnqueueError("enqueue request failed") from None
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    if not isinstance(response_body, bytes):
        raise EmailNotificationEnqueueError("enqueue response is not valid JSON")
    if len(response_body) > MAX_RPC_RESPONSE_BYTES:
        raise EmailNotificationEnqueueError("enqueue response is too large")
    try:
        response_value = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EmailNotificationEnqueueError(
            "enqueue response is not valid JSON"
        ) from None
    return _validate_aggregate_response(
        response_value,
        expected_candidate_count=len(candidates),
    )


def enqueue_candidates(
    supabase_url: str,
    service_role_key: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    timeout: int = RPC_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> dict[str, int]:
    """Enqueue bounded batches and combine only non-sensitive aggregate counts."""
    totals = {field: 0 for field in RPC_RESPONSE_FIELDS}
    for offset in range(0, len(candidates), MAX_CANDIDATES_PER_REQUEST):
        batch = candidates[offset : offset + MAX_CANDIDATES_PER_REQUEST]
        aggregate = enqueue_candidate_batch(
            supabase_url,
            service_role_key,
            batch,
            timeout=timeout,
            opener=opener,
        )
        for field in totals:
            totals[field] += aggregate[field]
    return totals


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Match current availability and enqueue user email notifications."
    )
    parser.add_argument(
        "--availability",
        type=Path,
        required=True,
        help="Path to the current availability.json generated by the scraper.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        supabase_url = os.environ.get("SUPABASE_URL", "")
        service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        rules = matching.fetch_notification_rules(supabase_url, service_role_key)
        availability = matching.load_availability_document(args.availability)
        slots = matching.extract_available_slots(availability)
        match_candidates = matching.match_notification_rules(rules, availability)
        queue_candidates = build_enqueue_candidates(match_candidates)
        totals = enqueue_candidates(
            supabase_url,
            service_role_key,
            queue_candidates,
        )
    except (
        matching.MatchingInputError,
        matching.NotificationRuleFetchError,
        EmailNotificationEnqueueError,
    ):
        print("email notification enqueue failed", file=sys.stderr)
        return 1

    summary = {
        "rules_evaluated": len(rules),
        "slots_evaluated": len(slots),
        "match_candidates": len(match_candidates),
        "enqueue_candidates": totals["candidate_count"],
        "inserted_delivery_items": totals["inserted_delivery_item_count"],
        "inserted_messages": totals["inserted_message_count"],
        "linked_items": totals["linked_item_count"],
    }
    print(" ".join(f"{key}={value}" for key, value in summary.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
