import json
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scripts import scrape


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "kamoike_schedule.html"
SUMIZEI_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sumizei_schedule.html"
TOUKAI_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "toukai_schedule.html"
TARGET = scrape.TargetDay(date(2026, 8, 1), "weekend", None)
SECOND_TARGET = scrape.TargetDay(date(2026, 8, 2), "weekend", None)
RESERVATION_URL = scrape.KAMOIKE_URL_TEMPLATE.format(date="2026-08-01")
SUMIZEI_RESERVATION_URL = scrape.SUMIZEI_BASE_URL
TOUKAI_RESERVATION_URL = scrape.P_KASHIKAN_BASE_URL
CHECKED_AT = "2026-07-21T12:00:00+09:00"


def fixture_html() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def parsed_result(html: str | None = None) -> dict:
    return scrape.parse_kamoike_html(
        html or fixture_html(),
        TARGET,
        RESERVATION_URL,
        CHECKED_AT,
    )


def sumizei_fixture_html() -> str:
    return SUMIZEI_FIXTURE_PATH.read_text(encoding="utf-8")


def parsed_sumizei_result(html: str | None = None) -> dict:
    return scrape.parse_sumizei_html(
        html or sumizei_fixture_html(),
        TARGET,
        SUMIZEI_RESERVATION_URL,
        CHECKED_AT,
    )


def toukai_fixture_html() -> str:
    return TOUKAI_FIXTURE_PATH.read_text(encoding="utf-8")


def parsed_toukai_result(html: str | None = None) -> dict:
    return scrape.parse_p_kashikan_html(
        html or toukai_fixture_html(),
        TARGET,
        TOUKAI_RESERVATION_URL,
        CHECKED_AT,
        scrape.TOUKAI_FACILITY_ID,
        scrape.TOUKAI_FACILITY_NAME,
        scrape.TOUKAI_FACILITY_CODE,
    )


def test_generate_target_days_filters_to_weekends_and_holidays() -> None:
    targets = scrape.generate_target_days(date(2026, 7, 20), days=15)

    assert [target.date.isoformat() for target in targets] == [
        "2026-07-20",
        "2026-07-25",
        "2026-07-26",
        "2026-08-01",
        "2026-08-02",
    ]
    assert targets[0].holiday_name == "海の日"


def test_generate_target_days_rejects_empty_window() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        scrape.generate_target_days(date(2026, 7, 20), days=0)


def test_extracts_one_hour_available_slot() -> None:
    availability = parsed_result()["availability"]

    assert any(
        slot["court_name"] == "コートA"
        and slot["start_time"] == "12:00"
        and slot["end_time"] == "13:00"
        and slot["duration_minutes"] == 60
        for slot in availability
    )


def test_merges_two_consecutive_hours_for_same_court() -> None:
    availability = parsed_result()["availability"]

    merged = next(
        slot
        for slot in availability
        if slot["court_name"] == "コートA" and slot["start_time"] == "08:00"
    )
    assert merged["end_time"] == "10:00"
    assert merged["duration_minutes"] == 120


def test_excludes_slots_before_eight() -> None:
    assert all(slot["start_time"] >= "08:00" for slot in parsed_result()["availability"])


def test_excludes_slots_after_thirteen() -> None:
    assert all(slot["end_time"] <= "13:00" for slot in parsed_result()["availability"])


def test_excludes_reserved_and_unavailable_cells() -> None:
    availability = parsed_result()["availability"]

    assert len(availability) == 3
    assert all(slot["status"] == "available" for slot in availability)


def test_does_not_treat_legend_as_availability() -> None:
    availability = parsed_result()["availability"]

    assert all(slot["court_name"] in {"コートA", "コートB"} for slot in availability)


def test_keeps_multiple_courts_separate() -> None:
    availability = parsed_result()["availability"]

    assert {slot["court_name"] for slot in availability} == {"コートA", "コートB"}


def test_zero_availability_is_success() -> None:
    html = fixture_html().replace("rsv--result--yes", "rsv--result--no")
    html = html.replace('area-label="予約可"', 'area-label="予約済み"')

    result = parsed_result(html)

    assert result["status"] == "success"
    assert result["availability"] == []
    assert result["error_type"] is None


