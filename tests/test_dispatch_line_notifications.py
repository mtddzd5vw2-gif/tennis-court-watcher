from __future__ import annotations

import json
from typing import Any

import pytest

from scripts import dispatch_line_notifications as dispatch


def metrics(**overrides: Any) -> dict[str, Any]:
    value = {
        "claimed_count": 1,
        "accepted_count": 1,
        "retry_count": 0,
        "permanent_failure_count": 0,
        "cancelled_count": 0,
        "quota_consumption": 17,
        "quota_limit": 180,
        "quota_exhausted": False,
    }
    value.update(overrides)
    return value


class FakeResponse:
    def __init__(self, value: Any, status: int = 200) -> None:
        self.body = json.dumps(value).encode("utf-8")
        self.status = status
        self.closed = False

    def read(self, _limit: int) -> bytes:
        return self.body

    def close(self) -> None:
        self.closed = True


def test_dispatch_request_has_worker_only_contract() -> None:
    observed: dict[str, Any] = {}
    response = FakeResponse(metrics())

    def opener(request: Any, timeout: int) -> FakeResponse:
        observed["request"] = request
        observed["timeout"] = timeout
        return response

    result = dispatch.dispatch_line_notifications(
        "https://abcdefghijklmnopqrst.supabase.co",
        "w" * 32,
        opener=opener,
    )
    request = observed["request"]
    assert request.full_url.endswith("/functions/v1/dispatch-line-notifications")
    assert request.data == b'{"batch_size":10}'
    assert "apikey" not in {
        key.lower() for key in request.unredirected_hdrs
    }
    assert request.unredirected_hdrs["Authorization"] == f"Bearer {'w' * 32}"
    assert result == metrics()
    assert response.closed


@pytest.mark.parametrize(
    "value",
    [
        {"claimed_count": 0},
        metrics(quota_limit=181),
        metrics(quota_exhausted=True),
        metrics(accepted_count=2),
        metrics(quota_consumption=True),
    ],
)
def test_dispatch_rejects_malformed_metrics(value: Any) -> None:
    with pytest.raises(dispatch.LineNotificationDispatchError):
        dispatch.dispatch_line_notifications(
            "https://abcdefghijklmnopqrst.supabase.co",
            "w" * 32,
            opener=lambda *_args, **_kwargs: FakeResponse(value),
        )


def test_dispatch_requires_high_entropy_length_secret() -> None:
    with pytest.raises(dispatch.LineNotificationDispatchError):
        dispatch.dispatch_line_notifications(
            "https://abcdefghijklmnopqrst.supabase.co",
            "short",
        )
