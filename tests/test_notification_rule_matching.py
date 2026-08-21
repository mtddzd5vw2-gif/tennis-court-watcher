from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from scripts import match_notification_rules as matching


SATURDAY = "2026-08-08"


def make_rule(**overrides: Any) -> dict[str, Any]:
    rule = {
        "rule_id": "rule-b",
        "user_id": "user-b",
        "is_enabled": True,
        "date_from": None,
        "date_to": None,
        "start_time": "09:00:00",
        "end_time": "11:00:00",
        "minimum_duration_minutes": 60,
        "include_holidays": False,
        "facility_ids": ["facility-a"],
        "weekdays": [6],
    }
    rule.update(overrides)
    return rule


def make_rpc_rule(**overrides: Any) -> dict[str, Any]:
    rule = make_rule(**overrides)
    del rule["is_enabled"]
    return rule


def make_slot(**overrides: Any) -> dict[str, Any]:
    slot = {
        "slot_id": "slot-a",
        "facility_id": "facility-a",
        "facility_name": "施設A",
        "date": SATURDAY,
        "court_name": "Aコート",
        "start_time": "09:00",
        "end_time": "11:00",
        "duration_minutes": 120,
        "status": "available",
        "reservation_url": "https://example.test/reserve",
    }
    slot.update(overrides)
    return slot


def make_availability(
    *slots: dict[str, Any],
    date_status: str = "success",
    day_type: str = "weekend",
) -> dict[str, Any]:
    selected_slots = list(slots) or [make_slot()]
    return {
        "schema_version": 2,
        "facilities": [
            {
                "id": "facility-a",
                "name": "施設A",
                "dates": [
                    {
                        "date": selected_slots[0]["date"],
                        "day_type": day_type,
                        "status": date_status,
                        "availability": selected_slots,
                    }
                ],
            }
        ],
    }


def test_facility_and_iso_weekday_must_match() -> None:
    availability = make_availability(make_slot())

    assert matching.match_notification_rules([make_rule()], availability)
    assert not matching.match_notification_rules(
        [make_rule(facility_ids=["facility-b"])], availability
    )
    assert not matching.match_notification_rules(
        [make_rule(weekdays=[7])], availability
    )


@pytest.mark.parametrize(
    ("date_from", "date_to", "expected"),
    [
        (SATURDAY, SATURDAY, True),
        ("2026-08-07", SATURDAY, True),
        (SATURDAY, "2026-08-09", True),
        ("2026-08-09", None, False),
        (None, "2026-08-07", False),
        (None, None, True),
    ],
)
def test_date_range_is_optional_and_inclusive(
    date_from: str | None,
    date_to: str | None,
    expected: bool,
) -> None:
    candidates = matching.match_notification_rules(
        [make_rule(date_from=date_from, date_to=date_to)],
        make_availability(make_slot()),
    )

    assert bool(candidates) is expected


@pytest.mark.parametrize("minimum_duration", [60, 120])
def test_minimum_duration_accepts_database_constraint_values(
    minimum_duration: int,
) -> None:
    rule = matching.normalize_notification_rule(
        make_rule(minimum_duration_minutes=minimum_duration)
    )

    assert rule.minimum_duration_minutes == minimum_duration


@pytest.mark.parametrize("minimum_duration", [1, 30, 61, 180, 300, 360, True])
def test_minimum_duration_rejects_values_outside_database_constraints(
    minimum_duration: int | bool,
) -> None:
    with pytest.raises(matching.MatchingInputError):
        matching.normalize_notification_rule(
            make_rule(minimum_duration_minutes=minimum_duration)
        )


@pytest.mark.parametrize(
    ("slot_start", "slot_end", "rule_start", "rule_end", "minimum", "expected"),
    [
        ("09:00", "11:00", "09:00", "11:00", 120, True),
        ("08:30", "13:00", "09:00", "11:00", 120, True),
        ("10:00", "13:00", "09:00", "11:00", 60, True),
        ("10:00", "13:00", "09:00", "12:00", 180, False),
        ("10:00", "13:00", "09:00", "11:00", 120, False),
        ("11:00", "13:00", "09:00", "11:00", 60, False),
    ],
)
def test_match_uses_actual_time_overlap(
    slot_start: str,
    slot_end: str,
    rule_start: str,
    rule_end: str,
    minimum: int,
    expected: bool,
) -> None:
    candidates = matching.match_notification_rules(
        [
            make_rule(
                start_time=rule_start,
                end_time=rule_end,
                minimum_duration_minutes=minimum,
            )
        ],
        make_availability(
            make_slot(
                start_time=slot_start,
                end_time=slot_end,
                duration_minutes=270,
            )
        ),
    )

    assert bool(candidates) is expected
    if expected:
        matched_rule = candidates[0]["matched_rules"][0]
        assert matched_rule["matched_start_time"] == max(slot_start, rule_start)
        assert matched_rule["matched_end_time"] == min(slot_end, rule_end)
        assert matched_rule["matched_duration_minutes"] >= minimum