def test_dom_change_raises_unexpected_dom() -> None:
    html = """
    <div class="rsv__result" data-reserve="1">
      <section class="rsv__field">
        <h3 class="rsv__result__item"><em>コートA</em></h3>
        <ul class="rsv__result__time"><li>8:00</li><li>13:00</li></ul>
      </section>
    </div>
    """

    with pytest.raises(scrape.ScrapeStructureError) as error:
        parsed_result(html)

    assert error.value.error_type == "unexpected_dom"


def test_missing_schedule_raises_no_schedule_table() -> None:
    with pytest.raises(scrape.ScrapeStructureError) as error:
        parsed_result("<html><body>maintenance</body></html>")

    assert error.value.error_type == "no_schedule_table"


def test_duplicate_court_rows_are_deduplicated() -> None:
    availability = parsed_result()["availability"]

    court_b = [slot for slot in availability if slot["court_name"] == "コートB"]
    assert len(court_b) == 1
    assert court_b[0]["start_time"] == "09:00"
    assert court_b[0]["end_time"] == "11:00"


def test_slot_id_is_stable_and_uses_required_fields() -> None:
    first = parsed_result()["availability"][0]
    second = parsed_result()["availability"][0]

    expected = scrape.make_slot_id(
        first["facility_id"],
        first["date"],
        first["court_name"],
        first["start_time"],
        first["end_time"],
    )
    assert first["slot_id"] == expected == second["slot_id"]


def test_build_document_reflects_kamoike_availability(tmp_path: Path) -> None:
    client = FakePageClient(PageCaptureFactory.success(fixture_html()))
    kamoike = scrape.configured_facilities()[0]

    document = scrape.build_document(
        [TARGET],
        facilities=[kamoike],
        client_factory=lambda: client,
    )
    output = tmp_path / "availability.json"
    scrape.write_document(document, output)
    loaded = scrape.load_document(output)

    facility = loaded["facilities"][0]
    assert loaded["schema_version"] == 2
    assert facility["id"] == "kamoike-prefectural"
    assert facility["dates"][0]["status"] == "success"
    assert len(facility["dates"][0]["availability"]) == 3
    assert client.snapshot_name == "2026-08-01"


def test_scrape_failure_records_diagnostics() -> None:
    client = FakePageClient(
        scrape.PageCapture(
            html="<html></html>",
            checked_at=CHECKED_AT,
            response_status=None,
            error_type="navigation_timeout",
            error_message="timed out",
        )
    )
    facility = scrape.configured_facilities()[0]

    result = scrape.scrape_kamoike(client, facility, TARGET)

    assert result["status"] == "error"
    assert result["error_type"] == "navigation_timeout"
    assert result["error_message"] == "timed out"
    assert result["checked_at"] == CHECKED_AT
    assert result["reservation_url"] == RESERVATION_URL
    assert result["availability"] == []


def test_comparable_document_ignores_check_timestamps() -> None:
    previous = {
        "generated_at": "2026-07-20T10:00:00+09:00",
        "facilities": [{"dates": [{"checked_at": "old", "availability": []}]}],
    }
    current = {
        "generated_at": "2026-07-21T10:00:00+09:00",
        "facilities": [{"dates": [{"checked_at": "new", "availability": []}]}],
    }

    assert scrape.comparable_document(previous) == scrape.comparable_document(current)


def test_write_availability_outputs_keeps_artifact_and_respects_dry_run(
    tmp_path: Path,
) -> None:
    previous = scrape.empty_document()
    current = scrape.empty_document()
    current["generated_at"] = CHECKED_AT
    current["facilities"] = [{"id": "test", "name": "Test", "dates": []}]
    data_path = tmp_path / "data" / "availability.json"
    output_directory = tmp_path / "run-output"

    written = scrape.write_availability_outputs(
        previous,
        current,
        dry_run=True,
        data_path=data_path,
        output_directory=output_directory,
    )

    assert written is False
    assert not data_path.exists()
    assert scrape.load_document(output_directory / "availability.json") == current
    assert not (output_directory / "notification-state.json").exists()

    written = scrape.write_availability_outputs(
        previous,
        current,
        dry_run=False,
        data_path=data_path,
        output_directory=output_directory,
    )

    assert written is True
    assert scrape.load_document(data_path) == current


