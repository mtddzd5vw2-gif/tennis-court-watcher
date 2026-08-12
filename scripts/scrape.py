from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

import jpholiday
from bs4 import BeautifulSoup, Tag


JST = timezone(timedelta(hours=9), name="JST")
WINDOW_DAYS = 15
MONITOR_START = time(8, 0)
MONITOR_END = time(13, 0)
MINIMUM_DURATION_MINUTES = 60
DATA_PATH = Path("data/availability.json")
RUN_OUTPUT_DIRECTORY = Path("run-output")
SNAPSHOT_ROOT = Path("snapshots")

KAMOIKE_FACILITY_ID = "kamoike-prefectural"
KAMOIKE_FACILITY_NAME = "鴨池県営テニスコート"
KAMOIKE_URL_TEMPLATE = (
    "https://v2.spm-cloud.com/user/kamoike-undo/reserves/daily"
    "?date={date}&category_id=483&area_id=289"
)

SUMIZEI_FACILITY_ID = "sumizei"
SUMIZEI_FACILITY_NAME = "SuMIzeiテニスコート"
P_KASHIKAN_BASE_URL = "https://k2.p-kashikan.jp/kagoshima-city/index.php"
SUMIZEI_BASE_URL = P_KASHIKAN_BASE_URL
SUMIZEI_FACILITY_CODE = "029"

TOUKAI_FACILITY_ID = "toukai-tennis"
TOUKAI_FACILITY_NAME = "東開庭球場"
TOUKAI_FACILITY_CODE = "131"

WIDTH_PATTERN = re.compile(r"width\s*:\s*([\d.]+)%", re.IGNORECASE)
STATE_CLASS_PATTERN = re.compile(r"rsv--result--(yes|no|out)")
PIXEL_WIDTH_PATTERN = re.compile(r"width\s*:\s*([\d.]+)px", re.IGNORECASE)
P_KASHIKAN_SLOT_PATTERN = re.compile(
    r"setAppStatus\(\s*'(?P<resource>[^']+)'\s*,\s*"
    r"'(?P<date>\d{4}/\d{2}/\d{2})'\s*,\s*\d+\s*,\s*"
    r"'(?P<times>\d{8})'"
)
P_KASHIKAN_ACCEPT_LANGUAGE = "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"
P_KASHIKAN_VIEWPORT = {"width": 1440, "height": 1000}
P_KASHIKAN_DIAGNOSTIC_MARKERS = {
    "Access denied": re.compile(r"access\s+denied", re.IGNORECASE),
    "Forbidden": re.compile(r"\bforbidden\b", re.IGNORECASE),
    "Cloudflare": re.compile(r"\bcloudflare\b", re.IGNORECASE),
    "Akamai": re.compile(r"\bakamai\b", re.IGNORECASE),
    "Imperva": re.compile(r"\bimperva\b", re.IGNORECASE),
    "Incapsula": re.compile(r"\bincapsula\b", re.IGNORECASE),
    "Bot": re.compile(r"\bbot\b", re.IGNORECASE),
    "Request ID": re.compile(r"\brequest[-_ ]?id\b", re.IGNORECASE),
    "IP restriction": re.compile(r"\bip[-_ ]?restriction\b", re.IGNORECASE),
    "rate limit": re.compile(r"\brate[-_ ]?limit", re.IGNORECASE),
}
SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
}
COOKIE_ATTRIBUTE_NAMES = {
    "domain",
    "expires",
    "max-age",
    "path",
    "samesite",
}
ACCESS_DENIED_MARKERS = (
    "access denied",
    "forbidden",
    "too many requests",
    "アクセスが拒否",
)


def _cookie_names_from_header(value: str) -> list[str]:
    names: set[str] = set()
    for part in value.split(";"):
        name, separator, _ = part.strip().partition("=")
        if separator and name and name.lower() not in COOKIE_ATTRIBUTE_NAMES:
            names.add(name)
    return sorted(names)


