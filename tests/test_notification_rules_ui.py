from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup


ROOT = Path(__file__).parents[1]
PAGE_PATH = ROOT / "account/notifications.html"
ACCOUNT_PATH = ROOT / "account/index.html"
SCRIPT_PATH = ROOT / "assets/js/notification-rules.js"
DESIGN_PATH = ROOT / "docs/PHASE2_NOTIFICATION_RULES_DESIGN.md"
ROADMAP_PATH = ROOT / "docs/DEVELOPMENT_ROADMAP.md"
SERVICE_SPEC_PATH = ROOT / "docs/SERVICE_SPECIFICATION.md"
README_PATH = ROOT / "README.md"
MIGRATION_PATH = (
    ROOT
    / "supabase/migrations"
    / "20260807100000_add_notification_rule_save_rpc.sql"
)
SUPABASE_JS_URL = (
    "https://cdn.jsdelivr.net/npm/"
    "@supabase/supabase-js@2.106.2/dist/umd/supabase.js"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def compact(value: str) -> str:
    without_comments = re.sub(r"--.*?$", "", value, flags=re.MULTILINE)
    return " ".join(without_comments.split())


def script_section(script: str, start: str, end: str) -> str:
    start_index = script.index(start)
    end_index = script.index(end, start_index)
    return script[start_index:end_index]


def test_notification_page_has_required_form_controls_and_accessibility() -> None:
    soup = BeautifulSoup(read(PAGE_PATH), "html.parser")

    assert soup.body["data-page"] == "notification-rules"
    scripts = [script["src"] for script in soup.find_all("script", src=True)]
    assert "../assets/config/auth-config.js" in scripts
    assert SUPABASE_JS_URL in scripts
    assert "../assets/js/notification-rules.js" in scripts

    assert soup.find(attrs={"data-notification-loading": True})["aria-live"] == (
        "polite"
    )
    assert soup.find(attrs={"data-notification-status": True})["aria-live"] == (
        "polite"
    )
    assert soup.find(attrs={"data-membership-required": True}).has_attr("hidden")
    assert soup.find(attrs={"data-notification-content": True}).has_attr("hidden")
    assert soup.find(attrs={"data-notification-rule-list": True})
    assert soup.find(attrs={"data-notification-empty": True}).has_attr("hidden")

    form = soup.find("form", attrs={"data-notification-rule-form": True})
    assert form
    assert not form.get("action")
    assert form.has_attr("novalidate")

    name = form.find("input", attrs={"data-rule-name": True})
    assert name["name"] == "name"
    assert name["required"] == ""
    assert name["maxlength"] == "80"
    assert not form.find(attrs={"name": "user_id"})

    facility_fieldset = form.find(
        "fieldset", attrs={"data-facility-fieldset": True}
    )
    assert facility_fieldset.find("legend").get_text(strip=True) == "施設"
    assert facility_fieldset.find(attrs={"data-facility-options": True})

    weekday_fieldset = form.find(
        "fieldset", attrs={"data-weekday-fieldset": True}
    )
    assert weekday_fieldset.find("legend").get_text(strip=True) == "曜日"
    weekdays = weekday_fieldset.find_all(
        "input", attrs={"data-rule-weekday": True}
    )
    assert [weekday["value"] for weekday in weekdays] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
    ]

    assert form.find("input", attrs={"data-rule-date-from": True})["type"] == (
        "date"
    )
    assert form.find("input", attrs={"data-rule-date-to": True})["type"] == (
        "date"
    )
    start_time = form.find("input", attrs={"data-rule-start-time": True})
    end_time = form.find("input", attrs={"data-rule-end-time": True})
    duration = form.find(
        "input", attrs={"data-rule-minimum-duration": True}
    )
    assert (start_time["type"], start_time["value"]) == ("time", "09:00")
    assert (end_time["type"], end_time["value"]) == ("time", "21:00")
    assert duration["value"] == "60"
    assert duration["min"] == "30"
    assert duration["max"] == "720"
    assert duration["step"] == "30"
    assert not form.find("input", attrs={"data-rule-enabled": True}).has_attr(
        "checked"
    )
    assert form.find("button", attrs={"data-save-rule": True})["type"] == "submit"
    assert form.find("button", attrs={"data-cancel-rule": True})["type"] == (
        "button"
    )
    assert form.find(attrs={"data-form-errors": True})["aria-live"] == "polite"


