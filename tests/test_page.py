from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import Browser, Page, Playwright, sync_playwright


ROOT = Path(__file__).parents[1]
INDEX_HTML = (ROOT / "index.html").read_text(encoding="utf-8")
AVAILABILITY = json.loads(
    (ROOT / "data" / "availability.json").read_text(encoding="utf-8")
)
UTILS_SCRIPT = BeautifulSoup(INDEX_HTML, "html.parser").find(
    "script", id="page-utils"
).string
JST = timezone(timedelta(hours=9))


@pytest.fixture(scope="module")
def playwright_runtime() -> Playwright:
    runtime = sync_playwright().start()
    yield runtime
    runtime.stop()


@pytest.fixture(scope="module")
def browser(playwright_runtime: Playwright) -> Browser:
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    instance = playwright_runtime.chromium.launch(
        headless=True,
        executable_path=executable,
    )
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def utils_page(browser: Browser) -> Page:
    context = browser.new_context(timezone_id="Asia/Tokyo")
    page = context.new_page()
    page.add_script_tag(content=UTILS_SCRIPT)
    yield page
    context.close()


@pytest.fixture
def page_loader(browser: Browser):
    contexts = []

    def load(data: dict | None = None, viewport: dict | None = None):
        payload = data or AVAILABILITY
        context = browser.new_context(
            timezone_id="Asia/Tokyo",
            viewport=viewport or {"width": 390, "height": 844},
        )
        contexts.append(context)
        page = context.new_page()
        console_errors: list[str] = []
        page_errors: list[str] = []
        data_requests: list[str] = []
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        def route_request(route) -> None:
            url = route.request.url
            if url == "http://pages.test/project/index.html":
                route.fulfill(status=200, content_type="text/html", body=INDEX_HTML)
            elif url == (
                "http://pages.test/project/assets/images/"
                "kagoshima-municipal-tennis-court.webp"
            ):
                route.fulfill(
                    status=200,
                    content_type="image/webp",
                    path=str(
                        ROOT
                        / "assets/images/kagoshima-municipal-tennis-court.webp"
                    ),
                )
            elif url.startswith("http://pages.test/project/data/availability.json?"):
                data_requests.append(url)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(payload, ensure_ascii=False),
                )
            else:
                route.fulfill(status=404, body="not found")

        page.route("**/*", route_request)
        page.goto("http://pages.test/project/index.html")
        page.locator("#reload:not([disabled])").wait_for()
        return page, console_errors, page_errors, data_requests

    yield load
    for context in contexts:
        context.close()


def make_slot(index: int) -> dict:
    return {
        "court_name": f"コート{index}",
        "start_time": "09:00",
        "end_time": "10:00",
        "reservation_url": "https://example.test/reserve",
    }


def make_date(
    date: str,
    status: str = "success",
    availability_count: int = 0,
    checked_at: str = "2026-08-01T10:00:00+09:00",
) -> dict:
    return {
        "date": date,
        "day_type": "weekend",
        "holiday_name": None,
        "status": status,
        "checked_at": checked_at,
        "availability": [make_slot(index) for index in range(availability_count)],
        "error_message": "テスト用エラー" if status == "error" else None,
    }


def make_document(dates: list[dict]) -> dict:
    return {
        "generated_at": datetime.now(JST).isoformat(),
        "window": {"start": "08:00", "end": "13:00", "timezone": "Asia/Tokyo"},
        "facilities": [{"id": "test", "name": "テストテニスコート", "dates": dates}],
    }


def evaluate(utils_page: Page, expression: str, argument=None):
    return utils_page.evaluate(expression, argument)


def expected_availability_count(facility: dict) -> int:
    return sum(
        len(date_entry.get("availability", []))
        for date_entry in facility.get("dates", [])
        if date_entry.get("status") == "success"
    )


def expected_empty_success_count(facility: dict) -> int:
    return sum(
        1
        for date_entry in facility.get("dates", [])
        if date_entry.get("status") == "success"
        and not date_entry.get("availability", [])
    )


def expected_facility_status_label(facility: dict) -> str:
    dates = facility.get("dates", [])
    statuses = [date_entry.get("status") for date_entry in dates]
    if "selector_pending" in statuses:
        return "設定調整中"
    if not statuses or all(status == "error" for status in statuses):
        return "取得エラー"
    if any(status != "success" for status in statuses):
        return "一部取得エラー"
    return "正常"


def test_top_page_has_static_account_link_without_auth_dependencies() -> None:
    soup = BeautifulSoup(INDEX_HTML, "html.parser")
    account_link = soup.find("a", href="account/index.html")

    assert account_link
    assert account_link.get_text(strip=True) == "マイページ"
    script_sources = [
        script.get("src", "")
        for script in soup.find_all("script")
        if script.get("src")
    ]
    assert all("supabase" not in source.lower() for source in script_sources)
    assert all("auth-config" not in source.lower() for source in script_sources)


