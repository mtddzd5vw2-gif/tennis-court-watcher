from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from scripts import enqueue_line_notifications as enqueue


USER_ID = "10000000-0000-4000-8000-000000000001"


def candidate() -> dict[str, Any]:
    return {
        "user_id": USER_ID,
        "slot_id": "kamoike-2026-08-22-09-11-a",
        "facility_id": "kamoike-prefectural",
        "facility_name": "鴨池県営テニスコート",
        "date": "2026-08-22",
        "start_time": "09:00",
        "end_time": "11:00",
        "court_name": "Aコート",
        "reservation_url": "https://example.jp/reserve",
        "matched_rules": [
            {"rule_id": "20000000-0000-4000-8000-000000000001"}
        ],
    }


def aggregate(*, shadow: bool) -> list[dict[str, Any]]:
    return [
        {
            "candidate_count": 1,
            "eligible_candidate_count": 1,
            "inserted_delivery_item_count": 0 if shadow else 1,
            "inserted_message_count": 0 if shadow else 1,
            "linked_item_count": 0 if shadow else 1,
            "shadow_mode": shadow,
        }
    ]


class FakeResponse:
    def __init__(self, value: Any, status: int = 200) -> None:
        self.body = json.dumps(value).encode("utf-8")
        self.status = status
        self.closed = False

    def read(self, _limit: int) -> bytes:
        return self.body

    def close(self) -> None:
        self.closed = True


def test_candidate_reuses_matching_contract_but_selects_line_channel() -> None:
    built = enqueue.build_enqueue_candidate(candidate())
    assert built["channel"] == "line"
    assert built["user_id"] == USER_ID
    assert set(built["payload"]) == {"court_name", "reservation_url"}


def test_shadow_request_is_no_write_and_canary_optional() -> None:
    response = FakeResponse(aggregate(shadow=True))
    observed: dict[str, Any] = {}

    def opener(request: Any, timeout: int) -> FakeResponse:
        observed["request"] = request
        observed["timeout"] = timeout
        return response

    result = enqueue.enqueue_candidate_batch(
        "https://abcdefghijklmnopqrst.supabase.co",
        "service-role-secret",
        [enqueue.build_enqueue_candidate(candidate())],
        shadow_mode=True,
        canary_user_id=None,
        use_allowlist=False,
        allow_all=False,
        opener=opener,
    )
    body = json.loads(observed["request"].data)
    assert body["p_shadow_mode"] is True
    assert body["p_canary_user_id"] is None
    assert body["p_use_allowlist"] is False
    assert body["p_allow_all"] is False
    assert result["inserted_delivery_item_count"] == 0
    assert response.closed


def test_live_enqueue_fails_closed_without_a_rollout_scope() -> None:
    with pytest.raises(enqueue.LineNotificationEnqueueError):
        enqueue.enqueue_candidates(
            "https://abcdefghijklmnopqrst.supabase.co",
            "service-role-secret",
            [],
            shadow_mode=False,
            canary_user_id=None,
            use_allowlist=False,
            allow_all=False,
        )


def test_live_allowlist_is_an_explicit_rollout_scope() -> None:
    result = enqueue.enqueue_candidate_batch(
        "https://abcdefghijklmnopqrst.supabase.co",
        "service-role-secret",
        [enqueue.build_enqueue_candidate(candidate())],
        shadow_mode=False,
        canary_user_id=None,
        use_allowlist=True,
        allow_all=False,
        opener=lambda *_args, **_kwargs: FakeResponse(aggregate(shadow=False)),
    )

    assert result["inserted_message_count"] == 1


def test_rollout_scopes_are_mutually_exclusive() -> None:
    with pytest.raises(enqueue.LineNotificationEnqueueError):
        enqueue.enqueue_candidates(
            "https://abcdefghijklmnopqrst.supabase.co",
            "service-role-secret",
            [],
            shadow_mode=False,
            canary_user_id=USER_ID,
            use_allowlist=True,
            allow_all=False,
        )


def test_live_canary_request_never_logs_or_redirects_sensitive_values() -> None:
    secret = "service-role-sensitive"

    def redirect(_request: Any, **_kwargs: Any) -> Any:
        raise urllib.error.HTTPError(
            "https://example.test/private",
            302,
            f"redirect contained {secret} and {USER_ID}",
            {},
            None,
        )

    with pytest.raises(enqueue.LineNotificationEnqueueError) as error:
        enqueue.enqueue_candidate_batch(
            "https://abcdefghijklmnopqrst.supabase.co",
            secret,
            [enqueue.build_enqueue_candidate(candidate())],
            shadow_mode=False,
            canary_user_id=USER_ID,
            use_allowlist=False,
            allow_all=False,
            opener=redirect,
        )
    assert secret not in str(error.value)
    assert USER_ID not in str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        aggregate(shadow=True)[0],
        [{**aggregate(shadow=True)[0], "shadow_mode": False}],
        [{**aggregate(shadow=True)[0], "eligible_candidate_count": 2}],
    ],
)
def test_response_contract_rejects_wrong_shape_mode_or_counts(value: Any) -> None:
    with pytest.raises(enqueue.LineNotificationEnqueueError):
        enqueue.enqueue_candidate_batch(
            "https://abcdefghijklmnopqrst.supabase.co",
            "service-role-secret",
            [enqueue.build_enqueue_candidate(candidate())],
            shadow_mode=True,
            canary_user_id=None,
            use_allowlist=False,
            allow_all=False,
            opener=lambda *_args, **_kwargs: FakeResponse(value),
        )