def test_notification_page_has_rule_count_and_limit_guidance() -> None:
    soup = BeautifulSoup(read(PAGE_PATH), "html.parser")
    rule_count = soup.find(attrs={"data-notification-rule-count": True})
    limit_guidance = soup.find(
        attrs={"data-notification-rule-limit": True}
    )
    new_rule_button = soup.find("button", attrs={"data-new-rule": True})

    assert rule_count
    assert rule_count.get_text(" ", strip=True) == "登録済み 0 / 5件"
    assert rule_count["aria-live"] == "polite"
    assert limit_guidance
    assert limit_guidance.has_attr("hidden")
    assert limit_guidance["aria-live"] == "polite"
    assert limit_guidance.get_text(" ", strip=True) == (
        "通知条件は最大5件まで登録できます。"
        "追加するには既存の条件を削除してください。"
    )
    assert new_rule_button.get_text(" ", strip=True) == "新しい通知条件"


def test_account_and_notification_pages_link_to_each_other_and_availability() -> None:
    account = BeautifulSoup(read(ACCOUNT_PATH), "html.parser")
    notifications = BeautifulSoup(read(PAGE_PATH), "html.parser")

    account_links = account.find_all("a", href="notifications.html")
    assert account_links
    assert any("通知条件" in link.get_text(strip=True) for link in account_links)
    assert notifications.find("a", href="index.html")
    assert notifications.find("a", href="../index.html")


def test_notification_page_explains_the_current_acquisition_scope() -> None:
    notifications = BeautifulSoup(read(PAGE_PATH), "html.parser")
    guidance = notifications.find(
        class_="notice",
        attrs={"aria-label": "現在の空き取得範囲"},
    )
    assert guidance
    text = guidance.get_text(" ", strip=True)

    for expected in (
        "土日・日本の祝日",
        "8:00〜13:00",
        "60分以上",
        "平日",
        "時間外",
        "60分未満",
        "保存できます",
        "一致しません",
        "実際の日付の曜日",
    ):
        assert expected in text
    assert not notifications.find("input", attrs={"name": "holiday"})


def test_notification_script_uses_existing_auth_contract_and_session_identity() -> None:
    script = read(SCRIPT_PATH)

    assert 'flowType: "pkce"' in script
    assert "persistSession: true" in script
    assert "autoRefreshToken: true" in script
    assert "client.auth.getSession()" in script
    assert "session.user.id" in script
    assert "userId = session.user.id" in script
    assert 'window.location.replace(LOGIN_PATH)' in script
    assert '.from("profiles")' in script
    assert '.select("membership_status")' in script
    assert 'membership_status !== "active"' in script
    assert "membershipRequired.hidden = false" in script
    assert "URLSearchParams" not in script

    for table in (
        "notification_rules",
        "notification_rule_facilities",
        "notification_rule_weekdays",
        "facilities",
    ):
        assert f'.from("{table}")' in script
    assert '.eq("user_id", userId)' in script


def test_notification_script_has_no_browser_secret_or_unsafe_html_sink() -> None:
    script = read(SCRIPT_PATH)
    lowered = script.lower()

    assert "innerHTML" not in script
    assert "service_role" not in lowered
    assert "sb_secret_" not in lowered
    assert "private key" not in lowered
    assert "access_token" not in lowered
    assert "refresh_token" not in lowered
    assert "console." not in script
    assert "document.createElement" in script
    assert ".textContent" in script


def test_notification_script_validates_every_form_rule_in_japanese() -> None:
    script = read(SCRIPT_PATH)

    for message in (
        "条件名を入力してください。",
        "条件名は80文字以内で入力してください。",
        "施設を1件以上選択してください。",
        "曜日を1件以上選択してください。",
        "開始時刻は終了時刻より前にしてください。",
        "開始日は終了日以前の日付にしてください。",
        "最低連続時間は30〜720分の範囲で、30分単位で入力してください。",
    ):
        assert message in script

    assert "trimmedName.length > 80" in script
    assert "selectedFacilities.length < 1" in script
    assert "selectedWeekdays.length < 1" in script
    assert "startMinutes >= endMinutes" in script
    assert "dateFromInput.value > dateToInput.value" in script
    assert "minimumDuration < 30" in script
    assert "minimumDuration > 720" in script
    assert "minimumDuration % 30 !== 0" in script


