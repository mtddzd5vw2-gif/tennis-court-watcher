from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo


LINE_QUOTA_ENDPOINT = "https://api.line.me/v2/bot/message/quota"
LINE_CONSUMPTION_ENDPOINT = (
    "https://api.line.me/v2/bot/message/quota/consumption"
)
RESEND_EMAIL_ENDPOINT = "https://api.resend.com/emails"
REPORT_FROM = "Tennis Court Watcher <no-reply@email.tenniscourtwatcher.com>"
REQUEST_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 64 * 1024
JST = ZoneInfo("Asia/Tokyo")
EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
PROVIDER_ERROR_CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


class LineUsageReportError(RuntimeError):
    """Raised when LINE usage cannot be reported without leaking secrets."""


@dataclass(frozen=True)
class LineUsage:
    total_usage: int
    quota_limit: int | None

    @property
    def remaining(self) -> int | None:
        if self.quota_limit is None:
            return None
        return max(0, self.quota_limit - self.total_usage)


@dataclass(frozen=True)
class ReportDecision:
    should_send: bool
    warning_due: bool


@dataclass(frozen=True)
class RenderedReport:
    subject: str
    text: str
    html: str


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _open_without_redirects(request: Any, *, timeout: int) -> Any:
    return urllib.request.build_opener(_NoRedirectHandler).open(
        request,
        timeout=timeout,
    )