@pytest.mark.parametrize(
    "rule_overrides",
    [
        {"is_enabled": False},
        {"facility_ids": []},
        {"weekdays": []},
    ],
)
def test_disabled_or_incomplete_rules_do_not_match(
    rule_overrides: dict[str, Any],
) -> None:
    assert not matching.match_notification_rules(
        [make_rule(**rule_overrides)],
        make_availability(make_slot()),
    )


def test_unavailable_slot_does_not_match() -> None:
    availability = make_availability(make_slot(status="reserved"))

    assert not matching.match_notification_rules([make_rule()], availability)
    assert matching.extract_available_slots(availability) == []


@pytest.mark.parametrize(
    "date_status",
    ["error", "selector_pending", "fallback_from_previous"],
)
def test_only_success_date_entries_can_create_matches(date_status: str) -> None:
    availability = make_availability(
        make_slot(),
        date_status=date_status,
    )

    assert not matching.match_notification_rules([make_rule()], availability)


def test_success_labeled_fallback_data_is_still_excluded() -> None:
    availability = make_availability(make_slot())
    availability["facilities"][0]["dates"][0]["fallback_from_previous"] = True

    assert not matching.match_notification_rules([make_rule()], availability)


def test_holiday_selection_is_independent_of_the_dates_iso_weekday() -> None:
    holiday_date = "2026-08-10"
    availability = make_availability(
        make_slot(date=holiday_date),
        day_type="holiday",
    )

    assert matching.match_notification_rules(
        [make_rule(weekdays=[], include_holidays=True)],
        availability,
    )
    assert not matching.match_notification_rules(
        [make_rule(weekdays=[6], include_holidays=False)],
        availability,
    )


def test_holiday_only_rule_does_not_match_an_ordinary_weekend() -> None:
    assert not matching.match_notification_rules(
        [make_rule(weekdays=[], include_holidays=True)],
        make_availability(make_slot(), day_type="weekend"),
    )


def test_same_user_and_slot_is_deduplicated_and_matched_rules_are_sorted() -> None:
    rules = [
        make_rule(rule_id="rule-z", user_id="user-a"),
        make_rule(rule_id="rule-a", user_id="user-a", start_time="09:00"),
    ]

    candidates = matching.match_notification_rules(
        rules,
        make_availability(make_slot()),
    )

    assert len(candidates) == 1
    assert [rule["rule_id"] for rule in candidates[0]["matched_rules"]] == [
        "rule-a",
        "rule-z",
    ]
    assert candidates[0]["matched_rules"][0] == {
        "rule_id": "rule-a",
        "matched_start_time": "09:00",
        "matched_end_time": "11:00",
        "matched_duration_minutes": 120,
    }


def test_different_users_get_separate_candidates_in_deterministic_order() -> None:
    candidates = matching.match_notification_rules(
        [
            make_rule(rule_id="rule-b", user_id="user-b"),
            make_rule(rule_id="rule-a", user_id="user-a"),
        ],
        make_availability(make_slot()),
    )

    assert [candidate["user_id"] for candidate in candidates] == [
        "user-a",
        "user-b",
    ]
    assert {candidate["slot_id"] for candidate in candidates} == {"slot-a"}


def test_duplicate_input_slot_is_evaluated_once() -> None:
    slot = make_slot()
    availability = make_availability(slot, dict(slot))

    slots = matching.extract_available_slots(availability)
    candidates = matching.match_notification_rules([make_rule()], availability)

    assert len(slots) == 1
    assert len(candidates) == 1


def test_inputs_are_not_mutated() -> None:
    rule = make_rule(facility_ids=["facility-a", "facility-a"])
    availability = make_availability(make_slot())
    before_rule = json.loads(json.dumps(rule))
    before_availability = json.loads(json.dumps(availability))

    matching.match_notification_rules([rule], availability)

    assert rule == before_rule
    assert availability == before_availability


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.closed = False

    def read(self, _limit: int) -> bytes:
        return self.body

    def close(self) -> None:
        self.closed = True