def test_sumizei_extracts_one_hour_available_slot() -> None:
    availability = parsed_sumizei_result()["availability"]

    assert any(
        slot["court_name"] == "テニスコート1"
        and slot["start_time"] == "12:00"
        and slot["end_time"] == "13:00"
        and slot["duration_minutes"] == 60
        for slot in availability
    )


def test_sumizei_merges_consecutive_slots() -> None:
    slot = next(
        slot
        for slot in parsed_sumizei_result()["availability"]
        if slot["court_name"] == "テニスコート1" and slot["start_time"] == "08:00"
    )

    assert slot["end_time"] == "10:00"
    assert slot["duration_minutes"] == 120


def test_sumizei_keeps_multiple_courts_separate() -> None:
    courts = {slot["court_name"] for slot in parsed_sumizei_result()["availability"]}

    assert courts == {"テニスコート1", "テニスコート2"}


def test_sumizei_excludes_slots_outside_monitor_window() -> None:
    availability = parsed_sumizei_result()["availability"]

    assert all(slot["start_time"] >= "08:00" for slot in availability)
    assert all(slot["end_time"] <= "13:00" for slot in availability)
    assert not any(slot["start_time"] == "12:30" for slot in availability)


def test_sumizei_excludes_unavailable_cells_and_legend() -> None:
    availability = parsed_sumizei_result()["availability"]

    assert len(availability) == 3
    assert all(slot["court_name"].startswith("テニスコート") for slot in availability)
    assert all(slot["status"] == "available" for slot in availability)


def test_sumizei_zero_availability_is_success() -> None:
    html = sumizei_fixture_html().replace(">○<", ">×<")

    result = parsed_sumizei_result(html)

    assert result["status"] == "success"
    assert result["availability"] == []


def test_sumizei_facility_not_found_is_recorded() -> None:
    capture = scrape.PageCapture(
        html="<html></html>",
        checked_at=CHECKED_AT,
        response_status=200,
        error_type="facility_not_found",
        error_message="facility code 029 was not found",
    )
    client = FakePageClient(PageCaptureFactory.success(fixture_html()), capture)
    facility = scrape.configured_facilities()[1]

    result = scrape.scrape_sumizei(client, facility, TARGET)

    assert result["status"] == "error"
    assert result["error_type"] == "facility_not_found"
    assert result["availability"] == []


def test_sumizei_dom_change_raises_unexpected_dom() -> None:
    html = sumizei_fixture_html().replace(' class="header"', "")

    with pytest.raises(scrape.ScrapeStructureError) as error:
        parsed_sumizei_result(html)

    assert error.value.error_type == "unexpected_dom"


def test_sumizei_duplicate_slots_are_removed() -> None:
    court_two = [
        slot
        for slot in parsed_sumizei_result()["availability"]
        if slot["court_name"] == "テニスコート2"
    ]

    assert len(court_two) == 1
    assert court_two[0]["start_time"] == "09:00"
    assert court_two[0]["end_time"] == "10:00"


def test_sumizei_slot_id_is_stable() -> None:
    first = parsed_sumizei_result()["availability"][0]
    second = parsed_sumizei_result()["availability"][0]

    assert first["slot_id"] == second["slot_id"] == scrape.make_slot_id(
        first["facility_id"],
        first["date"],
        first["court_name"],
        first["start_time"],
        first["end_time"],
    )


@pytest.mark.parametrize(
    ("encoded_time", "expected_start", "expected_end"),
    [
        ("10591159", "11:00", "12:00"),
        ("11591259", "12:00", "13:00"),
        ("08290859", "08:30", "09:00"),
        ("09001000", "09:00", "10:00"),
        ("08300930", "08:30", "09:30"),
    ],
)
def test_p_kashikan_internal_times_match_displayed_boundaries(
    encoded_time: str,
    expected_start: str,
    expected_end: str,
) -> None:
    element = BeautifulSoup(
        "<td onmousedown=\"setAppStatus("
        f"'029|001|01', '2026/08/01', 0, '{encoded_time}', '', '');"
        '\">○</td>',
        "html.parser",
    ).td

    start, end = scrape._p_kashikan_cell_minutes(
        element,
        scrape.clock_to_minutes("08:00"),
        scrape.clock_to_minutes("09:00"),
        TARGET,
        scrape.SUMIZEI_FACILITY_CODE,
        scrape.SUMIZEI_FACILITY_NAME,
    )

    assert scrape.minutes_to_clock(start) == expected_start
    assert scrape.minutes_to_clock(end) == expected_end


