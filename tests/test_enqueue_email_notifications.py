from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from scripts import enqueue_email_notifications as enqueue
from scripts import match_notification_rules as matching


class FakeResponse:
    def __init__(self, value: Any, *, status: int = 200, raw: bool = False) -> None:
        self.body = value if raw else json.dumps(value).encode("utf-8")
        self.status = status
        self.closed = False

    def read(self, _limit: int) -> bytes:
        return self.body

    def close(self) -> None:
        self.closed = True


def make_candidate(**overrides: Any) -> dict[str, Any]:
    candidate = {
        "user_id": "00000000-0000-4000-8000-000000000001",
        "slot_id": "slot-a",
        "facility_id": "facility-a",
        "facility_name": "施設A",
        "date": "2026-08-15",
        "court_name": "Aコート",
        "start_time": "09:00",
        "end_time": "11:00",
        "matched_rules": [
            {"rule_id": "00000000-0000-4000-8000-000000000002"}
        ],
        "reservation_url": "https://example.test/reserve",
    }
    candidate.update(overrides)
    return candidate


def aggregate(candidate_count: int) -> list[dict[str, int]]:
    return [
        {
            "candidate_count": candidate_count,
            "inserted_delivery_item_count": candidate_count,
            "inserted_message_count": min(candidate_count, 1),
            "linked_item_count": candidate_count,
        }
    ]


def test_candidate_conversion_deduplicates_and_sorts_rule_ids() -> None:
    candidate = make_candidate(
        matched_rules=[
            {"rule_id": "rule-z"},
            {"rule_id": "rule-a"},
            {"rule_id": "rule-z"},
        ]
    )

    assert enqueue.build_enqueue_candidate(candidate) == {
        "user_id": candidate["user_id"],
        "channel": "email",
        "slot_id": "slot-a",
        "facility_id": "facility-a",
        "facility_name": "施設A",
        "available_date": "2026-08-15",
        "start_time": "09:00",
        "end_time": "11:00",
        "matched_rule_ids": ["rule-a", "rule-z"],
        "payload": {
            "court_name": "Aコート",
            "reservation_url": "https://example.test/reserve",
        },
    }


def test_script_can_be_invoked_by_the_workflow_path() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/enqueue_email_notifications.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--availability" in result.stdout


def test_candidate_collection_order_is_deterministic() -> None:
    later = make_candidate(user_id="user-z", slot_id="slot-z")
    earlier = make_candidate(user_id="user-a", slot_id="slot-a")

    result = enqueue.build_enqueue_candidates([later, earlier])

    assert [candidate["user_id"] for candidate in result] == ["user-a", "user-z"]


def test_zero_candidates_skips_rpc() -> None:
    def unexpected_opener(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("RPC must not be called")

    assert enqueue.enqueue_candidates(
        "https://project.supabase.co",
        "service-role-key",
        [],
        opener=unexpected_opener,
    ) == {field: 0 for field in enqueue.RPC_RESPONSE_FIELDS}


@pytest.mark.parametrize("candidate_count", [1, 500])
def test_up_to_500_candidates_use_one_rpc(
    candidate_count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batches: list[int] = []

    def fake_batch(
        _url: str,
        _key: str,
        candidates: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, int]:
        batches.append(len(candidates))
        return aggregate(len(candidates))[0]

    monkeypatch.setattr(enqueue, "enqueue_candidate_batch", fake_batch)

    totals = enqueue.enqueue_candidates(
        "https://project.supabase.co",
        "service-role-key",
        [make_candidate(slot_id=f"slot-{index}") for index in range(candidate_count)],
    )

    assert batches == [candidate_count]
    assert totals["candidate_count"] == candidate_count


def test_more_than_500_candidates_use_bounded_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batches: list[int] = []

    def fake_batch(
        _url: str,
        _key: str,
        candidates: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> dict[str, int]:
        batches.append(len(candidates))
        return aggregate(len(candidates))[0]

    monkeypatch.setattr(enqueue, "enqueue_candidate_batch", fake_batch)

    totals = enqueue.enqueue_candidates(
        "https://project.supabase.co",
        "service-role-key",
        [make_candidate(slot_id=f"slot-{index}") for index in range(1001)],
    )

    assert batches == [500, 500, 1]
    assert totals["candidate_count"] == 1001


def test_rpc_request_uses_expected_url_method_body_and_scoped_headers() -> None:
    candidate = enqueue.build_enqueue_candidate(make_candidate())
    response = FakeResponse(aggregate(1))
    observed: dict[str, Any] = {}

    def opener(request: Any, timeout: int) -> FakeResponse:
        observed["request"] = request
        observed["timeout"] = timeout
        return response

    result = enqueue.enqueue_candidate_batch(
        "https://project.supabase.co/",
        "service-role-key",
        [candidate],
        opener=opener,
    )

    request = observed["request"]
    assert request.full_url == (
        "https://project.supabase.co"
        "/rest/v1/rpc/enqueue_email_notification_candidates"
    )
    assert request.method == "POST"
    assert json.loads(request.data) == {"p_candidates": [candidate]}
    normal_headers = {name.lower(): value for name, value in request.headers.items()}
    unredirected = {
        name.lower(): value for name, value in request.unredirected_hdrs.items()
    }
    assert set(normal_headers) == {"accept", "content-type"}
    assert unredirected == {
        "authorization": "Bearer service-role-key",
        "apikey": "service-role-key",
    }
    assert observed["timeout"] == enqueue.RPC_TIMEOUT_SECONDS
    assert response.closed
    assert result["candidate_count"] == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://project.supabase.co",
        "https://example.com",
        "https://project.supabase.co/other",
    ],
)
def test_rpc_rejects_malformed_supabase_url(url: str) -> None:
    with pytest.raises(enqueue.EmailNotificationEnqueueError):
        enqueue.enqueue_candidate_batch(url, "service-role-key", [{}])


def test_rpc_rejects_missing_service_role_key() -> None:
    with pytest.raises(enqueue.EmailNotificationEnqueueError):
        enqueue.enqueue_candidate_batch("https://project.supabase.co", "", [{}])


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(aggregate(1), status=500),
        FakeResponse(b"not-json", raw=True),
        FakeResponse({"candidate_count": 1}),
        FakeResponse(
            [
                {
                    **aggregate(1)[0],
                    "candidate_count": True,
                }
            ]
        ),
    ],
)
def test_rpc_rejects_http_and_malformed_responses(response: FakeResponse) -> None:
    with pytest.raises(enqueue.EmailNotificationEnqueueError):
        enqueue.enqueue_candidate_batch(
            "https://project.supabase.co",
            "service-role-key",
            [{}],
            opener=lambda *_args, **_kwargs: response,
        )