def _positive_timeout(value: int | float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise LineUsageReportError("request timeout must be positive")


def _single_line_secret(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise LineUsageReportError(f"{name} is required")
    normalized = value.strip()
    if not normalized or "\r" in normalized or "\n" in normalized:
        raise LineUsageReportError(f"{name} is required")
    return normalized


def _http_error_summary(operation: str, error: urllib.error.HTTPError) -> str:
    status = error.code if isinstance(error.code, int) else "unknown"
    provider_code = "unknown"
    try:
        response_body = error.read(MAX_RESPONSE_BYTES + 1)
        if (
            isinstance(response_body, bytes)
            and len(response_body) <= MAX_RESPONSE_BYTES
        ):
            value = json.loads(response_body.decode("utf-8"))
            if isinstance(value, Mapping):
                candidate = value.get("name", value.get("code"))
                if isinstance(candidate, str) and PROVIDER_ERROR_CODE_PATTERN.fullmatch(
                    candidate
                ):
                    provider_code = candidate
    except Exception:
        pass
    finally:
        try:
            error.close()
        except Exception:
            pass
    return f"{operation} request failed (status={status}, code={provider_code})"


def _read_json_response(
    request: urllib.request.Request,
    *,
    timeout: int,
    opener: Callable[..., Any] | None,
    operation: str,
) -> Mapping[str, Any]:
    _positive_timeout(timeout)
    open_request = opener if opener is not None else _open_without_redirects
    response: Any = None
    try:
        response = open_request(request, timeout=timeout)
        status = getattr(response, "status", 200)
        if not isinstance(status, int) or status < 200 or status >= 300:
            raise LineUsageReportError(f"{operation} request failed")
        response_body = response.read(MAX_RESPONSE_BYTES + 1)
    except LineUsageReportError:
        raise
    except urllib.error.HTTPError as error:
        raise LineUsageReportError(_http_error_summary(operation, error)) from None
    except Exception:
        raise LineUsageReportError(f"{operation} request failed") from None
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    if not isinstance(response_body, bytes):
        raise LineUsageReportError(f"{operation} response is not valid JSON")
    if len(response_body) > MAX_RESPONSE_BYTES:
        raise LineUsageReportError(f"{operation} response is too large")
    try:
        value = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LineUsageReportError(
            f"{operation} response is not valid JSON"
        ) from None
    if not isinstance(value, Mapping):
        raise LineUsageReportError(f"{operation} response has an invalid format")
    return value


def _line_request(endpoint: str, channel_access_token: str) -> urllib.request.Request:
    token = _single_line_secret(
        channel_access_token,
        "LINE_CHANNEL_ACCESS_TOKEN",
    )
    request = urllib.request.Request(
        endpoint,
        headers={"Accept": "application/json"},
        method="GET",
    )
    request.add_unredirected_header(
        "Authorization",
        f"Bearer {token}",
    )
    return request


def fetch_line_usage(
    channel_access_token: str,
    *,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> LineUsage:
    quota = _read_json_response(
        _line_request(LINE_QUOTA_ENDPOINT, channel_access_token),
        timeout=timeout,
        opener=opener,
        operation="LINE quota",
    )
    consumption = _read_json_response(
        _line_request(LINE_CONSUMPTION_ENDPOINT, channel_access_token),
        timeout=timeout,
        opener=opener,
        operation="LINE consumption",
    )

    quota_type = quota.get("type")
    if quota_type == "limited":
        quota_limit = quota.get("value")
        if (
            isinstance(quota_limit, bool)
            or not isinstance(quota_limit, int)
            or quota_limit < 1
        ):
            raise LineUsageReportError("LINE quota response has an invalid format")
    elif quota_type == "none":
        quota_limit = None
    else:
        raise LineUsageReportError("LINE quota response has an invalid format")

    total_usage = consumption.get("totalUsage")
    if (
        isinstance(total_usage, bool)
        or not isinstance(total_usage, int)
        or total_usage < 0
    ):
        raise LineUsageReportError(
            "LINE consumption response has an invalid format"
        )
    return LineUsage(total_usage=total_usage, quota_limit=quota_limit)


def parse_warning_threshold(value: str) -> int:
    try:
        threshold = int(value)
    except (TypeError, ValueError):
        raise LineUsageReportError(
            "LINE_USAGE_WARNING_THRESHOLD must be an integer"
        ) from None
    if threshold < 1 or threshold > 1_000_000_000:
        raise LineUsageReportError(
            "LINE_USAGE_WARNING_THRESHOLD is out of range"
        )
    return threshold


def decide_report(
    usage: LineUsage,
    *,
    weekly_report: bool,
    warning_already_sent: bool,
    warning_threshold: int,
) -> ReportDecision:
    if not isinstance(weekly_report, bool) or not isinstance(
        warning_already_sent,
        bool,
    ):
        raise LineUsageReportError("report mode is invalid")
    if warning_threshold < 1:
        raise LineUsageReportError("warning threshold must be positive")
    warning_due = (
        usage.total_usage >= warning_threshold and not warning_already_sent
    )
    return ReportDecision(
        should_send=weekly_report or warning_due,
        warning_due=warning_due,
    )


def _usage_lines(
    usage: LineUsage,
    warning_threshold: int,
    checked_at: datetime,
) -> list[str]:
    limit = "上限設定なし" if usage.quota_limit is None else f"{usage.quota_limit}通"
    remaining = (
        "算出対象外" if usage.remaining is None else f"{usage.remaining}通"
    )
    return [
        f"確認日時: {checked_at.astimezone(JST):%Y-%m-%d %H:%M} JST",
        f"今月の使用量: {usage.total_usage}通",
        f"今月の上限: {limit}",
        f"残り: {remaining}",
        f"運用警告値: {warning_threshold}通",
    ]


def render_report(
    usage: LineUsage,
    *,
    warning_threshold: int,
    checked_at: datetime,
    warning: bool,
) -> RenderedReport:
    if checked_at.tzinfo is None:
        raise LineUsageReportError("checked_at must be timezone-aware")
    if warning:
        subject = (
            "【要確認】LINE月間使用量が運用上限に到達しました"
            f"（{usage.total_usage}通）"
        )
        introduction = (
            "LINEの月間使用量が運用警告値に到達しました。"
            "自動Pushは180通を上限とし、以後はメール通知へ"
            "フォールバックしてください。"
        )
    else:
        quota = "上限なし" if usage.quota_limit is None else str(usage.quota_limit)
        subject = (
            "【週次報告】LINE月間使用量 "
            f"{usage.total_usage}/{quota}通"
        )
        introduction = "LINE公式アカウントの月間使用状況をお知らせします。"

    lines = _usage_lines(usage, warning_threshold, checked_at)
    text = "\n".join(
        [
            introduction,
            "",
            *lines,
            "",
            "この使用量にはLINE Official Account Managerからの配信も含まれます。",
            "無料プランから有料プランへ自動変更はしません。",
        ]
    )
    html_lines = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    rendered_html = "".join(
        [
            '<!doctype html><html lang="ja"><body>',
            f"<p>{html.escape(introduction)}</p>",
            f"<ul>{html_lines}</ul>",
            "<p>この使用量にはLINE Official Account Managerからの配信も含まれます。</p>",
            "<p>無料プランから有料プランへ自動変更はしません。</p>",
            "</body></html>",
        ]
    )
    return RenderedReport(subject=subject, text=text, html=rendered_html)


def _validated_recipient(value: str) -> str:
    if not isinstance(value, str):
        raise LineUsageReportError("LINE_USAGE_REPORT_TO is invalid")
    normalized = value.strip()
    if not normalized or "\r" in normalized or "\n" in normalized:
        raise LineUsageReportError("LINE_USAGE_REPORT_TO is invalid")
    display_name, address = parseaddr(normalized)
    if display_name or address != normalized or not EMAIL_PATTERN.fullmatch(address):
        raise LineUsageReportError("LINE_USAGE_REPORT_TO is invalid")
    return address


def _validated_idempotency_suffix(value: str) -> str:
    if not isinstance(value, str):
        raise LineUsageReportError("idempotency suffix is invalid")
    normalized = value.strip()
    if normalized and not PROVIDER_ERROR_CODE_PATTERN.fullmatch(normalized):
        raise LineUsageReportError("idempotency suffix is invalid")
    return normalized


def send_resend_report(
    resend_api_key: str,
    recipient: str,
    rendered: RenderedReport,
    *,
    checked_at: datetime,
    warning: bool,
    warning_threshold: int,
    idempotency_suffix: str = "",
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    opener: Callable[..., Any] | None = None,
) -> str:
    api_key = _single_line_secret(
        resend_api_key,
        "LINE_USAGE_REPORT_RESEND_API_KEY",
    )
    destination = _validated_recipient(recipient)
    jst_time = checked_at.astimezone(JST)
    idempotency_key = (
        f"tennis-court-watcher/line-usage-warning/{jst_time:%Y-%m}/"
        f"{warning_threshold}"
        if warning
        else f"tennis-court-watcher/line-usage-weekly/{jst_time:%Y-%m-%d}"
    )
    suffix = _validated_idempotency_suffix(idempotency_suffix)
    if suffix:
        idempotency_key = f"{idempotency_key}/{suffix}"
    payload = {
        "from": REPORT_FROM,
        "to": [destination],
        "subject": rendered.subject,
        "text": rendered.text,
        "html": rendered.html,
        "tags": [
            {"name": "tcw_source", "value": "line_usage_report"},
            {"name": "tcw_period", "value": f"{jst_time:%Y_%m}"},
        ],
    }
    request = urllib.request.Request(
        RESEND_EMAIL_ENDPOINT,
        data=json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        },
        method="POST",
    )
    request.add_unredirected_header(
        "Authorization",
        f"Bearer {api_key}",
    )
    response = _read_json_response(
        request,
        timeout=timeout,
        opener=opener,
        operation="Resend report",
    )
    message_id = response.get("id")
    if not isinstance(message_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,255}",
        message_id,
    ):
        raise LineUsageReportError("Resend report response has an invalid format")
    return message_id


def _write_warning_marker(path: str, checked_at: datetime) -> None:
    if not path:
        return
    marker = Path(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{checked_at.astimezone(JST):%Y-%m}\n", encoding="utf-8")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report aggregate LINE Official Account monthly usage.",
    )
    parser.add_argument("--weekly", action="store_true")
    parser.add_argument("--warning-already-sent", action="store_true")
    parser.add_argument("--warning-marker", default="")
    parser.add_argument("--idempotency-suffix", default="")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        threshold = parse_warning_threshold(
            os.environ.get("LINE_USAGE_WARNING_THRESHOLD", "180")
        )
        usage = fetch_line_usage(
            os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""),
        )
        decision = decide_report(
            usage,
            weekly_report=args.weekly,
            warning_already_sent=args.warning_already_sent,
            warning_threshold=threshold,
        )
        checked_at = datetime.now(tz=JST)
        report_sent = False
        if decision.should_send and not args.dry_run:
            rendered = render_report(
                usage,
                warning_threshold=threshold,
                checked_at=checked_at,
                warning=decision.warning_due,
            )
            send_resend_report(
                os.environ.get("LINE_USAGE_REPORT_RESEND_API_KEY", ""),
                os.environ.get("LINE_USAGE_REPORT_TO", ""),
                rendered,
                checked_at=checked_at,
                warning=decision.warning_due,
                warning_threshold=threshold,
                idempotency_suffix=args.idempotency_suffix,
            )
            report_sent = True
            if decision.warning_due:
                _write_warning_marker(args.warning_marker, checked_at)
    except LineUsageReportError as error:
        print(f"LINE usage reporting failed: {error}", file=sys.stderr)
        return 1

    quota = "none" if usage.quota_limit is None else str(usage.quota_limit)
    print(
        " ".join(
            [
                f"line_usage={usage.total_usage}",
                f"line_quota={quota}",
                f"warning_due={str(decision.warning_due).lower()}",
                f"report_sent={str(report_sent).lower()}",
                f"dry_run={str(args.dry_run).lower()}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