def test_p_kashikan_inferred_times_match_displayed_boundaries() -> None:
    element = BeautifulSoup("<td>●</td>", "html.parser").td

    start, end = scrape._p_kashikan_cell_minutes(
        element,
        scrape.clock_to_minutes("10:59"),
        scrape.clock_to_minutes("11:59"),
        TARGET,
        scrape.SUMIZEI_FACILITY_CODE,
        scrape.SUMIZEI_FACILITY_NAME,
    )

    assert scrape.minutes_to_clock(start) == "11:00"
    assert scrape.minutes_to_clock(end) == "12:00"


def test_p_kashikan_merges_slots_after_boundary_normalization() -> None:
    html = toukai_fixture_html()
    html = html.replace("'11001200'", "'10591159'", 1)
    html = html.replace("'12001300'", "'11591259'", 1)

    availability = parsed_toukai_result(html)["availability"]

    assert any(
        slot["court_name"] == "Aコート(ナイターあり)"
        and slot["start_time"] == "11:00"
        and slot["end_time"] == "13:00"
        and slot["duration_minutes"] == 120
        for slot in availability
    )
    assert all(
        ":59" not in slot["start_time"] and ":59" not in slot["end_time"]
        for slot in availability
    )


def test_p_kashikan_boundary_normalization_does_not_change_kamoike() -> None:
    availability = parsed_result()["availability"]

    assert {
        (slot["court_name"], slot["start_time"], slot["end_time"])
        for slot in availability
    } == {
        ("コートA", "08:00", "10:00"),
        ("コートA", "12:00", "13:00"),
        ("コートB", "09:00", "11:00"),
    }


def test_toukai_extracts_multiple_courts_and_boundary_slots() -> None:
    availability = parsed_toukai_result()["availability"]
    courts = {slot["court_name"] for slot in availability}

    assert courts == {
        "Aコート(ナイターあり)",
        "Bコート(ナイターなし)",
        "C・Dコート(ナイターあり)",
    }
    assert any(
        slot["court_name"] == "Aコート(ナイターあり)"
        and slot["start_time"] == "08:30"
        and slot["end_time"] == "10:00"
        for slot in availability
    )
    assert any(
        slot["court_name"] == "Aコート(ナイターあり)"
        and slot["start_time"] == "11:00"
        and slot["end_time"] == "13:00"
        for slot in availability
    )


def test_toukai_excludes_a_standalone_thirty_minute_slot() -> None:
    availability = parsed_toukai_result()["availability"]

    assert not any(
        slot["court_name"] == "Bコート(ナイターなし)"
        and slot["start_time"] == "08:30"
        for slot in availability
    )
    assert all(slot["duration_minutes"] >= 60 for slot in availability)


def test_toukai_does_not_merge_different_resource_rows() -> None:
    availability = [
        slot
        for slot in parsed_toukai_result()["availability"]
        if slot["court_name"] == "C・Dコート(ナイターあり)"
    ]

    assert any(slot["start_time"] == "11:00" and slot["end_time"] == "12:00" for slot in availability)
    assert any(slot["start_time"] == "12:00" and slot["end_time"] == "13:00" for slot in availability)
    assert not any(slot["start_time"] == "11:00" and slot["end_time"] == "13:00" for slot in availability)


def test_configured_facilities_include_both_p_kashikan_facilities() -> None:
    facilities = {facility.id: facility for facility in scrape.configured_facilities()}

    assert set(facilities) == {
        "kamoike-prefectural",
        "sumizei",
        "toukai-tennis",
    }
    assert facilities["sumizei"].p_kashikan_code == "029"
    assert facilities["toukai-tennis"].name == "東開庭球場"
    assert facilities["toukai-tennis"].p_kashikan_code == "131"
    assert all(
        not hasattr(facility, "notification_enabled")
        for facility in facilities.values()
    )