def _sanitize_headers(headers: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Return diagnostic-safe headers and cookie names without any secret values."""
    safe: dict[str, str] = {}
    cookie_names: set[str] = set()
    for raw_name, raw_value in headers.items():
        name = str(raw_name).lower()
        value = str(raw_value)
        if name in {"cookie", "set-cookie"}:
            cookie_names.update(_cookie_names_from_header(value))
            safe[name] = "<redacted>"
        elif (
            name in SENSITIVE_HEADER_NAMES
            or "token" in name
            or "secret" in name
            or name.endswith("-key")
        ):
            safe[name] = "<redacted>"
        else:
            safe[name] = value
    return dict(sorted(safe.items())), sorted(cookie_names)


def _p_kashikan_body_markers(html: str) -> list[str]:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return [
        label
        for label, pattern in P_KASHIKAN_DIAGNOSTIC_MARKERS.items()
        if pattern.search(text)
    ]


def _is_access_denied_response(status: int | None, html: str) -> bool:
    page_text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()
    return status in {401, 403, 429} or any(
        marker in page_text for marker in ACCESS_DENIED_MARKERS
    )


@dataclass(frozen=True)
class TargetDay:
    date: date
    day_type: str
    holiday_name: str | None


@dataclass(frozen=True)
class Facility:
    id: str
    name: str
    url_template: str
    selector_env: str
    scraper: Callable[["PageClient", "Facility", TargetDay], dict[str, Any]]
    requires_browser: bool = False
    p_kashikan_code: str | None = None


@dataclass(frozen=True)
class PageCapture:
    html: str
    checked_at: str
    response_status: int | None
    error_type: str | None = None
    error_message: str | None = None


class ScrapeStructureError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


class PageClient(Protocol):
    def capture_page(
        self,
        url: str,
        snapshot_directory: Path,
        snapshot_name: str,
    ) -> PageCapture: ...

    def extract_texts(self, url: str, selector: str) -> list[str]: ...

    def capture_p_kashikan_schedule(
        self,
        reservation_url: str,
        target_date: date,
        snapshot_directory: Path,
        facility_code: str,
        facility_name: str,
    ) -> PageCapture: ...


class PlaywrightClient:
    """Browser boundary shared by facility scrapers and replaceable in tests."""

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._p_kashikan_context: Any = None

    def __enter__(self) -> "PlaywrightClient":
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        return self

    def __exit__(self, *_: object) -> None:
        if self._p_kashikan_context is not None:
            self._p_kashikan_context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def _get_p_kashikan_context(self) -> Any:
        if self._browser is None or self._playwright is None:
            raise RuntimeError("PlaywrightClient must be used as a context manager")
        if self._p_kashikan_context is None:
            desktop_chrome = dict(self._playwright.devices["Desktop Chrome"])
            desktop_chrome.pop("default_browser_type", None)
            desktop_chrome.update(
                {
                    "locale": "ja-JP",
                    "timezone_id": "Asia/Tokyo",
                    "viewport": dict(P_KASHIKAN_VIEWPORT),
                    "java_script_enabled": True,
                    "extra_http_headers": {
                        "Accept-Language": P_KASHIKAN_ACCEPT_LANGUAGE,
                    },
                }
            )
            self._p_kashikan_context = self._browser.new_context(**desktop_chrome)
        return self._p_kashikan_context

    def _save_p_kashikan_diagnostics(
        self,
        page: Any,
        context: Any,
        response: Any,
        html: str,
        snapshot_directory: Path,
        snapshot_name: str,
    ) -> Path:
        try:
            response_headers_raw = response.all_headers() if response else {}
        except Exception:
            response_headers_raw = {}
        try:
            request_headers_raw = response.request.all_headers() if response else {}
        except Exception:
            request_headers_raw = {}
        response_headers, response_cookie_names = _sanitize_headers(
            response_headers_raw
        )
        request_headers, request_cookie_names = _sanitize_headers(
            request_headers_raw
        )
        try:
            context_cookie_names = sorted(
                {
                    str(cookie.get("name"))
                    for cookie in context.cookies()
                    if cookie.get("name")
                }
            )
        except Exception:
            context_cookie_names = []
        try:
            user_agent = page.evaluate("navigator.userAgent")
            webdriver = page.evaluate("navigator.webdriver")
        except Exception:
            user_agent = None
            webdriver = None
        try:
            page_title = page.title()
        except Exception:
            page_title = None
        diagnostic = {
            "schema_version": 1,
            "captured_at": datetime.now(JST).isoformat(timespec="seconds"),
            "execution_environment": (
                "github_actions"
                if os.getenv("GITHUB_ACTIONS", "").lower() == "true"
                else "local"
            ),
            "runtime": {
                "platform": platform.system(),
                "platform_release": platform.release(),
                "python_version": platform.python_version(),
                "runner_os": os.getenv("RUNNER_OS"),
                "runner_arch": os.getenv("RUNNER_ARCH"),
                "runner_environment": os.getenv("RUNNER_ENVIRONMENT"),
                "image_os": os.getenv("ImageOS"),
                "image_version": os.getenv("ImageVersion"),
                "github_repository": os.getenv("GITHUB_REPOSITORY"),
                "github_run_id": os.getenv("GITHUB_RUN_ID"),
                "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
                "github_sha": os.getenv("GITHUB_SHA"),
                "browser_version": (
                    self._browser.version if self._browser is not None else None
                ),
            },
            "request": {
                "url": response.request.url if response else None,
                "headers": request_headers,
            },
            "response": {
                "final_url": page.url,
                "status": response.status if response else None,
                "title": page_title,
                "headers": response_headers,
                "body_length": len(html.encode("utf-8")),
                "body_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
                "matched_restriction_markers": _p_kashikan_body_markers(html),
            },
            "browser": {
                "user_agent": user_agent,
                "navigator_webdriver": webdriver,
                "locale": "ja-JP",
                "timezone": "Asia/Tokyo",
                "accept_language": P_KASHIKAN_ACCEPT_LANGUAGE,
                "viewport": dict(P_KASHIKAN_VIEWPORT),
                "javascript_enabled": True,
            },
            "cookie_names": sorted(
                set(context_cookie_names)
                | set(response_cookie_names)
                | set(request_cookie_names)
            ),
        }
        diagnostic_path = snapshot_directory / f"{snapshot_name}-diagnostics.json"
        diagnostic_path.write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            "p_kashikan_diagnostic="
            + json.dumps(
                {
                    "environment": diagnostic["execution_environment"],
                    "status": diagnostic["response"]["status"],
                    "final_url": diagnostic["response"]["final_url"],
                    "title": diagnostic["response"]["title"],
                    "user_agent": user_agent,
                    "navigator_webdriver": webdriver,
                    "cookie_names": diagnostic["cookie_names"],
                    "matched_restriction_markers": diagnostic["response"][
                        "matched_restriction_markers"
                    ],
                    "file": str(diagnostic_path),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return diagnostic_path

    def capture_page(
        self,
        url: str,
        snapshot_directory: Path,
        snapshot_name: str,
    ) -> PageCapture:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        if self._browser is None:
            raise RuntimeError("PlaywrightClient must be used as a context manager")

        snapshot_directory.mkdir(parents=True, exist_ok=True)
        html_path = snapshot_directory / f"{snapshot_name}.html"
        image_path = snapshot_directory / f"{snapshot_name}.png"
        checked_at = datetime.now(JST).isoformat(timespec="seconds")
        response_status: int | None = None
        error_type: str | None = None
        error_message: str | None = None

        context = self._browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        try:
            try:
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                response_status = response.status if response else None
                try:
                    page.wait_for_load_state("networkidle", timeout=15_000)
                except PlaywrightTimeoutError:
                    pass
                try:
                    page.locator("#app").wait_for(state="attached", timeout=15_000)
                except PlaywrightTimeoutError:
                    pass
                try:
                    page.locator(".rsv__result[data-reserve]").wait_for(
                        state="attached",
                        timeout=20_000,
                    )
                except PlaywrightTimeoutError:
                    pass
            except PlaywrightTimeoutError as exc:
                error_type = "navigation_timeout"
                error_message = str(exc)
            except Exception as exc:
                error_type = "navigation_error"
                error_message = str(exc)

            html = ""
            for _ in range(3):
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=5_000)
                except PlaywrightTimeoutError:
                    pass
                try:
                    html = page.content()
                    break
                except Exception as exc:
                    error_message = (
                        f"{error_message}; content capture failed: {exc}"
                        if error_message
                        else f"content capture failed: {exc}"
                    )
                    page.wait_for_timeout(500)
            if not html:
                error_type = error_type or "navigation_error"
                html = (
                    "<!doctype html><html><body><h1>Capture failed</h1>"
                    f"<pre>{error_message or error_type}</pre></body></html>"
                )
            html_path.write_text(html, encoding="utf-8")
            for _ in range(3):
                try:
                    page.screenshot(path=str(image_path), full_page=True)
                    break
                except Exception as exc:
                    screenshot_error = f"screenshot failed: {exc}"
                    error_message = (
                        f"{error_message}; {screenshot_error}"
                        if error_message
                        else screenshot_error
                    )
                    page.wait_for_timeout(500)
            if not image_path.exists():
                error_type = error_type or "snapshot_error"
                error_message = error_message or "Screenshot could not be saved"

            if response_status in {401, 403, 429}:
                error_type = "access_denied"
                error_message = f"HTTP {response_status}"

            return PageCapture(
                html=html,
                checked_at=checked_at,
                response_status=response_status,
                error_type=error_type,
                error_message=error_message,
            )
        finally:
            context.close()

    def extract_texts(self, url: str, selector: str) -> list[str]:
        if self._browser is None:
            raise RuntimeError("PlaywrightClient must be used as a context manager")

        context = self._browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            return page.locator(selector).all_inner_texts()
        finally:
            context.close()

    @staticmethod
    def _save_page_snapshot(
        page: Any,
        snapshot_directory: Path,
        snapshot_name: str,
    ) -> tuple[str, str | None]:
        snapshot_directory.mkdir(parents=True, exist_ok=True)
        html_path = snapshot_directory / f"{snapshot_name}.html"
        image_path = snapshot_directory / f"{snapshot_name}.png"
        errors: list[str] = []
        try:
            html = page.content()
        except Exception as exc:
            html = (
                "<!doctype html><html><body><h1>Capture failed</h1>"
                f"<pre>{exc}</pre></body></html>"
            )
            errors.append(f"content capture failed: {exc}")
        html_path.write_text(html, encoding="utf-8")
        try:
            page.screenshot(path=str(image_path), full_page=True)
        except Exception as exc:
            errors.append(f"screenshot failed: {exc}")
        return html, "; ".join(errors) or None

    def capture_p_kashikan_schedule(
        self,
        reservation_url: str,
        target_date: date,
        snapshot_directory: Path,
        facility_code: str,
        facility_name: str,
    ) -> PageCapture:
        """Follow the anonymous public form flow observed on the live site."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        if self._browser is None:
            raise RuntimeError("PlaywrightClient must be used as a context manager")

        checked_at = datetime.now(JST).isoformat(timespec="seconds")
        date_label = target_date.isoformat()
        ymd = target_date.strftime("%Y%m%d")
        response_status: int | None = None
        html = ""
        step = "top"
        context = self._get_p_kashikan_context()
        page = context.new_page()
        try:
            response = page.goto(
                reservation_url,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            response_status = response.status if response else None
            html, snapshot_error = self._save_page_snapshot(
                page, snapshot_directory, f"{date_label}-top"
            )
            self._save_p_kashikan_diagnostics(
                page,
                context,
                response,
                html,
                snapshot_directory,
                f"{date_label}-top",
            )
            if snapshot_error:
                raise ScrapeStructureError("snapshot_error", snapshot_error)
            if _is_access_denied_response(response_status, html):
                raise ScrapeStructureError(
                    "access_denied", f"Access denied (HTTP {response_status})"
                )

            step = "facility-search"
            with page.expect_navigation(wait_until="domcontentloaded", timeout=60_000) as nav:
                page.get_by_role("link", name="施設 の空きを見る").click()
            response = nav.value
            response_status = response.status if response else response_status
            page.locator('input[name="ShisetsuCode"]').first.wait_for(
                state="attached", timeout=30_000
            )
            html, snapshot_error = self._save_page_snapshot(
                page, snapshot_directory, f"{date_label}-facility-search"
            )
            self._save_p_kashikan_diagnostics(
                page,
                context,
                response,
                html,
                snapshot_directory,
                f"{date_label}-facility-search",
            )
            if snapshot_error:
                raise ScrapeStructureError("snapshot_error", snapshot_error)
            if _is_access_denied_response(response_status, html):
                raise ScrapeStructureError(
                    "access_denied", f"Access denied (HTTP {response_status})"
                )
            facility_radio = page.locator(f"#scd{facility_code}")
            if facility_radio.count() != 1:
                raise ScrapeStructureError(
                    "facility_not_found",
                    f"{facility_name} facility code {facility_code} was not found",
                )

            step = "facility-selected"
            with page.expect_navigation(wait_until="domcontentloaded", timeout=60_000) as nav:
                facility_radio.click()
            response = nav.value
            response_status = response.status if response else response_status
            page.locator('input[name="UseDate"]').first.wait_for(
                state="attached", timeout=30_000
            )
            html, snapshot_error = self._save_page_snapshot(
                page, snapshot_directory, f"{date_label}-facility-selected"
            )
            self._save_p_kashikan_diagnostics(
                page,
                context,
                response,
                html,
                snapshot_directory,
                f"{date_label}-facility-selected",
            )
            if snapshot_error:
                raise ScrapeStructureError("snapshot_error", snapshot_error)
            if _is_access_denied_response(response_status, html):
                raise ScrapeStructureError(
                    "access_denied", f"Access denied (HTTP {response_status})"
                )

            step = "schedule"
            with page.expect_navigation(wait_until="domcontentloaded", timeout=60_000) as nav:
                page.evaluate(
                    """({ ymd, facilityCode }) => {
                        const form = document.forms.forma;
                        if (!form || !form.elements.UseDate || !form.elements.ShisetsuCode) {
                            throw new Error('Expected public availability form is missing');
                        }
                        form.elements.UseYM.value = ymd.slice(0, 6);
                        form.elements.UseDay.value = String(Number(ymd.slice(6, 8)));
                        form.elements.UseDate.value = ymd;
                        form.elements.ShisetsuCode.value = facilityCode;
                        form.elements.disp_span.value = '0';
                        form.submit();
                    }""",
                    {"ymd": ymd, "facilityCode": facility_code},
                )
            response = nav.value
            response_status = response.status if response else response_status
            try:
                page.locator(f'input[name="UseDate"][value="{ymd}"]').wait_for(
                    state="attached", timeout=30_000
                )
            except PlaywrightTimeoutError as exc:
                raise ScrapeStructureError(
                    "date_selection_failed",
                    f"The schedule did not switch to {date_label}",
                ) from exc
            try:
                page.locator(".SelectCalendar table.koma-table td.name").first.wait_for(
                    state="attached", timeout=30_000
                )
            except PlaywrightTimeoutError as exc:
                raise ScrapeStructureError(
                    "no_schedule_table",
                    f"No {facility_name} court schedule was found",
                ) from exc
            html, snapshot_error = self._save_page_snapshot(
                page, snapshot_directory, f"{date_label}-schedule"
            )
            self._save_p_kashikan_diagnostics(
                page,
                context,
                response,
                html,
                snapshot_directory,
                f"{date_label}-schedule",
            )
            if snapshot_error:
                raise ScrapeStructureError("snapshot_error", snapshot_error)
            if _is_access_denied_response(response_status, html):
                raise ScrapeStructureError(
                    "access_denied", f"Access denied (HTTP {response_status})"
                )
            return PageCapture(
                html=html,
                checked_at=checked_at,
                response_status=response_status,
            )
        except ScrapeStructureError as exc:
            html, snapshot_error = self._save_page_snapshot(
                page, snapshot_directory, f"{date_label}-{step}-error"
            )
            message = str(exc)
            if snapshot_error:
                message = f"{message}; {snapshot_error}"
            return PageCapture(
                html=html,
                checked_at=checked_at,
                response_status=response_status,
                error_type=exc.error_type,
                error_message=message,
            )
        except PlaywrightTimeoutError as exc:
            html, snapshot_error = self._save_page_snapshot(
                page, snapshot_directory, f"{date_label}-{step}-error"
            )
            error_type = "navigation_timeout"
            if step == "schedule":
                error_type = "date_selection_failed"
            message = str(exc)
            if snapshot_error:
                message = f"{message}; {snapshot_error}"
            return PageCapture(
                html=html,
                checked_at=checked_at,
                response_status=response_status,
                error_type=error_type,
                error_message=message,
            )
        except Exception as exc:
            html, snapshot_error = self._save_page_snapshot(
                page, snapshot_directory, f"{date_label}-{step}-error"
            )
            message = str(exc)
            if snapshot_error:
                message = f"{message}; {snapshot_error}"
            return PageCapture(
                html=html,
                checked_at=checked_at,
                response_status=response_status,
                error_type="navigation_error",
                error_message=message,
            )
        finally:
            page.close()


def generate_target_days(
    start: date | None = None,
    days: int = WINDOW_DAYS,
) -> list[TargetDay]:
    """Return weekends and Japanese holidays within an inclusive 15-day window."""
    if days < 1:
        raise ValueError("days must be at least 1")

    first_day = start or datetime.now(JST).date()
    targets: list[TargetDay] = []
    for offset in range(days):
        current = first_day + timedelta(days=offset)
        holiday = jpholiday.is_holiday_name(current)
        if holiday:
            targets.append(TargetDay(current, "holiday", holiday))
        elif current.weekday() >= 5:
            targets.append(TargetDay(current, "weekend", None))
    return targets


def parse_clock(value: str) -> time:
    normalized = value.replace("：", ":")
    hour, minute = (int(part) for part in normalized.split(":"))
    return time(hour, minute)


def clock_to_minutes(value: str | time) -> int:
    parsed = parse_clock(value) if isinstance(value, str) else value
    return parsed.hour * 60 + parsed.minute


def minutes_to_clock(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def overlaps_monitor_window(start: str, end: str) -> bool:
    slot_start = parse_clock(start)
    slot_end = parse_clock(end)
    return max(slot_start, MONITOR_START) < min(slot_end, MONITOR_END)


def make_slot_id(
    facility_id: str,
    target_date: str,
    court_name: str,
    start_time: str,
    end_time: str,
) -> str:
    source = "|".join(
        (facility_id, target_date, court_name, start_time, end_time)
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def make_availability_slot(
    facility_id: str,
    facility_name: str,
    target_date: str,
    court_name: str,
    start_minutes: int,
    end_minutes: int,
    reservation_url: str,
) -> dict[str, Any]:
    start_time = minutes_to_clock(start_minutes)
    end_time = minutes_to_clock(end_minutes)
    return {
        "facility_id": facility_id,
        "facility_name": facility_name,
        "date": target_date,
        "court_name": court_name,
        "start_time": start_time,
        "end_time": end_time,
        "duration_minutes": end_minutes - start_minutes,
        "status": "available",
        "reservation_url": reservation_url,
        "slot_id": make_slot_id(
            facility_id,
            target_date,
            court_name,
            start_time,
            end_time,
        ),
    }


def _style_width(element: Tag) -> float:
    match = WIDTH_PATTERN.search(element.get("style", ""))
    if not match:
        raise ScrapeStructureError(
            "unexpected_dom",
            "A reservation state cell has no percentage width",
        )
    return float(match.group(1))


def _is_hidden(element: Tag) -> bool:
    if element.has_attr("hidden") or element.get("aria-hidden") == "true":
        return True
    style = element.get("style", "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def merge_consecutive_slots(slots: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only merged ranges; atomic ranges are not retained separately."""
    deduplicated = {slot["slot_id"]: dict(slot) for slot in slots}
    ordered = sorted(
        deduplicated.values(),
        key=lambda slot: (
            slot["date"],
            natural_sort_key(slot["court_name"]),
            slot["start_time"],
            slot["end_time"],
        ),
    )
    merged: list[dict[str, Any]] = []

    for slot in ordered:
        if (
            merged
            and merged[-1]["date"] == slot["date"]
            and merged[-1]["court_name"] == slot["court_name"]
            and merged[-1]["end_time"] == slot["start_time"]
        ):
            current = merged[-1]
            current["end_time"] = slot["end_time"]
            current["duration_minutes"] += slot["duration_minutes"]
            current["slot_id"] = make_slot_id(
                current["facility_id"],
                current["date"],
                current["court_name"],
                current["start_time"],
                current["end_time"],
            )
        else:
            merged.append(dict(slot))
    return merged


def natural_sort_key(value: str) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part
        for part in re.split(r"(\d+)", value)
    )


def parse_kamoike_html(
    html: str,
    target: TargetDay,
    reservation_url: str,
    checked_at: str,
) -> dict[str, Any]:
    """Parse Vue-rendered court rows observed on the live reservation page."""
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True).lower()
    if any(marker in page_text for marker in ACCESS_DENIED_MARKERS):
        raise ScrapeStructureError("access_denied", "Access denied page detected")

    roots = [
        root
        for root in soup.select(".rsv__result[data-reserve]")
        if isinstance(root, Tag) and not _is_hidden(root)
    ]
    if not roots:
        raise ScrapeStructureError(
            "no_schedule_table",
            "No visible .rsv__result[data-reserve] schedule was found",
        )

    raw_slots: list[dict[str, Any]] = []
    row_count = 0
    for root in roots:
        for field in root.select(":scope > section.rsv__field"):
            if not isinstance(field, Tag) or _is_hidden(field):
                continue
            court_element = field.select_one(
                "h3.rsv__result__item:not(.major--item--color) em"
            )
            if court_element is None:
                continue
            court_name = court_element.get_text(" ", strip=True)
            if not court_name:
                raise ScrapeStructureError(
                    "unexpected_dom",
                    "A court row has no court name",
                )

            time_elements = field.select(".rsv__result__time > li")
            state_elements = field.select(".rsv__result__situation > li")
            if len(time_elements) < 2 or not state_elements:
                raise ScrapeStructureError(
                    "unexpected_dom",
                    f"Missing time header or state cells for {court_name}",
                )

            time_labels = [
                element.get_text(" ", strip=True) for element in time_elements
            ]
            try:
                grid_start = clock_to_minutes(time_labels[0])
                grid_end = clock_to_minutes(time_labels[-1])
            except (ValueError, IndexError) as exc:
                raise ScrapeStructureError(
                    "unexpected_dom",
                    f"Invalid time header for {court_name}: {time_labels}",
                ) from exc
            if grid_end <= grid_start:
                raise ScrapeStructureError(
                    "unexpected_dom",
                    f"Invalid time range for {court_name}: {time_labels}",
                )

            active_cells: list[tuple[Tag, str, float]] = []
            for element in state_elements:
                if not isinstance(element, Tag):
                    continue
                state_match = STATE_CLASS_PATTERN.search(" ".join(element.get("class", [])))
                if state_match:
                    active_cells.append(
                        (element, state_match.group(1), _style_width(element))
                    )
            if not active_cells:
                raise ScrapeStructureError(
                    "unexpected_dom",
                    f"No classified reservation cells for {court_name}",
                )

            active_width = sum(width for _, _, width in active_cells)
            if active_width <= 0:
                raise ScrapeStructureError(
                    "unexpected_dom",
                    f"Invalid reservation cell widths for {court_name}",
                )

            row_count += 1
            elapsed_width = 0.0
            grid_minutes = grid_end - grid_start
            for element, state, width in active_cells:
                segment_start = grid_start + round(
                    elapsed_width / active_width * grid_minutes
                )
                elapsed_width += width
                segment_end = grid_start + round(
                    elapsed_width / active_width * grid_minutes
                )
                if state != "yes":
                    continue

                icon = element.select_one("i")
                label = None
                if isinstance(icon, Tag):
                    label = icon.get("aria-label") or icon.get("area-label")
                if label and label != "予約可":
                    continue

                clipped_start = max(segment_start, clock_to_minutes(MONITOR_START))
                clipped_end = min(segment_end, clock_to_minutes(MONITOR_END))
                if clipped_end <= clipped_start:
                    continue
                raw_slots.append(
                    make_availability_slot(
                        KAMOIKE_FACILITY_ID,
                        KAMOIKE_FACILITY_NAME,
                        target.date.isoformat(),
                        court_name,
                        clipped_start,
                        clipped_end,
                        reservation_url,
                    )
                )

    if row_count == 0:
        raise ScrapeStructureError(
            "unexpected_dom",
            "The schedule has no visible court rows",
        )

    availability = [
        slot
        for slot in merge_consecutive_slots(raw_slots)
        if slot["duration_minutes"] >= MINIMUM_DURATION_MINUTES
    ]
    return success_result(target, checked_at, reservation_url, availability)


def success_result(
    target: TargetDay,
    checked_at: str,
    reservation_url: str,
    availability: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "date": target.date.isoformat(),
        "day_type": target.day_type,
        "holiday_name": target.holiday_name,
        "status": "success",
        "error_type": None,
        "error_message": None,
        "checked_at": checked_at,
        "reservation_url": reservation_url,
        "availability": availability,
    }


def error_result(
    target: TargetDay,
    checked_at: str,
    reservation_url: str,
    error_type: str,
    error_message: str,
    response_status: int | None = None,
) -> dict[str, Any]:
    result = {
        "date": target.date.isoformat(),
        "day_type": target.day_type,
        "holiday_name": target.holiday_name,
        "status": "error",
        "error_type": error_type,
        "error_message": error_message,
        "checked_at": checked_at,
        "reservation_url": reservation_url,
        "availability": [],
    }
    if response_status is not None:
        result["http_status"] = response_status
    return result


def pending_result(
    target: TargetDay,
    reservation_url: str,
    message: str,
) -> dict[str, Any]:
    return {
        "date": target.date.isoformat(),
        "day_type": target.day_type,
        "holiday_name": target.holiday_name,
        "status": "selector_pending",
        "error_type": None,
        "error_message": message,
        "checked_at": datetime.now(JST).isoformat(timespec="seconds"),
        "reservation_url": reservation_url,
        "availability": [],
    }


def scrape_kamoike(
    client: PageClient,
    facility: Facility,
    target: TargetDay,
) -> dict[str, Any]:
    reservation_url = facility.url_template.format(date=target.date.isoformat())
    capture = client.capture_page(
        reservation_url,
        SNAPSHOT_ROOT / KAMOIKE_FACILITY_ID,
        target.date.isoformat(),
    )
    if capture.error_type:
        return error_result(
            target,
            capture.checked_at,
            reservation_url,
            capture.error_type,
            capture.error_message or capture.error_type,
            capture.response_status,
        )
    try:
        return parse_kamoike_html(
            capture.html,
            target,
            reservation_url,
            capture.checked_at,
        )
    except ScrapeStructureError as exc:
        return error_result(
            target,
            capture.checked_at,
            reservation_url,
            exc.error_type,
            str(exc),
        )


def _pixel_width(element: Tag) -> float:
    match = PIXEL_WIDTH_PATTERN.search(element.get("style", ""))
    if not match:
        raise ScrapeStructureError(
            "unexpected_dom", "A P-Kashikan schedule cell has no pixel width"
        )
    return float(match.group(1))


def _normalize_p_kashikan_boundary(minutes: int) -> int:
    """Convert P-Kashikan's inclusive :29/:59 values to displayed boundaries."""
    return minutes + 1 if minutes % 60 in {29, 59} else minutes


def _p_kashikan_cell_minutes(
    element: Tag,
    inferred_start: int,
    inferred_end: int,
    target: TargetDay,
    facility_code: str,
    facility_name: str,
) -> tuple[int, int]:
    handler = element.get("onmousedown", "")
    if not handler:
        return (
            _normalize_p_kashikan_boundary(inferred_start),
            _normalize_p_kashikan_boundary(inferred_end),
        )
    match = P_KASHIKAN_SLOT_PATTERN.search(handler)
    if not match:
        raise ScrapeStructureError(
            "unexpected_dom",
            f"An available {facility_name} cell has an unknown handler",
        )
    if not match.group("resource").startswith(f"{facility_code}|"):
        raise ScrapeStructureError(
            "unexpected_dom", "An available cell belongs to another facility"
        )
    expected_date = target.date.strftime("%Y/%m/%d")
    if match.group("date") != expected_date:
        raise ScrapeStructureError(
            "date_selection_failed",
            f"Expected {expected_date}, got {match.group('date')}",
        )
    time_range = match.group("times")
    start = _normalize_p_kashikan_boundary(
        clock_to_minutes(f"{time_range[0:2]}:{time_range[2:4]}")
    )
    end = _normalize_p_kashikan_boundary(
        clock_to_minutes(f"{time_range[4:6]}:{time_range[6:8]}")
    )
    if end <= start:
        raise ScrapeStructureError(
            "unexpected_dom",
            f"Invalid {facility_name} cell time range: {time_range}",
        )
    return start, end


def parse_p_kashikan_html(
    html: str,
    target: TargetDay,
    reservation_url: str,
    checked_at: str,
    facility_id: str,
    facility_name: str,
    facility_code: str,
) -> dict[str, Any]:
    """Parse the live P-KASHIKAN court grid without scanning legends."""
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    if any(marker in page_text.lower() for marker in ACCESS_DENIED_MARKERS):
        raise ScrapeStructureError("access_denied", "Access denied page detected")

    facility_input = soup.select_one(
        f'input[name="ShisetsuCode"][value="{facility_code}"]'
    )
    if facility_input is None:
        raise ScrapeStructureError(
            "facility_not_found",
            f"{facility_name} facility code {facility_code} was not found",
        )
    if not facility_input.has_attr("checked"):
        raise ScrapeStructureError(
            "unexpected_dom", f"{facility_name} is not the selected facility"
        )

    use_date = soup.select_one('input[name="UseDate"]')
    expected_ymd = target.date.strftime("%Y%m%d")
    if not isinstance(use_date, Tag) or use_date.get("value") != expected_ymd:
        raise ScrapeStructureError(
            "date_selection_failed",
            f"Expected UseDate={expected_ymd}",
        )

    normalized_text = unicodedata.normalize("NFKC", page_text)
    if unicodedata.normalize("NFKC", facility_name) not in normalized_text:
        raise ScrapeStructureError(
            "unexpected_dom", "The selected facility heading is missing"
        )

    calendar = soup.select_one(".SelectCalendar")
    if not isinstance(calendar, Tag) or _is_hidden(calendar):
        raise ScrapeStructureError(
            "no_schedule_table", "No visible .SelectCalendar schedule was found"
        )
    header = calendar.select_one("table.koma-table th.header")
    if not isinstance(header, Tag) or header.get_text(" ", strip=True) != "施設":
        raise ScrapeStructureError(
            "unexpected_dom", f"The {facility_name} time header is missing"
        )
    header_cells = header.find_parent("tr").find_all("th", recursive=False)
    try:
        hour_labels = [int(cell.get_text(" ", strip=True)) for cell in header_cells[1:]]
    except ValueError as exc:
        raise ScrapeStructureError(
            "unexpected_dom", f"The {facility_name} time header is invalid"
        ) from exc
    if len(hour_labels) < 2 or hour_labels != list(
        range(hour_labels[0], hour_labels[-1] + 1)
    ):
        raise ScrapeStructureError(
            "unexpected_dom",
            f"Unexpected {facility_name} time header: {hour_labels}",
        )
    grid_start = hour_labels[0] * 60
    grid_end = (hour_labels[-1] + 1) * 60

    availability_candidates: list[dict[str, Any]] = []
    row_count = 0
    for table in calendar.select("table.koma-table"):
        if not isinstance(table, Tag) or _is_hidden(table):
            continue
        row = table.select_one("tr")
        if not isinstance(row, Tag):
            continue
        court_element = row.select_one("td.name")
        if not isinstance(court_element, Tag):
            continue
        court_name = court_element.get_text(" ", strip=True)
        if not court_name:
            raise ScrapeStructureError(
                "unexpected_dom", f"A {facility_name} court row has no court name"
            )
        cells = [
            cell
            for cell in row.find_all("td", recursive=False)
            if isinstance(cell, Tag) and cell is not court_element and not _is_hidden(cell)
        ]
        if not cells:
            raise ScrapeStructureError(
                "unexpected_dom", f"No state cells for {court_name}"
            )
        widths = [_pixel_width(cell) for cell in cells]
        total_width = sum(widths)
        if total_width <= 0:
            raise ScrapeStructureError(
                "unexpected_dom", f"Invalid cell widths for {court_name}"
            )

        row_count += 1
        row_slots: list[dict[str, Any]] = []
        elapsed_width = 0.0
        grid_minutes = grid_end - grid_start
        for cell, width in zip(cells, widths, strict=True):
            inferred_start = grid_start + round(
                elapsed_width / total_width * grid_minutes
            )
            elapsed_width += width
            inferred_end = grid_start + round(
                elapsed_width / total_width * grid_minutes
            )
            marker = cell.get_text(" ", strip=True)
            if marker not in {"●", "○", "〇"}:
                continue
            if marker in {"○", "〇"} and not cell.get("onmousedown"):
                raise ScrapeStructureError(
                    "unexpected_dom",
                    f"An internet-available cell for {court_name} has no time data",
                )
            segment_start, segment_end = _p_kashikan_cell_minutes(
                cell,
                inferred_start,
                inferred_end,
                target,
                facility_code,
                facility_name,
            )
            clipped_start = max(segment_start, clock_to_minutes(MONITOR_START))
            clipped_end = min(segment_end, clock_to_minutes(MONITOR_END))
            if clipped_end <= clipped_start:
                continue
            row_slots.append(
                make_availability_slot(
                    facility_id,
                    facility_name,
                    target.date.isoformat(),
                    court_name,
                    clipped_start,
                    clipped_end,
                    reservation_url,
                )
            )
        availability_candidates.extend(merge_consecutive_slots(row_slots))

    if row_count == 0:
        raise ScrapeStructureError(
            "unexpected_dom", f"The {facility_name} schedule has no visible court rows"
        )
    availability_by_id = {
        slot["slot_id"]: slot
        for slot in availability_candidates
        if slot["duration_minutes"] >= MINIMUM_DURATION_MINUTES
    }
    availability = [
        slot
        for _, slot in sorted(
            availability_by_id.items(),
            key=lambda item: (
                natural_sort_key(item[1]["court_name"]),
                item[1]["start_time"],
                item[1]["end_time"],
            ),
        )
    ]
    return success_result(target, checked_at, reservation_url, availability)


def parse_sumizei_html(
    html: str,
    target: TargetDay,
    reservation_url: str,
    checked_at: str,
) -> dict[str, Any]:
    return parse_p_kashikan_html(
        html,
        target,
        reservation_url,
        checked_at,
        SUMIZEI_FACILITY_ID,
        SUMIZEI_FACILITY_NAME,
        SUMIZEI_FACILITY_CODE,
    )


def scrape_p_kashikan(
    client: PageClient,
    facility: Facility,
    target: TargetDay,
) -> dict[str, Any]:
    reservation_url = facility.url_template
    if not facility.p_kashikan_code:
        return error_result(
            target,
            datetime.now(JST).isoformat(timespec="seconds"),
            reservation_url,
            "facility_not_found",
            f"{facility.name} has no P-Kashikan facility code",
        )
    capture = client.capture_p_kashikan_schedule(
        reservation_url,
        target.date,
        SNAPSHOT_ROOT / facility.id,
        facility.p_kashikan_code,
        facility.name,
    )
    if capture.error_type:
        return error_result(
            target,
            capture.checked_at,
            reservation_url,
            capture.error_type,
            capture.error_message or capture.error_type,
            capture.response_status,
        )
    try:
        return parse_p_kashikan_html(
            capture.html,
            target,
            reservation_url,
            capture.checked_at,
            facility.id,
            facility.name,
            facility.p_kashikan_code,
        )
    except ScrapeStructureError as exc:
        return error_result(
            target,
            capture.checked_at,
            reservation_url,
            exc.error_type,
            str(exc),
        )


def scrape_sumizei(
    client: PageClient,
    facility: Facility,
    target: TargetDay,
) -> dict[str, Any]:
    return scrape_p_kashikan(client, facility, target)


def configured_facilities() -> tuple[Facility, ...]:
    return (
        Facility(
            id=KAMOIKE_FACILITY_ID,
            name=KAMOIKE_FACILITY_NAME,
            url_template=KAMOIKE_URL_TEMPLATE,
            selector_env="",
            scraper=scrape_kamoike,
            requires_browser=True,
        ),
        Facility(
            id=SUMIZEI_FACILITY_ID,
            name=SUMIZEI_FACILITY_NAME,
            url_template=P_KASHIKAN_BASE_URL,
            selector_env="",
            scraper=scrape_p_kashikan,
            requires_browser=True,
            p_kashikan_code=SUMIZEI_FACILITY_CODE,
        ),
        Facility(
            id=TOUKAI_FACILITY_ID,
            name=TOUKAI_FACILITY_NAME,
            url_template=P_KASHIKAN_BASE_URL,
            selector_env="",
            scraper=scrape_p_kashikan,
            requires_browser=True,
            p_kashikan_code=TOUKAI_FACILITY_CODE,
        ),
    )


def empty_document() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "generated_at": None,
        "window": {
            "days": WINDOW_DAYS,
            "start": MONITOR_START.strftime("%H:%M"),
            "end": MONITOR_END.strftime("%H:%M"),
            "minimum_duration_minutes": MINIMUM_DURATION_MINUTES,
            "timezone": "Asia/Tokyo",
        },
        "facilities": [],
    }


def _previous_date_entry(
    document: dict[str, Any] | None,
    facility_id: str,
    target_date: date,
) -> dict[str, Any] | None:
    if not document:
        return None
    date_label = target_date.isoformat()
    for facility in document.get("facilities", []):
        if facility.get("id") != facility_id:
            continue
        for date_entry in facility.get("dates", []):
            if date_entry.get("date") == date_label:
                return date_entry
    return None


def _p_kashikan_403_fallback(
    facility: Facility,
    target: TargetDay,
    attempted_result: dict[str, Any],
    previous_document: dict[str, Any] | None,
) -> dict[str, Any]:
    checked_at = attempted_result.get("checked_at")
    if not checked_at:
        checked_at = datetime.now(JST).isoformat(timespec="seconds")
    fallback = error_result(
        target,
        str(checked_at),
        facility.url_template,
        "access_denied",
        str(attempted_result.get("error_message") or "Access denied (HTTP 403)"),
        403,
    )
    previous_entry = _previous_date_entry(
        previous_document,
        facility.id,
        target.date,
    )
    if previous_entry and (
        previous_entry.get("status") == "success"
        or previous_entry.get("fallback_from_previous") is True
    ):
        fallback["availability"] = deepcopy(
            previous_entry.get("availability", previous_entry.get("slots", []))
        )
        fallback["fallback_from_previous"] = True
        fallback["last_success_checked_at"] = (
            previous_entry.get("last_success_checked_at")
            or previous_entry.get("checked_at")
        )
    return fallback


def build_document(
    targets: list[TargetDay],
    facilities: Iterable[Facility] | None = None,
    client_factory: Callable[[], PlaywrightClient] = PlaywrightClient,
    previous_document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_facilities = tuple(facilities or configured_facilities())
    needs_browser = any(
        facility.requires_browser
        or (
            facility.url_template
            and facility.selector_env
            and os.getenv(facility.selector_env, "").strip()
        )
        for facility in selected_facilities
    )
    client_context: Any = client_factory() if needs_browser else _NoopClientContext()

    facility_results: list[dict[str, Any]] = []
    p_kashikan_403: dict[str, Any] | None = None
    with client_context as client:
        for facility in selected_facilities:
            dates: list[dict[str, Any]] = []
            for target in targets:
                is_p_kashikan = facility.p_kashikan_code is not None
                if is_p_kashikan and p_kashikan_403 is not None:
                    result = error_result(
                        target,
                        str(p_kashikan_403["checked_at"]),
                        facility.url_template,
                        "access_denied",
                        "Skipped after P-Kashikan HTTP 403 earlier in this run",
                        403,
                    )
                else:
                    try:
                        result = facility.scraper(client, facility, target)
                    except Exception as exc:
                        reservation_url = (
                            facility.url_template.format(date=target.date.isoformat())
                            if facility.url_template
                            else ""
                        )
                        result = error_result(
                            target,
                            datetime.now(JST).isoformat(timespec="seconds"),
                            reservation_url,
                            "unexpected_error",
                            str(exc),
                        )
                if is_p_kashikan and result.get("http_status") == 403:
                    if p_kashikan_403 is None:
                        p_kashikan_403 = {
                            "checked_at": result["checked_at"],
                            "facility_id": facility.id,
                            "date": target.date.isoformat(),
                        }
                        print(
                            "::warning::P-Kashikan returned HTTP 403; "
                            "remaining P-Kashikan requests were skipped."
                        )
                    result = _p_kashikan_403_fallback(
                        facility,
                        target,
                        result,
                        previous_document,
                    )
                dates.append(result)
            facility_results.append(
                {
                    "id": facility.id,
                    "name": facility.name,
                    "dates": dates,
                }
            )

    document = empty_document()
    document["generated_at"] = datetime.now(JST).isoformat(timespec="seconds")
    document["facilities"] = facility_results
    return document


class _NoopClientContext:
    def __enter__(self) -> "_NoopClientContext":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def capture_page(
        self,
        url: str,
        snapshot_directory: Path,
        snapshot_name: str,
    ) -> PageCapture:
        raise RuntimeError(f"Browser was not configured for {url}")

    def extract_texts(self, url: str, selector: str) -> list[str]:
        raise RuntimeError(f"Browser was not configured for {url} ({selector})")

    def capture_p_kashikan_schedule(
        self,
        reservation_url: str,
        target_date: date,
        snapshot_directory: Path,
        facility_code: str,
        facility_name: str,
    ) -> PageCapture:
        raise RuntimeError(
            f"Browser was not configured for {facility_name} ({reservation_url})"
        )


def load_document(path: Path = DATA_PATH) -> dict[str, Any]:
    if not path.exists():
        return empty_document()
    return json.loads(path.read_text(encoding="utf-8"))


def write_document(document: dict[str, Any], path: Path = DATA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def comparable_document(document: dict[str, Any]) -> dict[str, Any]:
    comparable = json.loads(json.dumps(document))
    comparable.pop("generated_at", None)
    for facility in comparable.get("facilities", []):
        for date_entry in facility.get("dates", []):
            date_entry.pop("checked_at", None)
    return comparable


def write_availability_outputs(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    dry_run: bool,
    data_path: Path = DATA_PATH,
    output_directory: Path = RUN_OUTPUT_DIRECTORY,
) -> bool:
    output_directory.mkdir(parents=True, exist_ok=True)
    write_document(current, output_directory / "availability.json")

    availability_changed = comparable_document(previous) != comparable_document(
        current
    )
    availability_written = False
    if not dry_run and availability_changed:
        write_document(current, data_path)
        availability_written = True

    print(
        f"availability_changed={availability_changed} "
        f"availability_written={availability_written} dry_run={dry_run}"
    )
    return availability_written


def environment_boolean(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    previous = load_document()
    targets = generate_target_days()
    current = build_document(targets, previous_document=previous)
    write_availability_outputs(
        previous,
        current,
        dry_run=environment_boolean("DRY_RUN"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