def test_notification_script_rejects_date_ranges_without_selected_weekdays() -> None:
    script = read(SCRIPT_PATH)
    helper = script_section(
        script,
        "function parseIsoDateUtc",
        "function validateRuleForm",
    )
    validation = script_section(
        script,
        "function validateRuleForm",
        "function normalizeTimeInput",
    )

    assert "function dateRangeContainsSelectedWeekday" in helper
    assert "setUTCFullYear" in helper
    assert ".getUTCDay()" in helper
    assert ".getDay()" not in helper
    assert "weekday === 0 ? 7 : weekday" in helper
    assert "Math.min(spanDays, 6)" in helper
    assert (
        "対象期間内に選択した曜日がありません。"
        "日付または曜日を変更してください。"
    ) in validation
    assert "!dateRangeContainsSelectedWeekday(" in validation
    assert "[dateFromInput, dateToInput, weekdayFieldset]" in validation
    assert validation.index("dateFromInput.value > dateToInput.value") < (
        validation.index("!dateRangeContainsSelectedWeekday(")
    )

def test_notification_script_uses_atomic_rpc_and_scoped_direct_mutations() -> None:
    script = read(SCRIPT_PATH)

    assert 'client.rpc(\n        "save_notification_rule"' in script
    for parameter in (
        "p_rule_id",
        "p_name",
        "p_is_enabled",
        "p_date_from",
        "p_date_to",
        "p_start_time",
        "p_end_time",
        "p_minimum_duration_minutes",
        "p_facility_ids",
        "p_weekdays",
    ):
        assert parameter in script
    assert "p_user_id" not in script

    assert '.update({ is_enabled: !rule.is_enabled })' in script
    assert '.delete()' in script
    assert '.eq("id", rule.id)' in script
    assert '.eq("user_id", userId)' in script
    assert "window.confirm(" in script
    assert "formSubmitting" in script
    assert "mutationBusy" in script
    assert "control.disabled = isBusy" in script


def test_notification_script_displays_and_enforces_the_five_rule_ui_limit() -> None:
    script = read(SCRIPT_PATH)
    availability = script_section(
        script,
        "function updateActionAvailability",
        "function setMutationBusy",
    )
    open_form = script_section(
        script,
        "function openRuleForm",
        "function closeRuleForm",
    )
    save = script_section(
        script,
        "async function saveRule",
        "async function start",
    )

    assert "const MAX_NOTIFICATION_RULES = 5;" in script
    assert (
        "`登録済み ${rules.length} / ${MAX_NOTIFICATION_RULES}件`"
        in script
    )
    assert (
        "ruleLimitGuidance.hidden = !hasReachedNotificationRuleLimit();"
        in script
    )
    assert "hasReachedNotificationRuleLimit()" in availability
    assert "newRuleButton.disabled" in availability

    action_loop = availability[
        availability.index(
            'for (const button of ruleList.querySelectorAll("[data-rule-action]"))'
        ):
    ]
    assert "button.disabled = disableListActions;" in action_loop
    assert "MAX_NOTIFICATION_RULES" not in action_loop
    assert "hasReachedNotificationRuleLimit" not in action_loop

    assert "if (!rule && hasReachedNotificationRuleLimit())" in open_form
    assert "NOTIFICATION_RULE_LIMIT_MESSAGE" in open_form
    assert "const wasEditing = editingRuleId !== null;" in save
    assert "if (!wasEditing && hasReachedNotificationRuleLimit())" in save
    assert save.index("const wasEditing = editingRuleId !== null;") < (
        save.index('client.rpc(\n        "save_notification_rule"')
    )


