from __future__ import annotations

import json
import re
import urllib.error
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import yaml

from scripts import report_line_usage as reporting


ROOT = Path(__file__).parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/report-line-usage.yml"
JST = ZoneInfo("Asia/Tokyo")


class FakeResponse:
    def __init__(self, value: Any, *, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(value).encode("utf-8")

    def read(self, _limit: int) -> bytes:
        return self._body

    def close(self) -> None:
        return None


class SequentialOpener:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.requests: list[Any] = []

    def __call__(self, request: Any, *, timeout: int) -> FakeResponse:
        assert timeout > 0
        self.requests.append(request)
        return self.responses.pop(0)


def load_workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def workflow_triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    return workflow.get("on", workflow.get(True, {}))


def test_fetch_line_usage_uses_both_official_quota_endpoints() -> None:
    opener = SequentialOpener(
        FakeResponse({"type": "limited", "value": 200}),
        FakeResponse({"totalUsage": 73}),
    )

    usage = reporting.fetch_line_usage("secret-token", opener=opener)

    assert usage == reporting.LineUsage(total_usage=73, quota_limit=200)
    assert usage.remaining == 127
    assert [request.full_url for request in opener.requests] == [
        reporting.LINE_QUOTA_ENDPOINT,
        reporting.LINE_CONSUMPTION_ENDPOINT,
    ]
    for request in opener.requests:
        assert request.get_header("Authorization") == "Bearer secret-token"


def test_line_token_allows_only_outer_clipboard_whitespace() -> None:
    opener = SequentialOpener(
        FakeResponse({"type": "limited", "value": 200}),
        FakeResponse({"totalUsage": 19}),
    )

    reporting.fetch_line_usage("\r\n secret-token \r\n", opener=opener)

    assert all(
        request.get_header("Authorization") == "Bearer secret-token"
        for request in opener.requests
    )
    with pytest.raises(reporting.LineUsageReportError):
        reporting.fetch_line_usage("secret\ntoken", opener=opener)


@pytest.mark.parametrize(
    ("quota", "consumption"),
    [
        ({"type": "limited"}, {"totalUsage": 1}),
        ({"type": "limited", "value": True}, {"totalUsage": 1}),
        ({"type": "unknown", "value": 200}, {"totalUsage": 1}),
        ({"type": "limited", "value": 200}, {"totalUsage": -1}),
        ({"type": "limited", "value": 200}, {"totalUsage": True}),
    ],
)
def test_fetch_line_usage_rejects_invalid_provider_responses(
    quota: dict[str, Any],
    consumption: dict[str, Any],
) -> None:
    opener = SequentialOpener(FakeResponse(quota), FakeResponse(consumption))

    with pytest.raises(reporting.LineUsageReportError):
        reporting.fetch_line_usage("secret-token", opener=opener)


def test_decision_sends_weekly_and_only_one_threshold_warning() -> None:
    below = reporting.LineUsage(total_usage=179, quota_limit=200)
    reached = reporting.LineUsage(total_usage=180, quota_limit=200)

    assert reporting.decide_report(
        below,
        weekly_report=True,
        warning_already_sent=False,
        warning_threshold=180,
    ) == reporting.ReportDecision(should_send=True, warning_due=False)
    assert reporting.decide_report(
        reached,
        weekly_report=False,
        warning_already_sent=False,
        warning_threshold=180,
    ) == reporting.ReportDecision(should_send=True, warning_due=True)
    assert reporting.decide_report(
        reached,
        weekly_report=False,
        warning_already_sent=True,
        warning_threshold=180,
    ) == reporting.ReportDecision(should_send=False, warning_due=False)


def test_rendered_warning_explains_cap_fallback_and_manager_usage() -> None:
    rendered = reporting.render_report(
        reporting.LineUsage(total_usage=181, quota_limit=200),
        warning_threshold=180,
        checked_at=datetime(2026, 8, 22, 12, 7, tzinfo=JST),
        warning=True,
    )

    assert "要確認" in rendered.subject
    assert "181通" in rendered.subject
    assert "メール通知へ" in rendered.text
    assert "Official Account Manager" in rendered.text
    assert "自動変更はしません" in rendered.text
    assert "残り: 19通" in rendered.text
    assert "LINE user ID" not in rendered.text


def test_resend_report_uses_operational_recipient_and_idempotency() -> None:
    opener = SequentialOpener(FakeResponse({"id": "resend_message_1"}))
    checked_at = datetime(2026, 8, 22, 12, 7, tzinfo=JST)
    rendered = reporting.render_report(
        reporting.LineUsage(total_usage=42, quota_limit=200),
        warning_threshold=180,
        checked_at=checked_at,
        warning=False,
    )

    message_id = reporting.send_resend_report(
        "resend-secret",
        "operator@example.com",
        rendered,
        checked_at=checked_at,
        warning=False,
        warning_threshold=180,
        opener=opener,
    )

    assert message_id == "resend_message_1"
    request = opener.requests[0]
    assert request.full_url == reporting.RESEND_EMAIL_ENDPOINT
    assert request.get_header("Authorization") == "Bearer resend-secret"
    assert request.get_header("Idempotency-key") == (
        "tennis-court-watcher/line-usage-weekly/2026-08-22"
    )
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["to"] == ["operator@example.com"]
    assert payload["from"] == reporting.REPORT_FROM
    assert payload["tags"][0] == {
        "name": "tcw_source",
        "value": "line_usage_report",
    }


def test_manual_report_uses_run_specific_idempotency_suffix() -> None:
    opener = SequentialOpener(FakeResponse({"id": "resend_message_manual"}))

    reporting.send_resend_report(
        "resend-secret",
        "operator@example.com",
        reporting.RenderedReport("subject", "text", "<p>text</p>"),
        checked_at=datetime(2026, 8, 22, 12, 7, tzinfo=JST),
        warning=False,
        warning_threshold=180,
        idempotency_suffix=" 32456481413 ",
        opener=opener,
    )

    assert opener.requests[0].get_header("Idempotency-key") == (
        "tennis-court-watcher/line-usage-weekly/2026-08-22/32456481413"
    )
    with pytest.raises(reporting.LineUsageReportError):
        reporting.send_resend_report(
            "resend-secret",
            "operator@example.com",
            reporting.RenderedReport("subject", "text", "<p>text</p>"),
            checked_at=datetime(2026, 8, 22, 12, 7, tzinfo=JST),
            warning=False,
            warning_threshold=180,
            idempotency_suffix="invalid/suffix",
            opener=opener,
        )


def test_resend_key_allows_only_outer_clipboard_whitespace() -> None:
    opener = SequentialOpener(FakeResponse({"id": "resend_message_2"}))

    reporting.send_resend_report(
        "\r\n resend-secret \r\n",
        "\r\n operator@example.com \r\n",
        reporting.RenderedReport("subject", "text", "<p>text</p>"),
        checked_at=datetime(2026, 8, 22, 12, 7, tzinfo=JST),
        warning=False,
        warning_threshold=180,
        opener=opener,
    )

    assert opener.requests[0].get_header("Authorization") == "Bearer resend-secret"
    payload = json.loads(opener.requests[0].data.decode("utf-8"))
    assert payload["to"] == ["operator@example.com"]
    with pytest.raises(reporting.LineUsageReportError):
        reporting.send_resend_report(
            "resend\nsecret",
            "operator@example.com",
            reporting.RenderedReport("subject", "text", "<p>text</p>"),
            checked_at=datetime(2026, 8, 22, 12, 7, tzinfo=JST),
            warning=False,
            warning_threshold=180,
            opener=opener,
        )


def test_http_error_logs_only_safe_status_and_provider_code() -> None:
    provider_error = urllib.error.HTTPError(
        reporting.RESEND_EMAIL_ENDPOINT,
        403,
        "Forbidden",
        {},
        BytesIO(
            json.dumps(
                {
                    "name": "invalid_api_key",
                    "message": "sensitive provider details",
                }
            ).encode("utf-8")
        ),
    )

    def failing_opener(_request: Any, *, timeout: int) -> Any:
        assert timeout > 0
        raise provider_error

    with pytest.raises(reporting.LineUsageReportError) as captured:
        reporting.send_resend_report(
            "resend-secret",
            "operator@example.com",
            reporting.RenderedReport("subject", "text", "<p>text</p>"),
            checked_at=datetime(2026, 8, 22, 12, 7, tzinfo=JST),
            warning=False,
            warning_threshold=180,
            opener=failing_opener,
        )

    assert str(captured.value) == (
        "Resend report request failed (status=403, code=invalid_api_key)"
    )
    assert "sensitive provider details" not in str(captured.value)


@pytest.mark.parametrize(
    "recipient",
    [
        "",
        "Name <operator@example.com>",
        "operator@example.com\nBcc: attacker@example.com",
        "not-an-email",
    ],
)
def test_resend_report_rejects_invalid_recipient(recipient: str) -> None:
    rendered = reporting.RenderedReport("subject", "text", "<p>text</p>")
    with pytest.raises(reporting.LineUsageReportError):
        reporting.send_resend_report(
            "resend-secret",
            recipient,
            rendered,
            checked_at=datetime(2026, 8, 22, 12, 7, tzinfo=JST),
            warning=False,
            warning_threshold=180,
            opener=SequentialOpener(FakeResponse({"id": "unused"})),
        )


def test_warning_marker_contains_only_the_billing_month(tmp_path: Path) -> None:
    marker = tmp_path / "state" / "warning"
    reporting._write_warning_marker(
        str(marker),
        datetime(2026, 8, 22, 12, 7, tzinfo=JST),
    )

    assert marker.read_text(encoding="utf-8") == "2026-08\n"


def test_workflow_checks_daily_reports_saturday_and_keeps_secrets_out() -> None:
    workflow = load_workflow()
    assert workflow_triggers(workflow)["schedule"] == [{"cron": "7 3 * * *"}]
    assert workflow["concurrency"] == {
        "group": "line-usage-report",
        "cancel-in-progress": False,
    }
    job = workflow["jobs"]["report"]
    assert "vars.ENABLE_LINE_USAGE_REPORTS == 'true'" in job["if"]

    steps = {step["name"]: step for step in job["steps"]}
    mode = steps["Determine report period and mode"]["run"]
    assert "TZ=Asia/Tokyo date +%u" in mode
    assert '== "6"' in mode

    report = steps["Query usage and send report when required"]
    assert report["env"] == {
        "LINE_CHANNEL_ACCESS_TOKEN": "${{ secrets.LINE_CHANNEL_ACCESS_TOKEN }}",
        "LINE_USAGE_REPORT_RESEND_API_KEY": (
            "${{ secrets.LINE_USAGE_REPORT_RESEND_API_KEY }}"
        ),
        "LINE_USAGE_REPORT_TO": "${{ secrets.LINE_USAGE_REPORT_TO }}",
        "LINE_USAGE_WARNING_THRESHOLD": (
            "${{ vars.LINE_USAGE_WARNING_THRESHOLD || '180' }}"
        ),
        "WEEKLY_REPORT": "${{ steps.report-mode.outputs.weekly }}",
        "WARNING_ALREADY_SENT": (
            "${{ steps.warning-marker.outputs.cache-hit || false }}"
        ),
        "IDEMPOTENCY_SUFFIX": (
            "${{ github.event_name == 'workflow_dispatch' && github.run_id || '' }}"
        ),
    }
    assert "--idempotency-suffix" in report["run"]
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "mie.masa" not in workflow_text
    assert "actions/cache/restore@0057852" in workflow_text
    assert "actions/cache/save@0057852" in workflow_text
    external_actions = [
        step["uses"]
        for step in job["steps"]
        if "uses" in step and not step["uses"].startswith("./")
    ]
    assert external_actions
    assert all(
        re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", action)
        for action in external_actions
    )
