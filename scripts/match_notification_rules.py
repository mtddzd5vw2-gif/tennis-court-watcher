from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


RPC_PATH = "/rest/v1/rpc/list_notification_rules_for_matching"
RPC_TIMEOUT_SECONDS = 20
MAX_RPC_RESPONSE_BYTES = 4 * 1024 * 1024
RULE_FIELDS = {
    "rule_id",
    "user_id",
    "is_enabled",
    "date_from",
    "date_to",
    "start_time",
    "end_time",
    "minimum_duration_minutes",
    "include_holidays",
    "facility_ids",
    "weekdays",
}
RPC_RULE_FIELDS = RULE_FIELDS - {"is_enabled"}
SLOT_FIELDS = (
    "slot_id",
    "facility_id",
    "facility_name",
    "date",
    "court_name",
    "start_time",
    "end_time",
    "duration_minutes",
    "status",
    "reservation_url",
)
ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
CLOCK_TIME_PATTERN = re.compile(r"\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?")
SUPABASE_PROJECT_HOST_PATTERN = re.compile(
    r"[a-z0-9]{1,63}\.supabase\.co",
    re.ASCII,
)


class MatchingInputError(ValueError):
    """Raised when notification rules or availability data are malformed."""


class NotificationRuleFetchError(RuntimeError):
    """Raised when the private Supabase RPC cannot be read safely."""


class RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject every HTTP redirect so credentials stay on the validated origin."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


@dataclass(frozen=True)
class NotificationRule:
    rule_id: str
    user_id: str
    is_enabled: bool
    date_from: date | None
    date_to: date | None
    start_time: time
    end_time: time
    minimum_duration_minutes: int
    include_holidays: bool
    facility_ids: tuple[str, ...]
    weekdays: tuple[int, ...]


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MatchingInputError(f"{field} must be a non-empty string")
    return value