def test_build_document_integrates_all_three_facilities() -> None:
    client = FakePageClient(
        PageCaptureFactory.success(fixture_html()),
        PageCaptureFactory.success(sumizei_fixture_html()),
        PageCaptureFactory.success(toukai_fixture_html()),
    )

    document = scrape.build_document(
        [TARGET],
        facilities=scrape.configured_facilities(),
        client_factory=lambda: client,
    )

    facilities = {facility["id"]: facility for facility in document["facilities"]}
    assert set(facilities) == {
        "kamoike-prefectural",
        "sumizei",
        "toukai-tennis",
    }
    assert all("notification_enabled" not in facility for facility in facilities.values())
    assert facilities["kamoike-prefectural"]["dates"][0]["status"] == "success"
    assert facilities["sumizei"]["dates"][0]["status"] == "success"
    assert len(facilities["sumizei"]["dates"][0]["availability"]) == 3
    assert facilities["toukai-tennis"]["dates"][0]["status"] == "success"
    assert facilities["toukai-tennis"]["dates"][0]["availability"]


def test_one_facility_failure_preserves_other_facility_result() -> None:
    sumizei_error = scrape.PageCapture(
        html="<html></html>",
        checked_at=CHECKED_AT,
        response_status=None,
        error_type="navigation_timeout",
        error_message="timed out",
    )
    client = FakePageClient(
        PageCaptureFactory.success(fixture_html()),
        sumizei_error,
        PageCaptureFactory.success(toukai_fixture_html()),
    )

    document = scrape.build_document(
        [TARGET],
        facilities=scrape.configured_facilities(),
        client_factory=lambda: client,
    )

    facilities = {facility["id"]: facility for facility in document["facilities"]}
    assert facilities["kamoike-prefectural"]["dates"][0]["status"] == "success"
    assert len(facilities["kamoike-prefectural"]["dates"][0]["availability"]) == 3
    assert facilities["sumizei"]["dates"][0]["status"] == "error"
    assert facilities["toukai-tennis"]["dates"][0]["status"] == "success"


def test_toukai_failure_does_not_stop_existing_facilities() -> None:
    toukai_error = scrape.PageCapture(
        html="<html></html>",
        checked_at=CHECKED_AT,
        response_status=None,
        error_type="facility_not_found",
        error_message="facility code 131 was not found",
    )
    client = FakePageClient(
        PageCaptureFactory.success(fixture_html()),
        PageCaptureFactory.success(sumizei_fixture_html()),
        toukai_error,
    )

    document = scrape.build_document(
        [TARGET],
        facilities=scrape.configured_facilities(),
        client_factory=lambda: client,
    )

    facilities = {facility["id"]: facility for facility in document["facilities"]}
    assert facilities["kamoike-prefectural"]["dates"][0]["status"] == "success"
    assert facilities["sumizei"]["dates"][0]["status"] == "success"
    assert facilities["toukai-tennis"]["dates"][0]["status"] == "error"
    assert (
        facilities["toukai-tennis"]["dates"][0]["error_type"]
        == "facility_not_found"
    )


def test_p_kashikan_403_stops_remaining_requests_but_kamoike_continues() -> None:
    denied = scrape.PageCapture(
        html="<html><head><title>403 Forbidden</title></head></html>",
        checked_at=CHECKED_AT,
        response_status=403,
        error_type="access_denied",
        error_message="Access denied (HTTP 403)",
    )
    client = FakePageClient(
        PageCaptureFactory.success(fixture_html()),
        denied,
        PageCaptureFactory.success(toukai_fixture_html()),
    )
    facilities = {facility.id: facility for facility in scrape.configured_facilities()}

    document = scrape.build_document(
        [TARGET, SECOND_TARGET],
        facilities=[
            facilities["sumizei"],
            facilities["toukai-tennis"],
            facilities["kamoike-prefectural"],
        ],
        client_factory=lambda: client,
    )

    results = {facility["id"]: facility for facility in document["facilities"]}
    assert client.p_kashikan_calls == [
        (scrape.SUMIZEI_FACILITY_CODE, TARGET.date)
    ]
    assert len(client.page_calls) == 2
    assert all(
        entry["status"] == "error" and entry["http_status"] == 403
        for facility_id in ("sumizei", "toukai-tennis")
        for entry in results[facility_id]["dates"]
    )
    assert all(
        entry["status"] == "success"
        for entry in results["kamoike-prefectural"]["dates"]
    )