def test_rpc_rejects_oversized_response() -> None:
    response = FakeResponse(b"x" * (enqueue.MAX_RPC_RESPONSE_BYTES + 1), raw=True)

    with pytest.raises(enqueue.EmailNotificationEnqueueError):
        enqueue.enqueue_candidate_batch(
            "https://project.supabase.co",
            "service-role-key",
            [{}],
            opener=lambda *_args, **_kwargs: response,
        )


def test_rpc_redirect_error_is_safe_and_not_followed() -> None:
    secret = "service-role-sensitive"
    sensitive_slot = "sensitive-slot-id"

    def redirect(_request: Any, **_kwargs: Any) -> Any:
        raise urllib.error.HTTPError(
            "https://project.supabase.co/private",
            302,
            f"redirect contained {secret} and {sensitive_slot}",
            {},
            None,
        )

    with pytest.raises(enqueue.EmailNotificationEnqueueError) as error:
        enqueue.enqueue_candidate_batch(
            "https://project.supabase.co",
            secret,
            [enqueue.build_enqueue_candidate(make_candidate(slot_id=sensitive_slot))],
            opener=redirect,
        )

    assert secret not in str(error.value)
    assert sensitive_slot not in str(error.value)
    redirect_handlers = [
        handler
        for handler in matching._build_no_redirect_opener().handlers
        if isinstance(handler, matching.RejectRedirectHandler)
    ]
    assert len(redirect_handlers) == 1


def test_cli_prints_aggregate_counts_without_identifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    availability_path = tmp_path / "availability.json"
    availability_path.write_text("{}", encoding="utf-8")
    secret = "service-role-sensitive"
    user_id = "sensitive-user-id"
    rule_id = "sensitive-rule-id"
    slot_id = "sensitive-slot-id"
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", secret)
    monkeypatch.setattr(enqueue.matching, "fetch_notification_rules", lambda *_: [object()])
    monkeypatch.setattr(enqueue.matching, "load_availability_document", lambda *_: {})
    monkeypatch.setattr(enqueue.matching, "extract_available_slots", lambda *_: [{}])
    monkeypatch.setattr(
        enqueue.matching,
        "match_notification_rules",
        lambda *_: [
            make_candidate(
                user_id=user_id,
                slot_id=slot_id,
                matched_rules=[{"rule_id": rule_id}],
            )
        ],
    )
    monkeypatch.setattr(
        enqueue,
        "enqueue_candidates",
        lambda *_args, **_kwargs: aggregate(1)[0],
    )

    result = enqueue.main(["--availability", str(availability_path)])

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    assert captured.out.strip() == (
        "rules_evaluated=1 slots_evaluated=1 match_candidates=1 "
        "enqueue_candidates=1 inserted_delivery_items=1 "
        "inserted_messages=1 linked_items=1"
    )
    for sensitive in (secret, user_id, rule_id, slot_id):
        assert sensitive not in captured.out
        assert sensitive not in captured.err
