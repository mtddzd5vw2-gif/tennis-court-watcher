(() => {
  "use strict";

  const PUBLIC_OUTCOMES = new Set([
    "success",
    "friend_required",
    "expired",
    "inactive",
    "already_linked",
    "line_in_use",
    "cancelled",
    "failed",
  ]);
  const LINE_AUTHORIZE_ORIGIN = "https://access.line.me";
  const LINE_AUTHORIZE_PATH = "/oauth2/v2.1/authorize";
  const SAFE_LINK_STATES = new Set([
    "active",
    "blocked",
    "unlinked",
    "delivery_failed",
  ]);

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

  function consumePublicOutcome() {
    const parameters = new URLSearchParams(window.location.search);
    if (!parameters.has("line_link")) {
      return "";
    }

    const values = parameters.getAll("line_link");
    window.history.replaceState(null, document.title, window.location.pathname);
    if (values.length !== 1 || !PUBLIC_OUTCOMES.has(values[0])) {
      return "failed";
    }
    return values[0];
  }

  function showPublicOutcome(element, outcome) {
    const messages = {
      success: ["LINEアカウントを連携しました。", "success"],
      friend_required: [
        "LINE連携は完了しました。通知を受け取るには、公式アカウントを友だち追加してからもう一度確認してください。",
        "warning",
      ],
      expired: [
        "LINE連携の有効時間が切れました。もう一度お試しください。",
        "error",
      ],
      inactive: [
        "LINE連携を利用するには、現行の利用規約への同意が必要です。",
        "error",
      ],
      already_linked: [
        "別のLINEアカウントへ変更する場合は、現在の連携を解除してからお試しください。",
        "error",
      ],
      line_in_use: [
        "このLINEアカウントはすでに別の会員アカウントへ連携されています。",
        "error",
      ],
      cancelled: ["LINE連携をキャンセルしました。", ""],
      failed: [
        "LINE連携を完了できませんでした。時間をおいて、もう一度お試しください。",
        "error",
      ],
    };
    if (!outcome || !messages[outcome]) {
      return;
    }
    setStatus(element, messages[outcome][0], messages[outcome][1]);
  }

  function validAuthorizationUrl(value) {
    if (typeof value !== "string" || value.length > 4096) {
      return false;
    }
    try {
      const url = new URL(value);
      return (
        url.origin === LINE_AUTHORIZE_ORIGIN &&
        url.pathname === LINE_AUTHORIZE_PATH &&
        url.username === "" &&
        url.password === ""
      );
    } catch {
      return false;
    }
  }

  // The callback contains only a coarse result, but consume it before account
  // or terms queries so it never remains in browser history on an error path.
  const pendingPublicOutcome = consumePublicOutcome();

  async function setup(client) {
    const panel = document.querySelector("[data-line-link-panel]");
    const summary = document.querySelector("[data-line-link-summary]");
    const guidance = document.querySelector("[data-line-link-guidance]");
    const start = document.querySelector("[data-line-link-start]");
    const unlinkStart = document.querySelector("[data-line-unlink-start]");
    const unlinkPanel = document.querySelector("[data-line-unlink-panel]");
    const unlinkConfirm = document.querySelector("[data-line-unlink-confirm]");
    const unlinkCancel = document.querySelector("[data-line-unlink-cancel]");
    const resultStatus = document.querySelector("[data-line-link-result]");
    const actionStatus = document.querySelector("[data-line-link-action-status]");

    if (
      !panel ||
      !summary ||
      !guidance ||
      !start ||
      !unlinkStart ||
      !unlinkPanel ||
      !unlinkConfirm ||
      !unlinkCancel ||
      !resultStatus ||
      !actionStatus
    ) {
      return;
    }

    showPublicOutcome(resultStatus, pendingPublicOutcome);
    panel.hidden = false;

    let currentState = "loading";
    let starting = false;
    let unlinking = false;

    const render = (state) => {
      currentState = state;
      unlinkPanel.hidden = true;
      unlinkConfirm.disabled = false;
      unlinkCancel.disabled = false;

      if (state === "active") {
        summary.textContent = "LINE通知は連携済みです";
        summary.dataset.state = "active";
        guidance.textContent =
          "LINEアカウントとの連携は完了しています。利用者別LINE通知の配信開始は、準備が整い次第お知らせします。";
        start.hidden = true;
        start.disabled = true;
        unlinkStart.hidden = false;
        unlinkStart.disabled = false;
        return;
      }

      if (state === "blocked" || state === "delivery_failed") {
        summary.textContent = "友だち追加の確認が必要です";
        summary.dataset.state = "attention";
        guidance.textContent =
          "LINE公式アカウントを友だち追加またはブロック解除してから、連携状態をもう一度確認してください。";
        start.textContent = "友だち追加を確認する";
        start.hidden = false;
        start.disabled = false;
        unlinkStart.hidden = false;
        unlinkStart.disabled = false;
        return;
      }

      if (state === "error") {
        summary.textContent = "LINE連携状況を確認できませんでした";
        summary.dataset.state = "attention";
        guidance.textContent =
          "時間をおいてページを再読み込みしてください。";
        start.hidden = true;
        start.disabled = true;
        unlinkStart.hidden = true;
        unlinkStart.disabled = true;
        return;
      }

      summary.textContent = "LINEは未連携です";
      summary.dataset.state = "inactive";
      guidance.textContent =
        "LINE通知を使う準備として、LINEアカウントを連携できます。連携だけでは通知配信は始まりません。";
      start.textContent = "LINEアカウントを連携する";
      start.hidden = false;
      start.disabled = false;
      unlinkStart.hidden = true;
      unlinkStart.disabled = true;
    };

    const refresh = async () => {
      setStatus(actionStatus, "LINE連携状況を確認しています…");
      start.disabled = true;
      unlinkStart.disabled = true;
      try {
        const response = await client.rpc("get_my_line_link_status");
        if (response.error || !Array.isArray(response.data)) {
          throw new Error("line_link_status_unavailable");
        }
        if (response.data.length > 1) {
          throw new Error("line_link_status_invalid");
        }
        const status = response.data.length === 1
          ? response.data[0].link_status
          : "unlinked";
        if (!SAFE_LINK_STATES.has(status)) {
          throw new Error("line_link_status_invalid");
        }
        render(status);
        setStatus(actionStatus, "");
      } catch {
        render("error");
        setStatus(
          actionStatus,
          "LINE連携状況を取得できませんでした。",
          "error",
        );
      }
    };

    start.addEventListener("click", async () => {
      if (starting || unlinking || currentState === "error") {
        return;
      }
      starting = true;
      start.disabled = true;
      unlinkStart.disabled = true;
      setStatus(actionStatus, "LINEを開いています…");
      try {
        const response = await client.functions.invoke(
          "start-line-account-link",
          { body: {} },
        );
        const authorizationUrl = response.data &&
            response.data.authorization_url;
        if (response.error || !validAuthorizationUrl(authorizationUrl)) {
          throw new Error("line_link_start_failed");
        }
        window.location.assign(authorizationUrl);
      } catch {
        starting = false;
        render(currentState);
        setStatus(
          actionStatus,
          "LINE連携を開始できませんでした。時間をおいて、もう一度お試しください。",
          "error",
        );
      }
    });

    unlinkStart.addEventListener("click", () => {
      if (starting || unlinking) {
        return;
      }
      unlinkPanel.hidden = false;
      unlinkStart.disabled = true;
      setStatus(actionStatus, "");
    });

    unlinkCancel.addEventListener("click", () => {
      if (unlinking) {
        return;
      }
      unlinkPanel.hidden = true;
      unlinkStart.disabled = false;
      setStatus(actionStatus, "");
    });

    unlinkConfirm.addEventListener("click", async () => {
      if (starting || unlinking) {
        return;
      }
      unlinking = true;
      unlinkConfirm.disabled = true;
      unlinkCancel.disabled = true;
      setStatus(actionStatus, "LINE連携を解除しています…");
      try {
        const response = await client.functions.invoke(
          "unlink-line-account",
          { body: { confirmation: "unlink-line-account" } },
        );
        if (response.error) {
          throw new Error("line_unlink_failed");
        }
        unlinking = false;
        render("unlinked");
        setStatus(actionStatus, "LINE連携を解除しました。", "success");
      } catch {
        unlinking = false;
        unlinkConfirm.disabled = false;
        unlinkCancel.disabled = false;
        setStatus(
          actionStatus,
          "LINE連携を解除できませんでした。時間をおいて、もう一度お試しください。",
          "error",
        );
      }
    });

    await refresh();
  }

  window.TCW_LINE_ACCOUNT_LINK = Object.freeze({ setup });
})();