def test_p_kashikan_403_preserves_previous_successful_data() -> None:
    denied = scrape.PageCapture(
        html="<html><head><title>403 Forbidden</title></head></html>",
        checked_at=CHECKED_AT,
        response_status=403,
        error_type="access_denied",
        error_message="Access denied (HTTP 403)",
    )
    client = FakePageClient(
        PageCaptureFactory.success(fixture_html()),
        denied,
        PageCaptureFactory.success(toukai_fixture_html()),
    )
    facilities = {facility.id: facility for facility in scrape.configured_facilities()}
    previous = scrape.empty_document()
    previous["facilities"] = [
        {
            "id": "sumizei",
            "name": scrape.SUMIZEI_FACILITY_NAME,
            "dates": [parsed_sumizei_result()],
        },
        {
            "id": "toukai-tennis",
            "name": scrape.TOUKAI_FACILITY_NAME,
            "dates": [parsed_toukai_result()],
        },
    ]

    document = scrape.build_document(
        [TARGET],
        facilities=[facilities["sumizei"], facilities["toukai-tennis"]],
        client_factory=lambda: client,
        previous_document=previous,
    )

    results = {facility["id"]: facility["dates"][0] for facility in document["facilities"]}
    for facility_id in ("sumizei", "toukai-tennis"):
        previous_entry = next(
            facility["dates"][0]
            for facility in previous["facilities"]
            if facility["id"] == facility_id
        )
        result = results[facility_id]
        assert result["status"] == "error"
        assert result["error_type"] == "access_denied"
        assert result["http_status"] == 403
        assert result["fallback_from_previous"] is True
        assert result["last_success_checked_at"] == CHECKED_AT
        assert result["availability"] == previous_entry["availability"]


def test_diagnostic_headers_redact_secrets_and_cookie_values() -> None:
    safe, cookie_names = scrape._sanitize_headers(
        {
            "Accept-Language": "ja-JP",
            "Cookie": "session=private-cookie; tracking=private-tracking",
            "Set-Cookie": "server_session=private-server-cookie; Path=/",
            "Authorization": "Bearer private-token",
            "X-Api-Key": "private-api-key",
        }
    )
    serialized = json.dumps(safe)

    assert safe["accept-language"] == "ja-JP"
    assert safe["cookie"] == "<redacted>"
    assert safe["set-cookie"] == "<redacted>"
    assert safe["authorization"] == "<redacted>"
    assert safe["x-api-key"] == "<redacted>"
    assert {"session", "tracking", "server_session"} <= set(cookie_names)
    assert "private-cookie" not in serialized
    assert "private-tracking" not in serialized
    assert "private-server-cookie" not in serialized
    assert "private-token" not in serialized
    assert "private-api-key" not in serialized


def test_p_kashikan_browser_context_uses_normal_settings_and_is_reused() -> None:
    class FakeContext:
        pass

    class FakeBrowser:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.context = FakeContext()

        def new_context(self, **options):
            self.calls.append(options)
            return self.context

    class FakePlaywright:
        devices = {
            "Desktop Chrome": {
                "user_agent": "Desktop Chrome User-Agent",
                "viewport": {"width": 1280, "height": 720},
                "device_scale_factor": 1,
                "is_mobile": False,
                "has_touch": False,
                "default_browser_type": "chromium",
            }
        }

    client = scrape.PlaywrightClient()
    browser = FakeBrowser()
    client._browser = browser
    client._playwright = FakePlaywright()

    first = client._get_p_kashikan_context()
    second = client._get_p_kashikan_context()

    assert first is second is browser.context
    assert len(browser.calls) == 1
    options = browser.calls[0]
    assert options["user_agent"] == "Desktop Chrome User-Agent"
    assert options["locale"] == "ja-JP"
    assert options["timezone_id"] == "Asia/Tokyo"
    assert options["viewport"] == scrape.P_KASHIKAN_VIEWPORT
    assert options["java_script_enabled"] is True
    assert (
        options["extra_http_headers"]["Accept-Language"]
        == scrape.P_KASHIKAN_ACCEPT_LANGUAGE
    )
    assert "default_browser_type" not in options