def test_fetch_rules_uses_private_rpc_headers_and_timeout() -> None:
    response = FakeResponse(json.dumps([make_rpc_rule()]).encode())
    observed: dict[str, Any] = {}

    def opener(request: Any, timeout: int) -> FakeResponse:
        observed["request"] = request
        observed["timeout"] = timeout
        return response

    rules = matching.fetch_notification_rules(
        "https://project.supabase.co/",
        "service-role-test-key",
        opener=opener,
    )

    request = observed["request"]
    assert request.full_url.endswith(
        "/rest/v1/rpc/list_notification_rules_for_matching"
    )
    assert request.method == "POST"
    assert request.data == b"{}"
    normal_headers = {
        name.lower(): value for name, value in request.headers.items()
    }
    unredirected_headers = {
        name.lower(): value
        for name, value in request.unredirected_hdrs.items()
    }
    assert set(normal_headers) == {"accept", "content-type"}
    assert "authorization" not in normal_headers
    assert "apikey" not in normal_headers
    assert unredirected_headers["authorization"] == (
        "Bearer service-role-test-key"
    )
    assert unredirected_headers["apikey"] == "service-role-test-key"
    assert request.get_header("Authorization") == "Bearer service-role-test-key"
    assert request.get_header("Apikey") == "service-role-test-key"
    sent_headers = {
        name.lower(): value for name, value in request.header_items()
    }
    assert sent_headers["authorization"] == "Bearer service-role-test-key"
    assert sent_headers["apikey"] == "service-role-test-key"
    assert observed["timeout"] == matching.RPC_TIMEOUT_SECONDS
    assert response.closed
    assert rules[0].rule_id == "rule-b"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_handler_rejects_every_redirect_status(status: int) -> None:
    request = urllib.request.Request("https://project.supabase.co/start")
    headers = Message()
    headers["Location"] = "https://redirect.example/target"

    redirected = matching.RejectRedirectHandler().redirect_request(
        request,
        None,
        status,
        "redirect",
        headers,
        headers["Location"],
    )

    assert redirected is None


class RedirectingHTTPHandler(urllib.request.BaseHandler):
    handler_order = 100

    def __init__(self, redirect_url: str) -> None:
        self.redirect_url = redirect_url
        self.requested_urls: list[str] = []

    def http_open(self, request: urllib.request.Request) -> Any:
        self.requested_urls.append(request.full_url)
        headers = Message()
        headers["Location"] = self.redirect_url
        response = urllib.response.addinfourl(
            io.BytesIO(b"redirect response body"),
            headers,
            request.full_url,
            302,
        )
        response.msg = "Found"
        return response


def test_no_redirect_opener_does_not_follow_302_response() -> None:
    initial_url = "http://initial.example/rpc"
    redirect_url = "http://redirect.example/secret-target"
    transport = RedirectingHTTPHandler(redirect_url)
    opener = urllib.request.build_opener(
        matching.RejectRedirectHandler(),
        transport,
    )

    with pytest.raises(urllib.error.HTTPError) as error:
        opener.open(urllib.request.Request(initial_url), timeout=1)

    assert error.value.code == 302
    assert transport.requested_urls == [initial_url]
    redirect_handlers = [
        handler
        for handler in matching._build_no_redirect_opener().handlers
        if isinstance(handler, urllib.request.HTTPRedirectHandler)
    ]
    assert len(redirect_handlers) == 1
    assert isinstance(redirect_handlers[0], matching.RejectRedirectHandler)


def redirect_error(
    request: urllib.request.Request,
    timeout: int,
    *,
    redirect_url: str,
    secret: str,
    rule_id: str,
    user_id: str,
) -> Any:
    assert timeout == matching.RPC_TIMEOUT_SECONDS
    headers = Message()
    headers["Location"] = redirect_url
    body = io.BytesIO(
        f"{secret} {rule_id} {user_id} redirect response".encode()
    )
    raise urllib.error.HTTPError(
        request.full_url,
        302,
        f"redirect to {redirect_url} for {rule_id} {user_id} using {secret}",
        headers,
        body,
    )


def test_redirect_failure_is_converted_to_safe_fetch_error() -> None:
    redirect_url = "https://redirect.example/private-location"
    secret = "service-role-redirect-secret"
    rule_id = "redirect-rule-id"
    user_id = "redirect-user-id"

    with pytest.raises(matching.NotificationRuleFetchError) as error:
        matching.fetch_notification_rules(
            "https://project.supabase.co",
            secret,
            opener=lambda request, timeout: redirect_error(
                request,
                timeout,
                redirect_url=redirect_url,
                secret=secret,
                rule_id=rule_id,
                user_id=user_id,
            ),
        )

    for sensitive_value in (redirect_url, secret, rule_id, user_id):
        assert sensitive_value not in str(error.value)