def test_limit_error_is_translated_and_refreshes_the_actual_rule_count() -> None:
    script = read(SCRIPT_PATH)
    refresh = script_section(
        script,
        "async function refreshNotificationDataAfterMutation",
        "function isNotificationRuleLimitError",
    )
    error_check = script_section(
        script,
        "function isNotificationRuleLimitError",
        "function canEnableRule",
    )
    save = script_section(
        script,
        "async function saveRule",
        "async function start",
    )
    japanese_message = (
        "通知条件は最大5件まで登録できます。"
        "追加するには既存の条件を削除してください。"
    )

    assert japanese_message in script
    assert "Notification rule limit of 5 has been reached." in script
    assert "error.message.includes(NOTIFICATION_RULE_LIMIT_DB_MESSAGE)" in (
        error_check
    )
    assert "await loadNotificationData()" in refresh
    assert (
        "保存は完了しておらず、一覧を再読み込みできませんでした。"
        in refresh
    )
    assert "ページを再読み込みしてください。" in refresh
    assert "操作を繰り返さないでください。" in refresh
    assert "if (result && isNotificationRuleLimitError(result.error))" in save
    assert (
        "await refreshNotificationDataAfterMutation(\n"
        "          NOTIFICATION_RULE_LIMIT_MESSAGE,\n"
        "          false,"
    ) in save
    assert "formOpen = false;" in save
    assert save.index("await refreshNotificationDataAfterMutation(") < (
        save.index("setFormBusy(false);")
    )


def test_delete_refresh_reenables_creation_when_rule_count_drops() -> None:
    script = read(SCRIPT_PATH)
    delete = script_section(
        script,
        "async function deleteRule",
        "async function saveRule",
    )
    render = script_section(
        script,
        "function renderRules",
        "function renderFacilityOptions",
    )

    assert 'await refreshNotificationDataAfterMutation("通知条件を削除しました。")' in (
        delete
    )
    assert "renderRules();" in script_section(
        script,
        "async function loadNotificationData",
        "async function refreshNotificationDataAfterMutation",
    )
    assert "updateActionAvailability();" in render
    assert "rules.length >= MAX_NOTIFICATION_RULES" in script


def test_mutation_failure_and_post_success_refresh_failure_are_distinct() -> None:
    script = read(SCRIPT_PATH)
    refresh_helper = script_section(
        script,
        "async function refreshNotificationDataAfterMutation",
        "function canEnableRule",
    )
    operations = {
        "toggle": script_section(
            script,
            "async function toggleRule",
            "async function deleteRule",
        ),
        "delete": script_section(
            script,
            "async function deleteRule",
            "async function saveRule",
        ),
        "save": script_section(
            script,
            "async function saveRule",
            "async function start",
        ),
    }

    assert "await loadNotificationData()" in refresh_helper
    assert "変更は完了しましたが、一覧を再読み込みできませんでした。" in (
        refresh_helper
    )
    assert "ページを再読み込みしてください。" in refresh_helper
    assert "操作を繰り返さないでください。" in refresh_helper
    assert "refreshFailed = true" in refresh_helper
    assert "通知条件を保存できませんでした" not in refresh_helper
    assert "通知条件の状態を変更できませんでした" not in refresh_helper
    assert "通知条件を削除できませんでした" not in refresh_helper

    for operation in operations.values():
        assert "await loadNotificationData()" not in operation
        assert "await refreshNotificationDataAfterMutation(" in operation

    assert "通知条件の状態を変更できませんでした" in operations["toggle"]
    assert "通知条件を削除できませんでした" in operations["delete"]
    assert "通知条件を保存できませんでした" in operations["save"]
    assert operations["save"].index("通知条件を保存できませんでした") < (
        operations["save"].rindex("await refreshNotificationDataAfterMutation(")
    )
    assert operations["save"].index("formPanel.hidden = true") < (
        operations["save"].index("await refreshNotificationDataAfterMutation(")
    )
    assert "const saveButton =" not in script


def test_incomplete_rules_cannot_be_enabled_but_can_still_be_paused() -> None:
    script = read(SCRIPT_PATH)
    completeness_check = script_section(
        script,
        "function canEnableRule",
        "async function toggleRule",
    )
    toggle = script_section(
        script,
        "async function toggleRule",
        "async function deleteRule",
    )
    guard = "if (!rule.is_enabled && !canEnableRule(rule.id))"

    assert "ruleFacilities.some(" in completeness_check
    assert "ruleWeekdays.some(" in completeness_check
    assert "return hasFacility && hasWeekday;" in completeness_check
    assert guard in toggle
    assert "施設と曜日が1件以上登録された条件だけを有効化できます。" in toggle
    assert toggle.index(guard) < toggle.index('.update({ is_enabled: !rule.is_enabled })')
    guarded_section = toggle[
        toggle.index(guard):toggle.index('.update({ is_enabled: !rule.is_enabled })')
    ]
    assert "return;" in guarded_section
    assert "if (rule.is_enabled &&" not in toggle


