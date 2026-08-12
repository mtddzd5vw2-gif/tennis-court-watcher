from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
from typing import Any

import pytest

from scripts import dispatch_email_notifications as dispatch


def metrics(**overrides: Any) -> dict[str, Any]:
    value = {
        "claimed_count": 1,
        "accepted_count": 1,
        "retry_count": 0,
        "permanent_failure_count": 0,
        "cancelled_count": 0,
    }
    value.update(overrides)
    return value


class FakeResponse:
    def __init__(self, value: Any, *, status: int = 200, raw: bool = False) -> None:
        self.body = value if raw else json.dumps(value).encode("utf-8")
        self.status = status
        self.closed = False

    def read(self, _limit: int) -> bytes:
        return self.body

    def close(self) -> None:
        self.closed = True


def test_dispatch_request_has_exact_contract() -> None:
    response = FakeResponse(metrics())
    observed: dict[str, Any] = {}

    def opener(request: Any, timeout: int) -> FakeResponse:
        observed["request"] = request
        observed["timeout"] = timeout
        return response

    result = dispatch.dispatch_email_notifications(
        "https://project.supabase.co/",
        "worker-secret",
        opener=opener,
    )

    request = observed["request"]
    assert request.full_url == (
        "https://project.supabase.co/functions/v1/dispatch-email-notifications"
    )
    assert request.method == "POST"
    assert request.data == b'{"batch_size":10}'
    normal_headers = {name.lower(): value for name, value in request.headers.items()}
    unredirected = {
        name.lower(): value for name, value in request.unredirected_hdrs.items()
    }
    assert set(normal_headers) == {"accept", "content-type"}
    assert "origin" not in normal_headers
    assert "origin" not in unredirected
    assert unredirected == {"authorization": "Bearer worker-secret"}
    assert observed["timeout"] == dispatch.REQUEST_TIMEOUT_SECONDS
    assert response.closed
    assert result == metrics()


def test_script_can_be_invoked_by_the_workflow_path() -> None:
    environment = os.environ.copy()
    environment.pop("SUPABASE_URL", None)
    environment.pop("EMAIL_DELIVERY_WORKER_SECRET", None)

    result = subprocess.run(
        [sys.executable, "scripts/dispatch_email_notifications.py"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() == "email notification dispatch failed"


@pytest.mark.parametrize(
    "url",
    [
        "http://project.supabase.co",
        "https://example.com",
        "https://project.supabase.co/functions",
    ],
)
def test_dispatch_rejects_malformed_url(url: str) -> None:
    with pytest.raises(dispatch.EmailNotificationDispatchError):
        dispatch.dispatch_email_notifications(url, "worker-secret")


def test_dispatch_rejects_missing_secret() -> None:
    with pytest.raises(dispatch.EmailNotificationDispatchError):
        dispatch.dispatch_email_notifications("https://project.supabase.co", "")


@pytest.mark.parametrize("status", [201, 204, 301, 400, 500])
def test_dispatch_rejects_every_non_200_status(status: int) -> None:
    with pytest.raises(dispatch.EmailNotificationDispatchError):
        dispatch.dispatch_email_notifications(
            "https://project.supabase.co",
            "worker-secret",
            opener=lambda *_args, **_kwargs: FakeResponse(metrics(), status=status),
        )


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(b"not-json", raw=True),
        FakeResponse({"claimed_count": 0}),
        FakeResponse(metrics(extra_count=0)),
        FakeResponse(metrics(claimed_count=True)),
        FakeResponse(metrics(claimed_count=-1)),
        FakeResponse(metrics(claimed_count=1.5)),
    ],
)
def test_dispatch_rejects_malformed_or_unexpected_aggregate(
    response: FakeResponse,
) -> None:
    with pytest.raises(dispatch.EmailNotificationDispatchError):
        dispatch.dispatch_email_notifications(
            "https://project.supabase.co",
            "worker-secret",
            opener=lambda *_args, **_kwargs: response,
        )


def test_dispatch_rejects_oversized_response() -> None:
    response = FakeResponse(b"x" * (dispatch.MAX_RESPONSE_BYTES + 1), raw=True)

    with pytest.raises(dispatch.EmailNotificationDispatchError):
        dispatch.dispatch_email_notifications(
            "https://project.supabase.co",
            "worker-secret",
            opener=lambda *_args, **_kwargs: response,
        )


def test_dispatch_redirect_error_does_not_expose_secret() -> None:
    secret = "sensitive-worker-secret"

    def redirect(_request: Any, **_kwargs: Any) -> Any:
        raise urllib.error.HTTPError(
            "https://project.supabase.co/private",
            302,
            f"redirect contained {secret}",
            {},
            None,
        )

    with pytest.raises(dispatch.EmailNotificationDispatchError) as error:
        dispatch.dispatch_email_notifications(
            "https://project.supabase.co",
            secret,
            opener=redirect,
        )

    assert secret not in str(error.value)


def test_cli_prints_only_aggregate_counts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "sensitive-worker-secret"
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("EMAIL_DELIVERY_WORKER_SECRET", secret)
    monkeypatch.setattr(dispatch, "dispatch_email_notifications", lambda *_: metrics())

    result = dispatch.main()

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    assert captured.out.strip() == (
        "claimed_count=1 accepted_count=1 retry_count=0 "
        "permanent_failure_count=0 cancelled_count=0"
    )
    assert secret not in captured.out
    assert secret not in captured.err