def _iso_date(value: Any, field: str, *, optional: bool = False) -> date | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or ISO_DATE_PATTERN.fullmatch(value) is None:
        raise MatchingInputError(f"{field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise MatchingInputError(f"{field} must be an ISO date") from exc


def _clock_time(value: Any, field: str) -> time:
    if not isinstance(value, str) or CLOCK_TIME_PATTERN.fullmatch(value) is None:
        raise MatchingInputError(f"{field} must be a clock time")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise MatchingInputError(f"{field} must be a clock time") from exc
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise MatchingInputError(f"{field} must use whole-minute local time")
    return parsed


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _format_time(value: time) -> str:
    return value.strftime("%H:%M")


def normalize_notification_rule(raw_rule: Mapping[str, Any]) -> NotificationRule:
    if not isinstance(raw_rule, Mapping):
        raise MatchingInputError("each notification rule must be an object")
    if set(raw_rule) != RULE_FIELDS:
        raise MatchingInputError("notification rule fields are invalid")

    is_enabled = raw_rule["is_enabled"]
    if not isinstance(is_enabled, bool):
        raise MatchingInputError("is_enabled must be a boolean")

    date_from = _iso_date(raw_rule["date_from"], "date_from", optional=True)
    date_to = _iso_date(raw_rule["date_to"], "date_to", optional=True)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise MatchingInputError("date_from must not be after date_to")

    start_time = _clock_time(raw_rule["start_time"], "start_time")
    end_time = _clock_time(raw_rule["end_time"], "end_time")
    start_minutes = _minutes(start_time)
    end_minutes = _minutes(end_time)
    if (
        start_minutes < 8 * 60
        or end_minutes > 13 * 60
        or start_time.minute != 0
        or end_time.minute != 0
        or end_minutes - start_minutes < 120
    ):
        raise MatchingInputError(
            "notification time must be a whole-hour range of at least two hours between 08:00 and 13:00"
        )

    minimum_duration = raw_rule["minimum_duration_minutes"]
    if (
        isinstance(minimum_duration, bool)
        or not isinstance(minimum_duration, int)
        or minimum_duration < 60
        or minimum_duration > 300
        or minimum_duration % 60 != 0
        or minimum_duration > end_minutes - start_minutes
    ):
        raise MatchingInputError(
            "minimum_duration_minutes must be 60 to 300 in 60 minute steps and fit the notification time range"
        )

    include_holidays = raw_rule["include_holidays"]
    if not isinstance(include_holidays, bool):
        raise MatchingInputError("include_holidays must be a boolean")

    raw_facility_ids = raw_rule["facility_ids"]
    if not isinstance(raw_facility_ids, list):
        raise MatchingInputError("facility_ids must be an array")
    facility_ids = tuple(
        sorted(
            {
                _non_empty_string(facility_id, "facility_ids")
                for facility_id in raw_facility_ids
            }
        )
    )

    raw_weekdays = raw_rule["weekdays"]
    if not isinstance(raw_weekdays, list):
        raise MatchingInputError("weekdays must be an array")
    weekdays: set[int] = set()
    for weekday in raw_weekdays:
        if (
            isinstance(weekday, bool)
            or not isinstance(weekday, int)
            or weekday < 6
            or weekday > 7
        ):
            raise MatchingInputError("weekdays must contain Saturday or Sunday")
        weekdays.add(weekday)

    return NotificationRule(
        rule_id=_non_empty_string(raw_rule["rule_id"], "rule_id"),
        user_id=_non_empty_string(raw_rule["user_id"], "user_id"),
        is_enabled=is_enabled,
        date_from=date_from,
        date_to=date_to,
        start_time=start_time,
        end_time=end_time,
        minimum_duration_minutes=minimum_duration,
        include_holidays=include_holidays,
        facility_ids=facility_ids,
        weekdays=tuple(sorted(weekdays)),
    )


def normalize_notification_rules(
    rules: Iterable[Mapping[str, Any] | NotificationRule],
) -> list[NotificationRule]:
    by_rule_id: dict[str, NotificationRule] = {}
    for raw_rule in rules:
        rule = (
            raw_rule
            if isinstance(raw_rule, NotificationRule)
            else normalize_notification_rule(raw_rule)
        )
        existing = by_rule_id.get(rule.rule_id)
        if existing is not None and existing != rule:
            raise MatchingInputError("duplicate rule_id has conflicting data")
        by_rule_id[rule.rule_id] = rule
    return sorted(by_rule_id.values(), key=lambda rule: (rule.user_id, rule.rule_id))


def _normalize_slot(
    raw_slot: Mapping[str, Any],
    *,
    facility_id: str,
    facility_name: str,
    entry_date: date,
    is_holiday: bool,
) -> dict[str, Any]:
    if not isinstance(raw_slot, Mapping):
        raise MatchingInputError("each availability slot must be an object")
    if any(field not in raw_slot for field in SLOT_FIELDS):
        raise MatchingInputError("availability slot fields are invalid")

    slot_id = _non_empty_string(raw_slot["slot_id"], "slot_id")
    slot_facility_id = _non_empty_string(raw_slot["facility_id"], "facility_id")
    slot_facility_name = _non_empty_string(
        raw_slot["facility_name"], "facility_name"
    )
    slot_date = _iso_date(raw_slot["date"], "date")
    if slot_facility_id != facility_id or slot_facility_name != facility_name:
        raise MatchingInputError("availability slot facility is inconsistent")
    if slot_date != entry_date:
        raise MatchingInputError("availability slot date is inconsistent")

    start_time = _clock_time(raw_slot["start_time"], "start_time")
    end_time = _clock_time(raw_slot["end_time"], "end_time")
    if start_time >= end_time:
        raise MatchingInputError("availability start_time must be before end_time")

    duration = raw_slot["duration_minutes"]
    if (
        isinstance(duration, bool)
        or not isinstance(duration, int)
        or duration < 1
    ):
        raise MatchingInputError("duration_minutes must be a positive integer")

    return {
        "slot_id": slot_id,
        "facility_id": slot_facility_id,
        "facility_name": slot_facility_name,
        "date": slot_date.isoformat(),
        "court_name": _non_empty_string(raw_slot["court_name"], "court_name"),
        "start_time": _format_time(start_time),
        "end_time": _format_time(end_time),
        "duration_minutes": duration,
        "status": _non_empty_string(raw_slot["status"], "status"),
        "reservation_url": _non_empty_string(
            raw_slot["reservation_url"], "reservation_url"
        ),
        "_is_holiday": is_holiday,
    }


def extract_available_slots(
    availability_document: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(availability_document, Mapping):
        raise MatchingInputError("availability document must be an object")
    facilities = availability_document.get("facilities")
    if not isinstance(facilities, list):
        raise MatchingInputError("availability facilities must be an array")

    slots_by_id: dict[str, dict[str, Any]] = {}
    for facility in facilities:
        if not isinstance(facility, Mapping):
            raise MatchingInputError("each facility must be an object")
        facility_id = _non_empty_string(facility.get("id"), "facility id")
        facility_name = _non_empty_string(facility.get("name"), "facility name")
        date_entries = facility.get("dates")
        if not isinstance(date_entries, list):
            raise MatchingInputError("facility dates must be an array")

        for date_entry in date_entries:
            if not isinstance(date_entry, Mapping):
                raise MatchingInputError("each date entry must be an object")
            if (
                date_entry.get("status") != "success"
                or date_entry.get("fallback_from_previous") is True
            ):
                continue
            entry_date = _iso_date(date_entry.get("date"), "date entry date")
            is_holiday = date_entry.get("day_type") == "holiday"
            raw_slots = date_entry.get("availability")
            if not isinstance(raw_slots, list):
                raise MatchingInputError("date entry availability must be an array")
            for raw_slot in raw_slots:
                slot = _normalize_slot(
                    raw_slot,
                    facility_id=facility_id,
                    facility_name=facility_name,
                    entry_date=entry_date,
                    is_holiday=is_holiday,
                )
                if slot["status"] != "available":
                    continue
                existing = slots_by_id.get(slot["slot_id"])
                if existing is not None and existing != slot:
                    raise MatchingInputError(
                        "duplicate slot_id has conflicting availability data"
                    )
                slots_by_id[slot["slot_id"]] = slot

    return sorted(
        slots_by_id.values(),
        key=lambda slot: (
            slot["date"],
            slot["facility_id"],
            slot["start_time"],
            slot["end_time"],
            slot["court_name"],
            slot["slot_id"],
        ),
    )


def _match_normalized_rules_to_slots(
    rules: Sequence[NotificationRule],
    slots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    candidates: dict[tuple[str, str], dict[str, Any]] = {}

    for slot in slots:
        slot_date = date.fromisoformat(str(slot["date"]))
        slot_start = time.fromisoformat(str(slot["start_time"]))
        slot_end = time.fromisoformat(str(slot["end_time"]))
        slot_start_minutes = _minutes(slot_start)
        slot_end_minutes = _minutes(slot_end)

        for rule in rules:
            matches_day = (
                slot_date.isoweekday() in rule.weekdays
                or (rule.include_holidays and slot.get("_is_holiday") is True)
            )
            if (
                not rule.is_enabled
                or not rule.facility_ids
                or (not rule.weekdays and not rule.include_holidays)
                or slot["facility_id"] not in rule.facility_ids
                or not matches_day
                or (rule.date_from is not None and slot_date < rule.date_from)
                or (rule.date_to is not None and slot_date > rule.date_to)
            ):
                continue

            matched_start_minutes = max(
                slot_start_minutes, _minutes(rule.start_time)
            )
            matched_end_minutes = min(slot_end_minutes, _minutes(rule.end_time))
            matched_duration = matched_end_minutes - matched_start_minutes
            if matched_duration < rule.minimum_duration_minutes:
                continue

            matched_start = time(
                matched_start_minutes // 60, matched_start_minutes % 60
            )
            matched_end = time(
                matched_end_minutes // 60, matched_end_minutes % 60
            )
            candidate_key = (rule.user_id, str(slot["slot_id"]))
            candidate = candidates.setdefault(
                candidate_key,
                {
                    "user_id": rule.user_id,
                    **{
                        key: value
                        for key, value in slot.items()
                        if not key.startswith("_")
                    },
                    "matched_rules": [],
                },
            )
            candidate["matched_rules"].append(
                {
                    "rule_id": rule.rule_id,
                    "matched_start_time": _format_time(matched_start),
                    "matched_end_time": _format_time(matched_end),
                    "matched_duration_minutes": matched_duration,
                }
            )

    for candidate in candidates.values():
        unique_rules = {
            (
                matched_rule["rule_id"],
                matched_rule["matched_start_time"],
                matched_rule["matched_end_time"],
                matched_rule["matched_duration_minutes"],
            ): matched_rule
            for matched_rule in candidate["matched_rules"]
        }
        candidate["matched_rules"] = [
            unique_rules[key] for key in sorted(unique_rules)
        ]

    return sorted(
        candidates.values(),
        key=lambda candidate: (
            candidate["user_id"],
            candidate["date"],
            candidate["facility_id"],
            candidate["start_time"],
            candidate["end_time"],
            candidate["court_name"],
            candidate["slot_id"],
        ),
    )


def match_notification_rules(
    rules: Iterable[Mapping[str, Any] | NotificationRule],
    availability_document: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return deterministic user/slot candidates without external communication."""
    normalized_rules = normalize_notification_rules(rules)
    available_slots = extract_available_slots(availability_document)
    return _match_normalized_rules_to_slots(normalized_rules, available_slots)


def _validated_supabase_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NotificationRuleFetchError(
            "SUPABASE_URL must be an HTTPS project URL"
        )
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise NotificationRuleFetchError(
            "SUPABASE_URL must be an HTTPS project URL"
        ) from None
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not hostname
        or SUPABASE_PROJECT_HOST_PATTERN.fullmatch(hostname) is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.netloc.endswith(":")
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise NotificationRuleFetchError(
            "SUPABASE_URL must be an HTTPS project URL"
        )
    return f"https://{hostname}"


def _build_no_redirect_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(RejectRedirectHandler())


def _open_without_redirects(
    request: urllib.request.Request,
    *,
    timeout: int | float,
) -> Any:
    return _build_no_redirect_opener().open(request, timeout=timeout)


def fetch_notification_rules(
    supabase_url: str,
    service_role_key: str,
    *,
    timeout: int = RPC_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> list[NotificationRule]:
    base_url = _validated_supabase_url(supabase_url)
    if not isinstance(service_role_key, str) or not service_role_key.strip():
        raise NotificationRuleFetchError("SUPABASE_SERVICE_ROLE_KEY is required")
    key = service_role_key.strip()
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
    ):
        raise NotificationRuleFetchError("RPC timeout must be positive")

    request = urllib.request.Request(
        f"{base_url}{RPC_PATH}",
        data=b"{}",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    request.add_unredirected_header("Authorization", f"Bearer {key}")
    request.add_unredirected_header("apikey", key)
    open_request = opener if opener is not None else _open_without_redirects
    response: Any = None
    try:
        response = open_request(request, timeout=timeout)
        status = getattr(response, "status", 200)
        if not isinstance(status, int) or status < 200 or status >= 300:
            raise NotificationRuleFetchError("notification rule request failed")
        body = response.read(MAX_RPC_RESPONSE_BYTES + 1)
    except NotificationRuleFetchError:
        raise
    except Exception:
        raise NotificationRuleFetchError("notification rule request failed") from None
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    if not isinstance(body, bytes):
        raise NotificationRuleFetchError(
            "notification rule response is not valid JSON"
        )
    if len(body) > MAX_RPC_RESPONSE_BYTES:
        raise NotificationRuleFetchError("notification rule response is too large")
    try:
        decoded = body.decode("utf-8")
        raw_rules = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise NotificationRuleFetchError(
            "notification rule response is not valid JSON"
        ) from None
    if not isinstance(raw_rules, list):
        raise NotificationRuleFetchError(
            "notification rule response must be an array"
        )
    try:
        normalized_rpc_rules: list[dict[str, Any]] = []
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, Mapping) or set(raw_rule) != RPC_RULE_FIELDS:
                raise MatchingInputError("notification rule fields are invalid")
            normalized_rpc_rules.append(
                {
                    **dict(raw_rule),
                    "is_enabled": True,
                }
            )
        return normalize_notification_rules(normalized_rpc_rules)
    except MatchingInputError:
        raise NotificationRuleFetchError(
            "notification rule response has an invalid format"
        ) from None


def load_availability_document(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise MatchingInputError("availability file could not be read") from None
    if not isinstance(document, Mapping):
        raise MatchingInputError("availability document must be an object")
    return document


def build_summary(
    rules: Sequence[NotificationRule],
    slots: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    return {
        "rules_evaluated": len(rules),
        "slots_evaluated": len(slots),
        "matched_users": len(
            {str(candidate["user_id"]) for candidate in candidates}
        ),
        "matched_slots": len(
            {str(candidate["slot_id"]) for candidate in candidates}
        ),
        "match_candidates": len(candidates),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch private notification rules and report aggregate availability "
            "match counts."
        )
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
        rules = fetch_notification_rules(
            os.environ.get("SUPABASE_URL", ""),
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
        )
        availability = load_availability_document(args.availability)
        slots = extract_available_slots(availability)
        candidates = _match_normalized_rules_to_slots(rules, slots)
        summary = build_summary(rules, slots, candidates)
    except (MatchingInputError, NotificationRuleFetchError):
        print("notification matching failed", file=sys.stderr)
        return 1

    print(" ".join(f"{key}={value}" for key, value in summary.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