def test_top_page_has_responsive_municipal_court_hero() -> None:
    soup = BeautifulSoup(INDEX_HTML, "html.parser")
    hero = soup.find("figure", class_="court-hero")
    image = hero.find("img")
    asset = ROOT / image["src"]

    assert hero.find(class_="court-hero__place").get_text(strip=True) == (
        "鹿児島市営テニスコート"
    )
    assert hero.find(class_="court-hero__message").get_text(strip=True) == (
        "空きを見つけて、コートへ出よう。"
    )
    assert image["alt"] == (
        "青空の下、鹿児島市営テニスコートでテニスを楽しむ人たち"
    )
    assert (image["width"], image["height"]) == ("1200", "900")
    assert image["decoding"] == "async"
    assert image["fetchpriority"] == "high"
    assert asset.is_file()
    assert asset.stat().st_size <= 100_000


def test_facility_and_total_availability_counts_are_correct(utils_page: Page) -> None:
    data = {
        "facilities": [
            {
                "id": "facility-a",
                "name": "施設A",
                "dates": [
                    make_date("2026-08-01", availability_count=2),
                    make_date("2026-08-02"),
                ],
            },
            {
                "id": "facility-b",
                "name": "施設B",
                "dates": [make_date("2026-08-01", availability_count=1)],
            },
            {
                "id": "facility-c",
                "name": "施設C",
                "dates": [
                    make_date("2026-08-01", availability_count=1),
                    make_date("2026-08-02", availability_count=2),
                ],
            },
        ]
    }
    result = evaluate(
        utils_page,
        "data => ({ facilities: data.facilities.map(facility => "
        "AvailabilityPage.countAvailability(facility)), "
        "total: AvailabilityPage.summarizeAllFacilities(data).totalAvailability })",
        data,
    )

    assert result == {"facilities": [2, 1, 3], "total": 6}


@pytest.mark.parametrize(
    ("statuses", "expected_status", "expected_label"),
    [
        (["success", "success"], "normal", "正常"),
        (["success", "error"], "partial_error", "一部取得エラー"),
        (["error", "error"], "error", "取得エラー"),
    ],
)
def test_facility_status_summarization(
    utils_page: Page,
    statuses: list[str],
    expected_status: str,
    expected_label: str,
) -> None:
    facility = {
        "dates": [make_date(f"2026-08-0{index + 1}", status) for index, status in enumerate(statuses)]
    }

    summary = evaluate(
        utils_page,
        "facility => AvailabilityPage.summarizeFacility(facility)",
        facility,
    )

    assert summary["status"] == expected_status
    assert summary["statusLabel"] == expected_label


def test_selector_pending_status_takes_priority(utils_page: Page) -> None:
    facility = {
        "dates": [make_date("2026-08-01"), make_date("2026-08-02", "selector_pending")]
    }

    summary = evaluate(
        utils_page,
        "facility => AvailabilityPage.summarizeFacility(facility)",
        facility,
    )

    assert summary["status"] == "selector_pending"
    assert summary["statusLabel"] == "設定調整中"


def test_latest_facility_checked_at_is_selected(utils_page: Page) -> None:
    facility = {
        "dates": [
            make_date("2026-08-01", checked_at="2026-08-01T09:59:00+09:00"),
            make_date("2026-08-02", checked_at="2026-08-01T10:01:00+09:00"),
        ]
    }

    summary = evaluate(
        utils_page,
        "facility => AvailabilityPage.summarizeFacility(facility)",
        facility,
    )

    assert summary["latestCheckedAt"] == "2026-08-01T10:01:00+09:00"


def test_japanese_date_and_holiday_formatting(utils_page: Page) -> None:
    result = evaluate(
        utils_page,
        "() => [AvailabilityPage.formatJapaneseDate('2026-08-01'), "
        "AvailabilityPage.formatJapaneseDate('2026-08-11', '山の日'), "
        "AvailabilityPage.formatJapaneseDate('2027-01-01', '元日')]",
    )

    assert result == ["8月1日（土）", "8月11日（火・山の日）", "1月1日（金・元日）"]


@pytest.mark.parametrize(
    ("age_minutes", "expected_level", "message_fragment"),
    [
        (60, "fresh", ""),
        (61, "delayed", "更新が遅れています"),
        (120, "delayed", "更新が遅れています"),
        (121, "stale", "2時間以上更新されていません"),
    ],
)
def test_freshness_thresholds(
    utils_page: Page,
    age_minutes: int,
    expected_level: str,
    message_fragment: str,
) -> None:
    result = evaluate(
        utils_page,
        "age => AvailabilityPage.getFreshnessStatus("
        "new Date(Date.UTC(2026, 7, 1, 10, 0) - age * 60000).toISOString(), "
        "new Date(Date.UTC(2026, 7, 1, 10, 0)))",
        age_minutes,
    )

    assert result["level"] == expected_level
    assert message_fragment in result["message"]


