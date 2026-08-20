(() => {
  "use strict";

  const LOGIN_PATH = "../auth/login.html";
  const MAX_NOTIFICATION_RULES = 5;
  const NOTIFICATION_RULE_LIMIT_MESSAGE =
    "通知条件は最大5件まで登録できます。追加するには既存の条件を削除してください。";
  const NOTIFICATION_RULE_LIMIT_DB_MESSAGE =
    "Notification rule limit of 5 has been reached.";
  const WEEKDAY_LABELS = new Map([
    [1, "月曜日"],
    [2, "火曜日"],
    [3, "水曜日"],
    [4, "木曜日"],
    [5, "金曜日"],
    [6, "土曜日"],
    [7, "日曜日"],
  ]);
  const PROVIDER_SUPPRESSION_REASONS = new Set([
    "resend_bounced",
    "resend_complained",
    "resend_suppressed",
  ]);

  const loading = document.querySelector("[data-notification-loading]");
  const membershipRequired = document.querySelector(
    "[data-membership-required]",
  );
  const content = document.querySelector("[data-notification-content]");
  const status = document.querySelector("[data-notification-status]");
  const emailPreferenceToggle = document.querySelector(
    "[data-email-notification-toggle]",
  );
  const emailPreferenceGuidance = document.querySelector(
    "[data-email-notification-guidance]",
  );
  const emailPreferenceStatus = document.querySelector(
    "[data-email-notification-status]",
  );
  const newRuleButton = document.querySelector("[data-new-rule]");
  const ruleCount = document.querySelector("[data-notification-rule-count]");
  const ruleLimitGuidance = document.querySelector(
    "[data-notification-rule-limit]",
  );
  const ruleList = document.querySelector("[data-notification-rule-list]");
  const emptyState = document.querySelector("[data-notification-empty]");
  const formPanel = document.querySelector(
    "[data-notification-rule-form-panel]",
  );
  const formTitle = document.querySelector("[data-rule-form-title]");
  const form = document.querySelector("[data-notification-rule-form]");
  const formErrors = document.querySelector("[data-form-errors]");
  const nameInput = document.querySelector("[data-rule-name]");
  const facilityFieldset = document.querySelector("[data-facility-fieldset]");
  const facilityOptions = document.querySelector("[data-facility-options]");
  const weekdayFieldset = document.querySelector("[data-weekday-fieldset]");
  const weekdayInputs = Array.from(
    document.querySelectorAll("[data-rule-weekday]"),
  );
  const dateFromInput = document.querySelector("[data-rule-date-from]");
  const dateToInput = document.querySelector("[data-rule-date-to]");
  const startTimeInput = document.querySelector("[data-rule-start-time]");
  const endTimeInput = document.querySelector("[data-rule-end-time]");
  const minimumDurationInput = document.querySelector(
    "[data-rule-minimum-duration]",
  );
  const enabledInput = document.querySelector("[data-rule-enabled]");
  const cancelButton = document.querySelector("[data-cancel-rule]");

  let client;
  let userId;
  let facilities = [];
  let rules = [];
  let ruleFacilities = [];
  let ruleWeekdays = [];
  let emailPreference = null;
  let emailPreferenceBusy = false;
  let editingRuleId = null;
  let formOpen = false;
  let formSubmitting = false;
  let mutationBusy = false;
  let refreshFailed = false;

  function getAuthConfig() {
    const config = window.TCW_AUTH_CONFIG;
    if (
      !config ||
      typeof config.supabaseUrl !== "string" ||
      typeof config.supabasePublishableKey !== "string" ||
      !config.supabaseUrl ||
      !config.supabasePublishableKey
    ) {
      throw new Error("auth_config_unavailable");
    }
    return config;
  }

  function createAuthClient(config) {
    if (!window.supabase || typeof window.supabase.createClient !== "function") {
      throw new Error("auth_sdk_unavailable");
    }

    return window.supabase.createClient(
      config.supabaseUrl,
      config.supabasePublishableKey,
      {
        auth: {
          flowType: "pkce",
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: false,
        },
      },
    );
  }

  function setStatus(element, message, state = "") {
    if (!element) {
      return;
    }
    element.textContent = message;
    if (state) {
      element.dataset.state = state;
    } else {
      delete element.dataset.state;
    }
  }

  function createElement(tagName, className = "", text = "") {
    const element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    if (text) {
      element.textContent = text;
    }
    return element;
  }

  function activeFacilities() {
    return facilities.filter((facility) => facility.is_active === true);
  }

  function hasReachedNotificationRuleLimit() {
    return rules.length >= MAX_NOTIFICATION_RULES;
  }

  function updateNotificationRuleLimitDisplay() {
    ruleCount.textContent =
      `登録済み ${rules.length} / ${MAX_NOTIFICATION_RULES}件`;
    ruleLimitGuidance.hidden = !hasReachedNotificationRuleLimit();
  }

  function updateActionAvailability() {
    const disableListActions =
      mutationBusy || formSubmitting || formOpen || refreshFailed;
    updateNotificationRuleLimitDisplay();
    newRuleButton.disabled =
      disableListActions ||
      activeFacilities().length === 0 ||
      hasReachedNotificationRuleLimit();
    for (const button of ruleList.querySelectorAll("[data-rule-action]")) {
      button.disabled = disableListActions;
    }
    if (emailPreferenceToggle) {
      emailPreferenceToggle.disabled =
        emailPreferenceBusy ||
        (emailPreference !== null &&
          PROVIDER_SUPPRESSION_REASONS.has(emailPreference.disabled_reason));
    }
  }

  function renderEmailPreference() {
    if (!emailPreference || !emailPreferenceToggle) {
      return;
    }

    const providerSuppressed = PROVIDER_SUPPRESSION_REASONS.has(
      emailPreference.disabled_reason,
    );
    emailPreferenceToggle.checked = emailPreference.is_enabled === true;
    if (providerSuppressed) {
      emailPreferenceGuidance.textContent =
        "配信エラーのためメール通知を停止しています。安全確認が必要なため、この画面から再開できません。";
    } else if (emailPreference.is_enabled) {
      emailPreferenceGuidance.textContent =
        "メール通知は有効です。不要になった場合はいつでも停止できます。";
    } else {
      emailPreferenceGuidance.textContent =
        "メール通知は停止中です。受け取りを再開すると、過去の停止リンクは無効になります。";
    }
    updateActionAvailability();
  }

  function setMutationBusy(isBusy) {
    mutationBusy = isBusy;
    updateActionAvailability();
  }

  function setFormBusy(isBusy) {
    formSubmitting = isBusy;
    for (const control of form.elements) {
      control.disabled = isBusy;
    }
    updateActionAvailability();
  }

  function formatDate(value) {
    if (!value) {
      return "指定なし";
    }
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) {
      return "確認できません";
    }
    return `${match[1]}年${Number(match[2])}月${Number(match[3])}日`;
  }

  function formatTime(value) {
    if (typeof value !== "string" || value.length < 5) {
      return "確認できません";
    }
    return value.slice(0, 5);
  }

  function formatTimestamp(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "確認できません";
    }
    return new Intl.DateTimeFormat("ja-JP", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "Asia/Tokyo",
    }).format(date);
  }

  function appendDefinitionRow(list, term, description) {
    const termElement = createElement("dt", "", term);
    const descriptionElement = createElement("dd", "", description);
    list.append(termElement, descriptionElement);
  }

  function selectedFacilityNames(ruleId) {
    const facilityNames = new Map(
      facilities.map((facility) => [facility.id, facility.name]),
    );
    return ruleFacilities
      .filter((selection) => selection.rule_id === ruleId)
      .map(
        (selection) =>
          facilityNames.get(selection.facility_id) || "利用停止中の施設",
      )
      .join("、");
  }

  function selectedWeekdayNames(ruleId) {
    return ruleWeekdays
      .filter((selection) => selection.rule_id === ruleId)
      .map((selection) => Number(selection.weekday))
      .sort((left, right) => left - right)
      .map((weekday) => WEEKDAY_LABELS.get(weekday) || "確認できない曜日")
      .join("・");
  }

  function createRuleAction(label, variant, action) {
    const button = createElement(
      "button",
      `button button--compact ${variant}`.trim(),
      label,
    );
    button.type = "button";
    button.dataset.ruleAction = action;
    return button;
  }

  function renderRules() {
    ruleList.replaceChildren();
    emptyState.hidden = rules.length !== 0;

    for (const rule of rules) {
      const card = createElement("article", "rule-card");
      const header = createElement("div", "rule-card__header");
      const headingGroup = createElement("div");
      const heading = createElement("h3", "rule-card__title", rule.name);
      const state = createElement(
        "span",
        rule.is_enabled
          ? "rule-state rule-state--enabled"
          : "rule-state rule-state--paused",
        rule.is_enabled ? "有効" : "停止中",
      );
      headingGroup.append(heading, state);
      header.append(headingGroup);

      const details = createElement("dl", "rule-details");
      appendDefinitionRow(
        details,
        "施設",
        selectedFacilityNames(rule.id) || "施設が登録されていません",
      );
      appendDefinitionRow(
        details,
        "曜日",
        selectedWeekdayNames(rule.id) || "曜日が登録されていません",
      );
      appendDefinitionRow(
        details,
        "対象期間",
        `${formatDate(rule.date_from)} 〜 ${formatDate(rule.date_to)}`,
      );
      appendDefinitionRow(
        details,
        "対象時間帯",
        `${formatTime(rule.start_time)} 〜 ${formatTime(rule.end_time)}`,
      );
      appendDefinitionRow(
        details,
        "最低連続時間",
        `${rule.minimum_duration_minutes}分`,
      );
      appendDefinitionRow(details, "更新日時", formatTimestamp(rule.updated_at));

      const actions = createElement("div", "rule-card__actions");
      const editButton = createRuleAction(
        "編集",
        "button--secondary",
        "edit",
      );
      const toggleButton = createRuleAction(
        rule.is_enabled ? "一時停止" : "有効化",
        "button--secondary",
        "toggle",
      );
      const deleteButton = createRuleAction(
        "削除",
        "button--danger",
        "delete",
      );

      editButton.addEventListener("click", () => openRuleForm(rule));
      toggleButton.addEventListener("click", () => {
        void toggleRule(rule);
      });
      deleteButton.addEventListener("click", () => {
        void deleteRule(rule);
      });
      actions.append(editButton, toggleButton, deleteButton);
      card.append(header, details, actions);
      ruleList.append(card);
    }

    updateActionAvailability();
  }

  function renderFacilityOptions() {
    facilityOptions.replaceChildren();
    const availableFacilities = activeFacilities();

    if (!availableFacilities.length) {
      const unavailable = createElement(
        "p",
        "status-text",
        "現在選択できる施設がありません。",
      );
      unavailable.dataset.state = "error";
      facilityOptions.append(unavailable);
      updateActionAvailability();
      return;
    }

    for (const facility of availableFacilities) {
      const label = createElement("label", "check-option");
      const input = document.createElement("input");
      const text = createElement("span", "", facility.name);
      input.type = "checkbox";
      input.name = "facility_ids";
      input.value = facility.id;
      input.dataset.ruleFacility = "";
      label.append(input, text);
      facilityOptions.append(label);
    }

    updateActionAvailability();
  }

  function clearFormErrors() {
    formErrors.replaceChildren();
    formErrors.hidden = true;
    for (const element of form.querySelectorAll('[aria-invalid="true"]')) {
      element.removeAttribute("aria-invalid");
    }
  }

  function showFormErrors(errors) {
    clearFormErrors();
    const heading = createElement(
      "p",
      "form-errors__title",
      "入力内容を確認してください。",
    );
    const list = document.createElement("ul");
    for (const error of errors) {
      const item = createElement("li", "", error.message);
      list.append(item);
      for (const element of error.elements) {
        element.setAttribute("aria-invalid", "true");
      }
    }
    formErrors.append(heading, list);
    formErrors.hidden = false;
    formErrors.focus();
  }

  function timeToMinutes(value) {
    const match = /^(\d{2}):(\d{2})$/.exec(value);
    if (!match) {
      return null;
    }
    return Number(match[1]) * 60 + Number(match[2]);
  }

  function parseIsoDateUtc(value) {
    const match = /^(\d{4,})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) {
      return null;
    }

    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const date = new Date(0);
    date.setUTCHours(0, 0, 0, 0);
    date.setUTCFullYear(year, month - 1, day);

    if (
      date.getUTCFullYear() !== year ||
      date.getUTCMonth() !== month - 1 ||
      date.getUTCDate() !== day
    ) {
      return null;
    }

    return date;
  }

  function dateRangeContainsSelectedWeekday(dateFrom, dateTo, selectedWeekdays) {
    if (selectedWeekdays.length === 0) {
      return true;
    }

    const startDate = parseIsoDateUtc(dateFrom);
    const endDate = parseIsoDateUtc(dateTo);
    if (!startDate || !endDate || startDate > endDate) {
      return true;
    }

    const selected = new Set(selectedWeekdays);
    const dayMilliseconds = 24 * 60 * 60 * 1000;
    const spanDays = Math.floor(
      (endDate.getTime() - startDate.getTime()) / dayMilliseconds,
    );

    for (let offset = 0; offset <= Math.min(spanDays, 6); offset += 1) {
      const weekday = new Date(
        startDate.getTime() + offset * dayMilliseconds,
      ).getUTCDay();
      const isoWeekday = weekday === 0 ? 7 : weekday;
      if (selected.has(isoWeekday)) {
        return true;
      }
    }

    return false;
  }

  function validateRuleForm() {
    const errors = [];
    const trimmedName = nameInput.value.trim();
    const selectedFacilities = Array.from(
      facilityOptions.querySelectorAll('input[name="facility_ids"]:checked'),
      (input) => input.value,
    );
    const selectedWeekdays = weekdayInputs
      .filter((input) => input.checked)
      .map((input) => Number(input.value));
    const startMinutes = timeToMinutes(startTimeInput.value);
    const endMinutes = timeToMinutes(endTimeInput.value);
    const minimumDuration = Number(minimumDurationInput.value);

    if (!trimmedName) {
      errors.push({
        message: "条件名を入力してください。",
        elements: [nameInput],
      });
    } else if (trimmedName.length > 80) {
      errors.push({
        message: "条件名は80文字以内で入力してください。",
        elements: [nameInput],
      });
    }

    if (selectedFacilities.length < 1) {
      errors.push({
        message: "施設を1件以上選択してください。",
        elements: [facilityFieldset],
      });
    }

    if (selectedWeekdays.length < 1) {
      errors.push({
        message: "曜日を1件以上選択してください。",
        elements: [weekdayFieldset],
      });
    }

    if (startMinutes === null || endMinutes === null) {
      errors.push({
        message: "開始時刻と終了時刻を入力してください。",
        elements: [startTimeInput, endTimeInput],
      });
    } else if (startMinutes >= endMinutes) {
      errors.push({
        message: "開始時刻は終了時刻より前にしてください。",
        elements: [startTimeInput, endTimeInput],
      });
    }

    if (
      dateFromInput.value &&
      dateToInput.value &&
      dateFromInput.value > dateToInput.value
    ) {
      errors.push({
        message: "開始日は終了日以前の日付にしてください。",
        elements: [dateFromInput, dateToInput],
      });
    } else if (
      dateFromInput.value &&
      dateToInput.value &&
      !dateRangeContainsSelectedWeekday(
        dateFromInput.value,
        dateToInput.value,
        selectedWeekdays,
      )
    ) {
      errors.push({
        message:
          "対象期間内に選択した曜日がありません。日付または曜日を変更してください。",
        elements: [dateFromInput, dateToInput, weekdayFieldset],
      });
    }

    if (
      !Number.isInteger(minimumDuration) ||
      minimumDuration < 30 ||
      minimumDuration > 720 ||
      minimumDuration % 30 !== 0
    ) {
      errors.push({
        message: "最低連続時間は30〜720分の範囲で、30分単位で入力してください。",
        elements: [minimumDurationInput],
      });
    }

    return {
      errors,
      values: {
        p_rule_id: editingRuleId,
        p_name: trimmedName,
        p_is_enabled: enabledInput.checked,
        p_date_from: dateFromInput.value || null,
        p_date_to: dateToInput.value || null,
        p_start_time: startTimeInput.value,
        p_end_time: endTimeInput.value,
        p_minimum_duration_minutes: minimumDuration,
        p_facility_ids: selectedFacilities,
        p_weekdays: selectedWeekdays,
      },
    };
  }

  function normalizeTimeInput(value, fallback) {
    return typeof value === "string" && value.length >= 5
      ? value.slice(0, 5)
      : fallback;
  }

  function openRuleForm(rule = null) {
    if (!rule && hasReachedNotificationRuleLimit()) {
      setStatus(status, NOTIFICATION_RULE_LIMIT_MESSAGE, "error");
      updateActionAvailability();
      return;
    }

    form.reset();
    clearFormErrors();
    setStatus(status, "");
    editingRuleId = rule ? rule.id : null;
    formTitle.textContent = rule ? "通知条件を編集" : "新しい通知条件";

    if (rule) {
      nameInput.value = rule.name;
      dateFromInput.value = rule.date_from || "";
      dateToInput.value = rule.date_to || "";
      startTimeInput.value = normalizeTimeInput(rule.start_time, "08:00");
      endTimeInput.value = normalizeTimeInput(rule.end_time, "13:00");
      minimumDurationInput.value = String(rule.minimum_duration_minutes);
      enabledInput.checked = rule.is_enabled;

      const selectedFacilities = new Set(
        ruleFacilities
          .filter((selection) => selection.rule_id === rule.id)
          .map((selection) => selection.facility_id),
      );
      for (const input of facilityOptions.querySelectorAll(
        'input[name="facility_ids"]',
      )) {
        input.checked = selectedFacilities.has(input.value);
      }

      const selectedWeekdays = new Set(
        ruleWeekdays
          .filter((selection) => selection.rule_id === rule.id)
          .map((selection) => Number(selection.weekday)),
      );
      for (const input of weekdayInputs) {
        input.checked = selectedWeekdays.has(Number(input.value));
      }
    }

    formOpen = true;
    formPanel.hidden = false;
    updateActionAvailability();
    nameInput.focus();
    formPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function closeRuleForm() {
    form.reset();
    clearFormErrors();
    editingRuleId = null;
    formOpen = false;
    formPanel.hidden = true;
    updateActionAvailability();
    newRuleButton.focus();
  }

  async function loadNotificationData() {
    const [
      facilitiesResult,
      rulesResult,
      ruleFacilitiesResult,
      ruleWeekdaysResult,
      emailPreferenceResult,
    ] = await Promise.all([
      client
        .from("facilities")
        .select("id,name,is_active,sort_order")
        .order("sort_order", { ascending: true })
        .order("name", { ascending: true }),
      client
        .from("notification_rules")
        .select(
          "id,name,is_enabled,date_from,date_to,start_time,end_time," +
            "minimum_duration_minutes,updated_at",
        )
        .eq("user_id", userId)
        .order("updated_at", { ascending: false }),
      client
        .from("notification_rule_facilities")
        .select("rule_id,facility_id")
        .eq("user_id", userId),
      client
        .from("notification_rule_weekdays")
        .select("rule_id,weekday")
        .eq("user_id", userId),
      client
        .from("notification_email_preferences")
        .select("is_enabled,disabled_reason,disabled_at,updated_at")
        .eq("user_id", userId)
        .single(),
    ]);

    if (
      facilitiesResult.error ||
      rulesResult.error ||
      ruleFacilitiesResult.error ||
      ruleWeekdaysResult.error ||
      emailPreferenceResult.error
    ) {
      throw new Error("notification_data_unavailable");
    }

    facilities = facilitiesResult.data || [];
    rules = rulesResult.data || [];
    ruleFacilities = ruleFacilitiesResult.data || [];
    ruleWeekdays = ruleWeekdaysResult.data || [];
    emailPreference = emailPreferenceResult.data;
    renderFacilityOptions();
    renderRules();
    renderEmailPreference();
  }

  async function refreshEmailPreferenceFromServer() {
    if (!client || !userId || emailPreferenceBusy) {
      return;
    }

    let result;
    try {
      result = await client
        .from("notification_email_preferences")
        .select("is_enabled,disabled_reason,disabled_at,updated_at")
        .eq("user_id", userId)
        .single();
    } catch {
      result = null;
    }

    if (!result || result.error || !result.data) {
      return;
    }

    emailPreference = result.data;
    renderEmailPreference();
  }

  function scrollToEmailPreferenceIfRequested() {
    if (window.location.hash !== "#email-notification-settings") {
      return;
    }

    document
      .getElementById("email-notification-settings")
      ?.scrollIntoView({ block: "start" });
  }

  async function updateEmailPreference() {
    if (!emailPreference || emailPreferenceBusy) {
      return;
    }
    if (PROVIDER_SUPPRESSION_REASONS.has(emailPreference.disabled_reason)) {
      emailPreferenceToggle.checked = false;
      renderEmailPreference();
      return;
    }

    const nextEnabled = emailPreferenceToggle.checked;
    emailPreferenceBusy = true;
    updateActionAvailability();
    setStatus(
      emailPreferenceStatus,
      nextEnabled ? "メール通知を有効にしています…" : "メール通知を停止しています…",
    );

    let result;
    try {
      result = await client
        .from("notification_email_preferences")
        .update({ is_enabled: nextEnabled })
        .eq("user_id", userId)
        .select("is_enabled,disabled_reason,disabled_at,updated_at")
        .single();
    } catch {
      result = null;
    }

    if (!result || result.error || !result.data) {
      emailPreferenceToggle.checked = emailPreference.is_enabled === true;
      setStatus(
        emailPreferenceStatus,
        "メール通知の設定を変更できませんでした。状態を再読み込みしてから、もう一度お試しください。",
        "error",
      );
      emailPreferenceBusy = false;
      updateActionAvailability();
      return;
    }

    emailPreference = result.data;
    emailPreferenceBusy = false;
    renderEmailPreference();
    setStatus(
      emailPreferenceStatus,
      nextEnabled ? "メール通知を有効にしました。" : "メール通知を停止しました。",
      "success",
    );
  }

  async function refreshNotificationDataAfterMutation(
    successMessage,
    mutationCompleted = true,
  ) {
    try {
      await loadNotificationData();
    } catch {
      refreshFailed = true;
      updateActionAvailability();
      setStatus(
        status,
        (mutationCompleted
          ? "変更は完了しましたが、一覧を再読み込みできませんでした。"
          : "保存は完了しておらず、一覧を再読み込みできませんでした。") +
          "ページを再読み込みしてください。操作を繰り返さないでください。",
        "error",
      );
      return false;
    }

    refreshFailed = false;
    updateActionAvailability();
    setStatus(status, successMessage, mutationCompleted ? "success" : "error");
    return true;
  }

  function isNotificationRuleLimitError(error) {
    return (
      error &&
      typeof error.message === "string" &&
      error.message.includes(NOTIFICATION_RULE_LIMIT_DB_MESSAGE)
    );
  }

  function canEnableRule(ruleId) {
    const hasFacility = ruleFacilities.some(
      (selection) => selection.rule_id === ruleId,
    );
    const hasWeekday = ruleWeekdays.some(
      (selection) => selection.rule_id === ruleId,
    );
    return hasFacility && hasWeekday;
  }

  async function toggleRule(rule) {
    if (mutationBusy || formOpen) {
      return;
    }
    if (!rule.is_enabled && !canEnableRule(rule.id)) {
      setStatus(
        status,
        "施設と曜日が1件以上登録された条件だけを有効化できます。" +
          "条件を編集して不足している項目を登録してください。",
        "error",
      );
      return;
    }

    setMutationBusy(true);
    setStatus(
      status,
      rule.is_enabled ? "通知条件を一時停止しています…" : "通知条件を有効化しています…",
    );

    let result;
    try {
      result = await client
        .from("notification_rules")
        .update({ is_enabled: !rule.is_enabled })
        .eq("id", rule.id)
        .eq("user_id", userId)
        .select("id")
        .single();
    } catch {
      result = null;
    }

    if (!result || result.error || !result.data) {
      setStatus(
        status,
        "通知条件の状態を変更できませんでした。時間をおいて再度お試しください。",
        "error",
      );
      setMutationBusy(false);
      return;
    }

    await refreshNotificationDataAfterMutation(
      rule.is_enabled
        ? "通知条件を一時停止しました。"
        : "通知条件を有効化しました。",
    );
    setMutationBusy(false);
  }

  async function deleteRule(rule) {
    if (mutationBusy || formOpen) {
      return;
    }
    const confirmed = window.confirm(
      `通知条件「${rule.name}」を削除しますか？この操作は取り消せません。`,
    );
    if (!confirmed) {
      return;
    }

    setMutationBusy(true);
    setStatus(status, "通知条件を削除しています…");
    let result;
    try {
      result = await client
        .from("notification_rules")
        .delete()
        .eq("id", rule.id)
        .eq("user_id", userId)
        .select("id")
        .single();
    } catch {
      result = null;
    }

    if (!result || result.error || !result.data) {
      setStatus(
        status,
        "通知条件を削除できませんでした。時間をおいて再度お試しください。",
        "error",
      );
      setMutationBusy(false);
      return;
    }

    await refreshNotificationDataAfterMutation("通知条件を削除しました。");
    setMutationBusy(false);
  }

  async function saveRule(event) {
    event.preventDefault();
    if (formSubmitting) {
      return;
    }

    const wasEditing = editingRuleId !== null;
    if (!wasEditing && hasReachedNotificationRuleLimit()) {
      setStatus(status, NOTIFICATION_RULE_LIMIT_MESSAGE, "error");
      updateActionAvailability();
      return;
    }

    const validation = validateRuleForm();
    if (validation.errors.length) {
      showFormErrors(validation.errors);
      return;
    }

    clearFormErrors();
    setFormBusy(true);
    setStatus(status, "通知条件を保存しています…");
    let result;
    try {
      result = await client.rpc(
        "save_notification_rule",
        validation.values,
      );
    } catch {
      result = null;
    }

    if (!result || result.error || !result.data) {
      if (result && isNotificationRuleLimitError(result.error)) {
        formPanel.hidden = true;
        formOpen = false;
        editingRuleId = null;
        await refreshNotificationDataAfterMutation(
          NOTIFICATION_RULE_LIMIT_MESSAGE,
          false,
        );
        setFormBusy(false);
        return;
      }

      setStatus(
        status,
        "通知条件を保存できませんでした。入力内容を確認し、再度お試しください。",
        "error",
      );
      setFormBusy(false);
      return;
    }

    formPanel.hidden = true;
    formOpen = false;
    editingRuleId = null;
    await refreshNotificationDataAfterMutation(
      wasEditing ? "通知条件を更新しました。" : "通知条件を作成しました。",
    );
    setFormBusy(false);
    newRuleButton.focus();
  }

  async function start() {
    try {
      client = createAuthClient(getAuthConfig());
    } catch {
      setStatus(
        loading,
        "認証設定を読み込めませんでした。時間をおいて再読み込みしてください。",
        "error",
      );
      return;
    }

    let session;
    try {
      const sessionResult = await client.auth.getSession();
      if (sessionResult.error) {
        throw new Error("session_lookup_failed");
      }
      session = sessionResult.data.session;
    } catch {
      window.location.replace(LOGIN_PATH);
      return;
    }

    if (!session || !session.user || !session.user.id) {
      window.location.replace(LOGIN_PATH);
      return;
    }
    userId = session.user.id;

    setStatus(loading, "会員状態を確認しています…");
    let profileResult;
    try {
      profileResult = await client
        .from("profiles")
        .select("membership_status")
        .eq("id", userId)
        .single();
    } catch {
      profileResult = null;
    }

    if (!profileResult || profileResult.error || !profileResult.data) {
      setStatus(
        loading,
        "会員状態を確認できませんでした。時間をおいて再読み込みしてください。",
        "error",
      );
      return;
    }

    if (profileResult.data.membership_status !== "active") {
      loading.hidden = true;
      membershipRequired.hidden = false;
      return;
    }

    setStatus(loading, "通知条件を読み込んでいます…");
    try {
      await loadNotificationData();
      loading.hidden = true;
      content.hidden = false;
      scrollToEmailPreferenceIfRequested();
    } catch {
      setStatus(
        loading,
        "通知条件を取得できませんでした。時間をおいて再読み込みしてください。",
        "error",
      );
    }
  }

  newRuleButton.addEventListener("click", () => openRuleForm());
  cancelButton.addEventListener("click", closeRuleForm);
  form.addEventListener("submit", (event) => {
    void saveRule(event);
  });
  emailPreferenceToggle.addEventListener("change", () => {
    void updateEmailPreference();
  });

  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      void refreshEmailPreferenceFromServer();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      void refreshEmailPreferenceFromServer();
    }
  });

  void start();
})();