def test_phase_two_documents_describe_current_progress_and_remaining_scope() -> None:
    design = read(DESIGN_PATH)
    roadmap = read(ROADMAP_PATH)
    service_spec = read(SERVICE_SPEC_PATH)
    readme = read(README_PATH)
    documents = (design, roadmap, service_spec, readme)

    assert "Phase 2は完了" in design
    assert "一覧・新規作成・編集・削除・一時停止・有効化UI" in design
    assert "`save_notification_rule` RPC" in design
    assert "画面と照合処理は未実装" not in design
    assert "今回の実装時点では本番Supabaseへ未適用" not in design

    assert "| Phase 2 | 完了 |" in roadmap
    assert "**状態: 完了**" in roadmap
    assert "| Phase 2 | 次に着手 |" not in roadmap
    assert "**状態: 次に着手**" not in roadmap

    assert "`account/notifications.html`" in service_spec
    assert "通知条件の一覧・新規作成・編集・一時停止・有効化・削除UI" in (
        service_spec
    )
    assert "照合エンジン" in service_spec

    assert "Phase 2は完了" in readme
    assert "「登録済み n / 5件」" in readme
    assert "通知条件UI、照合ロジック、退会処理は未実装" not in readme
    assert "このmigrationはSupabase環境へまだ適用していません" not in readme

    for document in documents:
        assert "最大5件" in document
        assert "停止中" in document
        assert "advisory" in document
        assert "削除" in document
        assert "Phase 3" in document
        assert "メール" in document
        assert "自動適用されない" in document

    assert "Phase 3.4.1: automation foundation complete" in roadmap
    assert "Phase 3.4.2: production staged enablementを完了" in roadmap
    assert "Phase 3.4.3: legacy administrator LINEを退役" in roadmap


def test_save_rpc_is_security_invoker_and_has_no_user_id_argument() -> None:
    sql = read(MIGRATION_PATH).lower()
    normalized = compact(sql)
    signature_match = re.search(
        r"create function public\.save_notification_rule\((.*?)\)"
        r" returns uuid language plpgsql security invoker"
        r" set search_path = ''",
        normalized,
    )

    assert signature_match
    signature = signature_match.group(1)
    assert "p_rule_id uuid" in signature
    assert "p_user_id" not in signature
    assert "security definer" not in sql
    assert "disable row level security" not in sql
    assert "create policy" not in sql
    assert "v_user_id uuid := auth.uid()" in normalized
    assert "if v_user_id is null then" in normalized
    assert "values ( v_user_id," in normalized
    assert "where rule.id = p_rule_id and rule.user_id = v_user_id" in normalized


def test_save_rpc_validates_and_deduplicates_facilities_and_weekdays() -> None:
    sql = compact(read(MIGRATION_PATH).lower())

    assert "cardinality(p_facility_ids) < 1" in sql
    assert "cardinality(p_weekdays) < 1" in sql
    assert "facility.id = any (v_facility_ids)" in sql
    assert "facility.is_active = true" in sql
    assert "weekday_input.weekday < 1" in sql
    assert "weekday_input.weekday > 7" in sql
    assert "array_agg( distinct facility_input.facility_id" in sql
    assert "array_agg( distinct weekday_input.weekday" in sql

    for table in (
        "public.notification_rules",
        "public.notification_rule_facilities",
        "public.notification_rule_weekdays",
    ):
        assert table in sql
    assert "delete from public.notification_rule_facilities" in sql
    assert "delete from public.notification_rule_weekdays" in sql
    assert "insert into public.notification_rule_facilities" in sql
    assert "insert into public.notification_rule_weekdays" in sql
    assert "return v_rule_id" in sql


def test_save_rpc_execute_permission_is_authenticated_only() -> None:
    sql = compact(read(MIGRATION_PATH).lower())
    grants = re.findall(r"\bgrant\s+execute\b.*?;", sql)

    assert len(grants) == 1
    assert grants[0].endswith("to authenticated;")
    assert "to anon" not in grants[0]
    assert "to public" not in grants[0]
    assert re.search(
        r"revoke all on function public\.save_notification_rule\(.*?\)"
        r" from public, anon, authenticated;",
        sql,
    )