@pytest.mark.parametrize("generated_at", [None, "not-a-date"])
def test_invalid_generated_at_warns(utils_page: Page, generated_at: str | None) -> None:
    result = evaluate(
        utils_page,
        "value => AvailabilityPage.getFreshnessStatus(value, new Date())",
        generated_at,
    )

    assert result["level"] == "unknown"
    assert result["message"].startswith("⚠")


def test_only_empty_success_dates_are_partitioned(utils_page: Page) -> None:
    facility = {
        "dates": [
            make_date("2026-08-01", availability_count=1),
            make_date("2026-08-02", "error"),
            make_date("2026-08-03", "selector_pending"),
            make_date("2026-08-04"),
        ]
    }

    result = evaluate(
        utils_page,
        "facility => { const parts = AvailabilityPage.partitionFacilityDates(facility); "
        "return { visible: parts.alwaysVisible.map(item => item.date), "
        "empty: parts.emptySuccess.map(item => item.date) }; }",
        facility,
    )

    assert result["visible"] == ["2026-08-01", "2026-08-02", "2026-08-03"]
    assert result["empty"] == ["2026-08-04"]


def test_existing_json_renders_counts_status_dates_and_relative_data_path(page_loader) -> None:
    page, console_errors, page_errors, requests = page_loader()
    expected_counts = [
        expected_availability_count(facility)
        for facility in AVAILABILITY["facilities"]
    ]
    expected_empty_counts = [
        expected_empty_success_count(facility)
        for facility in AVAILABILITY["facilities"]
    ]
    expected_total = sum(expected_counts)
    expected_total_label = (
        f"合計{expected_total}件の空き候補"
        if expected_total
        else "現在、対象時間帯の空き候補はありません"
    )
    expected_breakdown = " / ".join(
        f"{facility['name'].removesuffix('テニスコート')} {count}件"
        for facility, count in zip(AVAILABILITY["facilities"], expected_counts)
    )
    expected_count_labels = [
        f"空き候補 {count}件"
        for count in expected_counts
    ]
    expected_status_labels = [
        expected_facility_status_label(facility)
        for facility in AVAILABILITY["facilities"]
    ]
    expected_facility_names = [
        facility["name"] for facility in AVAILABILITY["facilities"]
    ]
    date_titles = page.locator(".date-title h3").all_inner_texts()

    assert page.locator("#total-availability").inner_text() == expected_total_label
    assert page.locator("#facility-counts").inner_text() == expected_breakdown
    assert page.locator(".facility-header h2").all_inner_texts() == expected_facility_names
    assert "東開庭球場" in expected_facility_names
    assert page.locator(".status-badge").all_inner_texts() == expected_status_labels
    assert page.locator(".availability-count").all_inner_texts() == expected_count_labels
    facility_cards = page.locator(".facility")
    for index, expected_empty_count in enumerate(expected_empty_counts):
        card = facility_cards.nth(index)
        toggle = card.locator(".empty-days-toggle")
        container = card.locator(".empty-days-container")
        if expected_empty_count:
            assert toggle.count() == 1
            assert (
                toggle.inner_text()
                == f"空きなしの日を表示（{expected_empty_count}日）"
            )
            assert toggle.get_attribute("aria-expanded") == "false"
            assert toggle.get_attribute("aria-controls") == container.get_attribute("id")
            assert container.locator(".date-block").count() == expected_empty_count
            assert container.is_hidden()
        else:
            assert toggle.count() == 0
            assert container.count() == 0
    assert all(
        ":59" not in time_label
        for time_label in page.locator(".slot strong").all_inner_texts()
    )
    assert date_titles
    assert all(
        re.fullmatch(r"\d{1,2}月\d{1,2}日（.+）", title)
        for title in date_titles
    )
    assert "最終確認" in page.locator(".facility-summary").all_inner_texts()[0]
    assert page.locator("#freshness-warning").get_attribute("data-level") in {
        "fresh", "delayed", "stale"
    }
    assert requests and "project/data/availability.json?t=" in requests[0]
    assert console_errors == []
    assert page_errors == []