def test_fetch_rules_rejects_http_and_invalid_json_without_exposing_values() -> None:
    secret = "service-role-do-not-log"

    def failing_opener(_request: Any, timeout: int) -> Any:
        assert timeout == matching.RPC_TIMEOUT_SECONDS
        raise urllib.error.HTTPError(
            "https://project.supabase.co/private",
            500,
            f"response mentions {secret}",
            {},
            None,
        )

    with pytest.raises(matching.NotificationRuleFetchError) as error:
        matching.fetch_notification_rules(
            "https://project.supabase.co",
            secret,
            opener=failing_opener,
        )
    assert secret not in str(error.value)

    with pytest.raises(matching.NotificationRuleFetchError):
        matching.fetch_notification_rules(
            "https://project.supabase.co",
            secret,
            opener=lambda *_args, **_kwargs: FakeResponse(b"not-json"),
        )
    with pytest.raises(matching.NotificationRuleFetchError):
        matching.fetch_notification_rules(
            "https://project.supabase.co",
            secret,
            opener=lambda *_args, **_kwargs: FakeResponse(
                json.dumps([make_rule()]).encode()
            ),
        )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://oocqyeariwuppkeaeioh.supabase.co",
            "https://oocqyeariwuppkeaeioh.supabase.co",
        ),
        (
            "https://oocqyeariwuppkeaeioh.supabase.co/",
            "https://oocqyeariwuppkeaeioh.supabase.co",
        ),
    ],
)
def test_supabase_url_accepts_standard_project_urls(
    url: str,
    expected: str,
) -> None:
    assert matching._validated_supabase_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://oocqyeariwuppkeaeioh.supabase.co",
        "https://example.com",
        "https://project.supabase.co.evil.example",
        "https://supabase.co",
        "https://user:password@oocqyeariwuppkeaeioh.supabase.co",
        "https://oocqyeariwuppkeaeioh.supabase.co?secret=value",
        "https://oocqyeariwuppkeaeioh.supabase.co#fragment",
        "https://oocqyeariwuppkeaeioh.supabase.co/rest",
        "https://oocqyeariwuppkeaeioh.supabase.co:443",
    ],
)
def test_fetch_rules_only_accepts_safe_https_urls(url: str) -> None:
    with pytest.raises(matching.NotificationRuleFetchError) as error:
        matching._validated_supabase_url(url)

    assert url not in str(error.value)


def test_fetch_rules_converts_invalid_minimum_duration_to_safe_error() -> None:
    response = FakeResponse(
        json.dumps([make_rpc_rule(minimum_duration_minutes=61)]).encode()
    )

    with pytest.raises(matching.NotificationRuleFetchError) as error:
        matching.fetch_notification_rules(
            "https://oocqyeariwuppkeaeioh.supabase.co",
            "service-role-test-key",
            opener=lambda *_args, **_kwargs: response,
        )

    assert "61" not in str(error.value)
    assert "rule-b" not in str(error.value)


def test_cli_logs_aggregate_counts_only_and_creates_no_result_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    availability_path = tmp_path / "availability.json"
    availability_path.write_text(
        json.dumps(make_availability(make_slot())),
        encoding="utf-8",
    )
    secret = "service-role-secret-value"
    rule_id = "sensitive-rule-id"
    user_id = "sensitive-user-id"
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", secret)
    monkeypatch.setattr(
        matching,
        "fetch_notification_rules",
        lambda *_args, **_kwargs: matching.normalize_notification_rules(
            [make_rule(rule_id=rule_id, user_id=user_id)]
        ),
    )
    before = set(tmp_path.iterdir())

    result = matching.main(["--availability", str(availability_path)])

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    assert captured.out.strip() == (
        "rules_evaluated=1 slots_evaluated=1 matched_users=1 "
        "matched_slots=1 match_candidates=1"
    )
    for sensitive_value in (secret, rule_id, user_id):
        assert sensitive_value not in captured.out
        assert sensitive_value not in captured.err
    assert set(tmp_path.iterdir()) == before


def test_redirect_failure_does_not_expose_details_in_cli_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    availability_path = tmp_path / "availability.json"
    availability_path.write_text(
        json.dumps(make_availability(make_slot())),
        encoding="utf-8",
    )
    redirect_url = "https://redirect.example/private-location"
    secret = "service-role-cli-redirect-secret"
    rule_id = "cli-redirect-rule-id"
    user_id = "cli-redirect-user-id"
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", secret)
    monkeypatch.setattr(
        matching,
        "_open_without_redirects",
        lambda request, timeout: redirect_error(
            request,
            timeout,
            redirect_url=redirect_url,
            secret=secret,
            rule_id=rule_id,
            user_id=user_id,
        ),
    )
    before = set(tmp_path.iterdir())

    result = matching.main(["--availability", str(availability_path)])

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "notification matching failed\n"
    for sensitive_value in (redirect_url, secret, rule_id, user_id):
        assert sensitive_value not in captured.out
        assert sensitive_value not in captured.err
    assert set(tmp_path.iterdir()) == before