def test_p_kashikan_diagnostic_file_and_log_do_not_expose_secrets(
    tmp_path: Path,
    capsys,
) -> None:
    class FakeRequest:
        url = scrape.P_KASHIKAN_BASE_URL

        @staticmethod
        def all_headers() -> dict[str, str]:
            return {
                "accept-language": "ja-JP",
                "cookie": "session=private-request-cookie",
                "authorization": "Bearer private-token",
            }

    class FakeDiagnosticResponse:
        status = 403
        request = FakeRequest()

        @staticmethod
        def all_headers() -> dict[str, str]:
            return {
                "content-type": "text/html",
                "set-cookie": "session=private-response-cookie; Path=/",
            }

    class FakeDiagnosticPage:
        url = scrape.P_KASHIKAN_BASE_URL

        @staticmethod
        def title() -> str:
            return "403 Forbidden"

        @staticmethod
        def evaluate(expression: str):
            if expression == "navigator.userAgent":
                return "Desktop Chrome"
            if expression == "navigator.webdriver":
                return True
            raise AssertionError(expression)

    class FakeDiagnosticContext:
        @staticmethod
        def cookies() -> list[dict[str, str]]:
            return [{"name": "session", "value": "private-context-cookie"}]

    class FakeBrowser:
        version = "149.0.0.0"

    client = scrape.PlaywrightClient()
    client._browser = FakeBrowser()
    html = "<html><title>403 Forbidden</title><body>Cloudflare Bot Request ID</body></html>"

    path = client._save_p_kashikan_diagnostics(
        FakeDiagnosticPage(),
        FakeDiagnosticContext(),
        FakeDiagnosticResponse(),
        html,
        tmp_path,
        "probe",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    emitted = capsys.readouterr().out
    serialized = json.dumps(payload)
    assert payload["response"]["status"] == 403
    assert payload["response"]["title"] == "403 Forbidden"
    assert payload["browser"]["navigator_webdriver"] is True
    assert payload["cookie_names"] == ["session"]
    assert payload["response"]["matched_restriction_markers"] == [
        "Forbidden",
        "Cloudflare",
        "Bot",
        "Request ID",
    ]
    for secret in (
        "private-request-cookie",
        "private-response-cookie",
        "private-context-cookie",
        "private-token",
    ):
        assert secret not in serialized
        assert secret not in emitted


class PageCaptureFactory:
    @staticmethod
    def success(html: str) -> scrape.PageCapture:
        return scrape.PageCapture(
            html=html,
            checked_at=CHECKED_AT,
            response_status=200,
        )


class FakePageClient:
    def __init__(
        self,
        capture: scrape.PageCapture,
        sumizei_capture: scrape.PageCapture | None = None,
        toukai_capture: scrape.PageCapture | None = None,
    ) -> None:
        self.capture = capture
        self.sumizei_capture = sumizei_capture or capture
        self.toukai_capture = toukai_capture or capture
        self.snapshot_directory: Path | None = None
        self.snapshot_name = ""
        self.page_calls: list[tuple[str, str]] = []
        self.p_kashikan_calls: list[tuple[str, date]] = []

    def __enter__(self) -> "FakePageClient":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def capture_page(
        self,
        url: str,
        snapshot_directory: Path,
        snapshot_name: str,
    ) -> scrape.PageCapture:
        self.snapshot_directory = snapshot_directory
        self.snapshot_name = snapshot_name
        self.page_calls.append((url, snapshot_name))
        return self.capture

    def extract_texts(self, url: str, selector: str) -> list[str]:
        return []

    def capture_p_kashikan_schedule(
        self,
        reservation_url: str,
        target_date: date,
        snapshot_directory: Path,
        facility_code: str,
        facility_name: str,
    ) -> scrape.PageCapture:
        self.snapshot_directory = snapshot_directory
        self.snapshot_name = target_date.isoformat()
        self.p_kashikan_calls.append((facility_code, target_date))
        if facility_code == scrape.SUMIZEI_FACILITY_CODE:
            return self.sumizei_capture
        if facility_code == scrape.TOUKAI_FACILITY_CODE:
            return self.toukai_capture
        raise AssertionError(f"Unexpected P-Kashikan facility: {facility_name}")