def test_p_kashikan_facilities_render_official_boundary_times(page_loader) -> None:
    sumizei_date = make_date("2026-08-01")
    sumizei_date["availability"] = [
        {
            **make_slot(1),
            "court_name": "テニスコート2",
            "start_time": "11:00",
            "end_time": "12:00",
        }
    ]
    toukai_date = make_date("2026-08-01")
    toukai_date["availability"] = [
        {
            **make_slot(2),
            "court_name": "C・Dコート(ナイターあり)",
            "start_time": "12:00",
            "end_time": "13:00",
        }
    ]
    data = {
        "generated_at": datetime.now(JST).isoformat(),
        "window": {"start": "08:00", "end": "13:00", "timezone": "Asia/Tokyo"},
        "facilities": [
            {
                "id": "sumizei",
                "name": "SuMIzeiテニスコート",
                "dates": [sumizei_date],
            },
            {
                "id": "toukai-tennis",
                "name": "東開庭球場",
                "dates": [toukai_date],
            },
        ],
    }

    page, console_errors, page_errors, _ = page_loader(data)

    assert page.locator(".slot strong").all_inner_texts() == [
        "11:00〜12:00",
        "12:00〜13:00",
    ]
    assert ":59" not in page.locator("#content").inner_text()
    assert console_errors == []
    assert page_errors == []


def test_empty_days_toggle_updates_aria_and_visibility(page_loader) -> None:
    data = make_document(
        [
            make_date("2026-08-01", availability_count=1),
            make_date("2026-08-02"),
            make_date("2026-08-03"),
            make_date("2026-08-04", "error"),
        ]
    )
    page, _, _, _ = page_loader(data)
    toggle = page.locator(".facility").first.locator(".empty-days-toggle")
    container = page.locator(".facility").first.locator(".empty-days-container")

    assert toggle.inner_text() == "空きなしの日を表示（2日）"
    assert toggle.get_attribute("aria-expanded") == "false"
    assert toggle.get_attribute("aria-controls") == container.get_attribute("id")
    assert page.locator(".facility > .date-block").count() == 2
    assert page.locator(".facility > .date-block .slot").count() == 1
    assert page.locator(".facility > .date-block .error").count() == 1
    assert container.locator(".date-block").count() == 2
    assert container.is_hidden()

    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert toggle.inner_text() == "空きなしの日を隠す"
    assert container.is_visible()


def test_available_error_and_selector_pending_dates_remain_visible(page_loader) -> None:
    data = make_document(
        [
            make_date("2026-08-01", availability_count=1),
            make_date("2026-08-02", "error"),
            make_date("2026-08-03", "selector_pending"),
            make_date("2026-08-04"),
        ]
    )
    page, _, _, _ = page_loader(data)

    assert page.locator(".facility > .date-block").count() == 3
    assert page.locator(".facility > .date-block .slot").count() == 1
    assert page.locator(".facility > .date-block .error").count() == 1
    assert page.get_by_text("施設の取得設定を調整中です。").is_visible()
    assert page.locator(".empty-days-container .date-block").count() == 1


def test_no_empty_success_date_means_no_toggle(page_loader) -> None:
    data = make_document(
        [make_date("2026-08-01", availability_count=1), make_date("2026-08-02", "error")]
    )
    page, _, _, _ = page_loader(data)

    assert page.locator(".empty-days-toggle").count() == 0


def test_zero_total_uses_empty_summary_message(page_loader) -> None:
    data = make_document([make_date("2026-08-01")])
    page, _, _, _ = page_loader(data)

    assert page.locator("#total-availability").inner_text() == "現在、対象時間帯の空き候補はありません"


def test_fresh_data_hides_warning(page_loader) -> None:
    data = copy.deepcopy(AVAILABILITY)
    data["generated_at"] = datetime.now(JST).isoformat()
    page, _, _, _ = page_loader(data)

    assert page.locator("#freshness-warning").is_hidden()
    assert page.locator("#freshness-warning").get_attribute("data-level") == "fresh"


def test_stale_data_shows_two_hour_warning(page_loader) -> None:
    data = copy.deepcopy(AVAILABILITY)
    data["generated_at"] = (datetime.now(JST) - timedelta(minutes=121)).isoformat()
    page, _, _, _ = page_loader(data)

    warning = page.locator("#freshness-warning")
    assert warning.is_visible()
    assert warning.get_attribute("data-level") == "stale"
    assert "2時間以上更新されていません" in warning.inner_text()


def test_mobile_and_desktop_have_no_horizontal_overflow(page_loader) -> None:
    mobile, _, _, _ = page_loader(viewport={"width": 390, "height": 844})
    assert mobile.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")

    desktop, _, _, _ = page_loader(viewport={"width": 1280, "height": 900})
    assert desktop.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert desktop.locator("main").evaluate("element => element.getBoundingClientRect().width <= 720")


def test_reload_button_fetches_again_without_console_errors(page_loader) -> None:
    page, console_errors, page_errors, requests = page_loader()

    page.locator("#reload").click()
    page.locator("#reload:not([disabled])").wait_for()

    assert len(requests) == 2
    assert requests[0] != requests[1]
    assert console_errors == []
    assert page_errors == []
