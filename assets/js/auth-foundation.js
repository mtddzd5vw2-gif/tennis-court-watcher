(() => {
  "use strict";

  const LOGIN_PATH = "../auth/login.html";
  const ACCOUNT_PATH = "../account/index.html";
  const NOTIFICATIONS_PATH = "../account/notifications.html";
  const LINE_AUTH_PROVIDER = "custom:line";
  const PENDING_TERMS_KEY = "tcw.pendingTermsAcceptance";
  const PENDING_DESTINATION_KEY = "tcw.pendingAuthDestination";
  const FUNNEL_STORAGE_PREFIX = "tcw.anonymousFunnel";
  const FUNNEL_FUNCTION_NAME = "record-anonymous-funnel-event";
  const FUNNEL_EVENTS = new Set([
    "login_page_view",
    "line_start_click",
    "terms_prompt_view",
  ]);
  const LOGIN_SESSION_TIMEOUT_MS = 8000;
  const AUTHENTICATED_REDIRECT_DELAY_MS = 400;

  function getAuthConfig() {
    const config = window.TCW_AUTH_CONFIG;
    if (
      !config ||
      typeof config.supabaseUrl !== "string" ||
      typeof config.supabasePublishableKey !== "string" ||
      typeof config.authCallbackUrl !== "string" ||
      !config.supabaseUrl ||
      !config.supabasePublishableKey ||
      !config.authCallbackUrl
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

  function scrubAuthenticationParameters() {
    if (window.location.search || window.location.hash) {
      window.history.replaceState(null, document.title, window.location.pathname);
    }
  }

  function hasPendingTermsAcceptance() {
    try {
      return window.sessionStorage.getItem(PENDING_TERMS_KEY) === "1";
    } catch {
      return false;
    }
  }

  function clearPendingTermsAcceptance() {
    try {
      window.sessionStorage.removeItem(PENDING_TERMS_KEY);
    } catch {
      // The marker contains no account data and expires with this browser tab.
    }
  }

  function requestedDestination() {
    const parameters = new URLSearchParams(window.location.search);
    return parameters.get("next") === "notifications"
      ? NOTIFICATIONS_PATH
      : ACCOUNT_PATH;
  }

  function rememberPendingDestination(destination) {
    try {
      if (destination === NOTIFICATIONS_PATH) {
        window.sessionStorage.setItem(PENDING_DESTINATION_KEY, "notifications");
      } else {
        window.sessionStorage.removeItem(PENDING_DESTINATION_KEY);
      }
    } catch {
      // Authentication can continue; account remains the safe fallback.
    }
  }

  function consumePendingDestination() {
    try {
      const destination =
        window.sessionStorage.getItem(PENDING_DESTINATION_KEY) ===
        "notifications"
          ? NOTIFICATIONS_PATH
          : ACCOUNT_PATH;
      window.sessionStorage.removeItem(PENDING_DESTINATION_KEY);
      return destination;
    } catch {
      return ACCOUNT_PATH;
    }
  }

  function isMissingLoginAccount(error) {
    return (
      error &&
      error.status === 422 &&
      error.code === "otp_disabled"
    );
  }

  function currentJstDate() {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Tokyo",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date());
    const values = Object.fromEntries(
      parts.map((part) => [part.type, part.value]),
    );
    return `${values.year}-${values.month}-${values.day}`;
  }

  function recordAnonymousFunnelEvent(config, eventName) {
    if (!FUNNEL_EVENTS.has(eventName)) {
      return;
    }

    const storageKey = `${FUNNEL_STORAGE_PREFIX}.${currentJstDate()}.${eventName}`;
    try {
      if (window.localStorage.getItem(storageKey) === "1") {
        return;
      }
      window.localStorage.setItem(storageKey, "1");
    } catch {
      // Counting remains best effort if browser storage is unavailable.
    }

    const endpoint =
      `${config.supabaseUrl}/functions/v1/${FUNNEL_FUNCTION_NAME}`;
    const clearMarker = () => {
      try {
        window.localStorage.removeItem(storageKey);
      } catch {
        // A failed anonymous metric must never block authentication.
      }
    };

    void window.fetch(endpoint, {
      method: "POST",
      headers: {
        "content-type": "text/plain;charset=UTF-8",
      },
      body: JSON.stringify({ event_name: eventName }),
      cache: "no-store",
      credentials: "omit",
      keepalive: true,
      mode: "cors",
      referrerPolicy: "no-referrer",
    }).then((response) => {
      if (!response.ok) {
        clearMarker();
      }
    }).catch(clearMarker);
  }

  function enableLoginForm(client, config, form, destination) {
    const emailInput = form.elements.email;
    const submitButton = form.querySelector('button[type="submit"]');
    const status = form.querySelector("[data-form-status]");
    let submitting = false;

    const isValid = () =>
      emailInput.value.trim() !== "" &&
      emailInput.validity.valid;

    const updateSubmitState = () => {
      submitButton.disabled = submitting || !isValid();
    };

    emailInput.addEventListener("input", updateSubmitState);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (submitting || !isValid()) {
        updateSubmitState();
        return;
      }

      submitting = true;
      updateSubmitState();
      setStatus(status, "送信しています…");

      void client.auth
        .signInWithOtp({
          email: emailInput.value.trim(),
          options: {
            emailRedirectTo: config.authCallbackUrl,
            shouldCreateUser: false,
          },
        })
        .then(({ error }) => {
          if (error && !isMissingLoginAccount(error)) {
            throw new Error("magic_link_request_failed");
          }
          clearPendingTermsAcceptance();
          rememberPendingDestination(destination);
          form.reset();
          setStatus(
            status,
            "メールを確認してください。リンクを送信できる場合は、まもなく届きます。",
            "success",
          );
        })
        .catch(() => {
          setStatus(
            status,
            "送信を完了できませんでした。時間をおいて、もう一度お試しください。",
            "error",
          );
        })
        .finally(() => {
          submitting = false;
          updateSubmitState();
        });
    });

    updateSubmitState();
    form.hidden = false;
  }

  async function getLoginSession(client) {
    let timeoutId;
    const timeout = new Promise((resolve, reject) => {
      timeoutId = window.setTimeout(
        () => reject(new Error("session_lookup_timeout")),
        LOGIN_SESSION_TIMEOUT_MS,
      );
    });

    try {
      return await Promise.race([client.auth.getSession(), timeout]);
    } finally {
      window.clearTimeout(timeoutId);
    }
  }

  function enableLineLogin(client, config, button, status, destination) {
    let starting = false;
    button.disabled = false;

    button.addEventListener("click", async () => {
      if (starting) {
        return;
      }
      starting = true;
      button.disabled = true;
      setStatus(status, "LINEを開いています…");
      recordAnonymousFunnelEvent(config, "line_start_click");
      clearPendingTermsAcceptance();
      rememberPendingDestination(destination);

      try {
        const { error } = await client.auth.signInWithOAuth({
          provider: LINE_AUTH_PROVIDER,
          options: {
            redirectTo: config.authCallbackUrl,
            queryParams: {
              bot_prompt: "aggressive",
            },
          },
        });
        if (error) {
          throw new Error("line_login_failed");
        }
      } catch {
        starting = false;
        button.disabled = false;
        setStatus(
          status,
          "LINEログインを開始できませんでした。時間をおいて、もう一度お試しください。",
          "error",
        );
      }
    });
  }

  async function setupLogin(client, config) {
    const form = document.querySelector("[data-auth-form]");
    const lineButton = document.querySelector("[data-line-auth-start]");
    const lineStatus = document.querySelector("[data-line-auth-status]");
    const sessionStatus = document.querySelector(
      "[data-login-session-status]",
    );
    if (!form || !lineButton || !lineStatus || !sessionStatus) {
      return;
    }
    const destination = requestedDestination();

    try {
      const result = await getLoginSession(client);
      if (!result || result.error) {
        throw new Error("session_lookup_failed");
      }
      if (result.data && result.data.session) {
        setStatus(
          sessionStatus,
          destination === NOTIFICATIONS_PATH
            ? "ログイン済みです。空き通知へ移動します。"
            : "ログイン済みです。マイページへ移動します。",
          "success",
        );
        await new Promise((resolve) => {
          window.setTimeout(resolve, AUTHENTICATED_REDIRECT_DELAY_MS);
        });
        window.location.replace(destination);
        return;
      }
      sessionStatus.hidden = true;
      recordAnonymousFunnelEvent(config, "login_page_view");
    } catch {
      setStatus(
        sessionStatus,
        "ログイン状態を確認できませんでした。ログインが必要な場合は、以下からお試しください。",
        "error",
      );
    }

    enableLineLogin(client, config, lineButton, lineStatus, destination);
    enableLoginForm(client, config, form, destination);
  }

  async function handleCallback(client) {
    const status = document.querySelector("[data-callback-status]");
    const retry = document.querySelector("[data-callback-retry]");
    const parameters = new URLSearchParams(window.location.search);
    const code = parameters.get("code");

    // Read the one-time code once, then remove every query/fragment value before
    // rendering a result or following any link.
    scrubAuthenticationParameters();

    if (!code) {
      setStatus(
        status,
        "認証リンクを確認できませんでした。ログイン画面からもう一度お試しください。",
        "error",
      );
      retry.hidden = false;
      return;
    }

    setStatus(status, "認証を確認しています…");
    try {
      const { error } = await client.auth.exchangeCodeForSession(code);
      if (error) {
        throw new Error("code_exchange_failed");
      }

      if (hasPendingTermsAcceptance()) {
        try {
          const result = await client.rpc("accept_current_terms");
          if (!result.error) {
            clearPendingTermsAcceptance();
          }
        } catch {
          // Keep the session and marker. The account page provides a retry path.
        }
      }
      window.location.replace(consumePendingDestination());
    } catch {
      setStatus(
        status,
        "認証を完了できませんでした。リンクの期限を確認し、もう一度ログインしてください。",
        "error",
      );
      retry.hidden = false;
    }
  }

  async function setupAccount(client, config) {
    const loading = document.querySelector("[data-account-loading]");
    const content = document.querySelector("[data-account-content]");
    const email = document.querySelector("[data-account-email]");
    const backupEmailGuidance = document.querySelector(
      "[data-backup-email-guidance]",
    );
    const backupEmailForm = document.querySelector("[data-backup-email-form]");
    const backupEmailInput = backupEmailForm &&
      backupEmailForm.elements["backup-email"];
    const backupEmailButton = backupEmailForm &&
      backupEmailForm.querySelector('button[type="submit"]');
    const backupEmailStatus = document.querySelector(
      "[data-backup-email-status]",
    );
    const consentPanel = document.querySelector("[data-terms-consent-panel]");
    const consentInput = document.querySelector("[data-account-terms-consent]");
    const consentButton = document.querySelector("[data-accept-current-terms]");
    const consentStatus = document.querySelector("[data-terms-consent-status]");
    const logout = document.querySelector("[data-sign-out]");
    const deleteStart = document.querySelector("[data-delete-account-start]");
    const deletePanel = document.querySelector("[data-delete-account-panel]");
    const deleteConsent = document.querySelector("[data-delete-account-consent]");
    const deleteConfirm = document.querySelector("[data-delete-account-confirm]");
    const deleteCancel = document.querySelector("[data-delete-account-cancel]");
    const deleteStatus = document.querySelector("[data-delete-account-status]");
    const status = document.querySelector("[data-action-status]");
    let lineAccountLinkInitialized = false;

    let session;
    try {
      const result = await client.auth.getSession();
      if (result.error) {
        throw new Error("session_lookup_failed");
      }
      session = result.data.session;
    } catch {
      window.location.replace(LOGIN_PATH);
      return;
    }

    if (!session) {
      window.location.replace(LOGIN_PATH);
      return;
    }

    const accountEmail = session.user && session.user.email
      ? session.user.email
      : "";
    email.textContent = accountEmail || "LINEでログイン中";
    if (backupEmailGuidance) {
      backupEmailGuidance.textContent = accountEmail
        ? "このメールアドレスは、予備のログイン手段として利用できます。"
        : "メールは任意です。LINEを利用できない場合に備えて、予備のログイン手段を追加できます。";
    }
    if (
      !accountEmail &&
      backupEmailForm &&
      backupEmailInput &&
      backupEmailButton
    ) {
      backupEmailForm.hidden = false;
      const updateBackupEmailButton = () => {
        backupEmailButton.disabled =
          !backupEmailInput.value.trim() || !backupEmailInput.validity.valid;
      };
      backupEmailInput.addEventListener("input", updateBackupEmailButton);
      backupEmailForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (backupEmailButton.disabled) {
          return;
        }
        backupEmailInput.disabled = true;
        backupEmailButton.disabled = true;
        setStatus(backupEmailStatus, "確認メールを送っています…");
        rememberPendingDestination(ACCOUNT_PATH);
        try {
          const { error } = await client.auth.updateUser(
            { email: backupEmailInput.value.trim() },
            { emailRedirectTo: getAuthConfig().authCallbackUrl },
          );
          if (error) {
            throw new Error("backup_email_update_failed");
          }
          backupEmailForm.reset();
          setStatus(
            backupEmailStatus,
            "確認メールを送りました。メール内のリンクを開くと登録が完了します。",
            "success",
          );
        } catch {
          setStatus(
            backupEmailStatus,
            "確認メールを送れませんでした。時間をおいて、もう一度お試しください。",
            "error",
          );
        } finally {
          backupEmailInput.disabled = false;
          updateBackupEmailButton();
        }
      });
    }
    content.hidden = false;
    logout.disabled = false;
    deleteStart.disabled = false;

    const setupLineAccountLink = async (membershipStatus) => {
      if (
        membershipStatus !== "active" ||
        lineAccountLinkInitialized ||
        !window.TCW_LINE_ACCOUNT_LINK ||
        typeof window.TCW_LINE_ACCOUNT_LINK.setup !== "function"
      ) {
        return;
      }
      lineAccountLinkInitialized = true;
      try {
        await window.TCW_LINE_ACCOUNT_LINK.setup(client);
      } catch {
        const linePanel = document.querySelector("[data-line-link-panel]");
        if (linePanel) {
          linePanel.hidden = false;
        }
        setStatus(
          document.querySelector("[data-line-link-action-status]"),
          "LINE連携機能を読み込めませんでした。時間をおいて再読み込みしてください。",
          "error",
        );
      }
    };

    const loadAccountData = async () => {
      setStatus(loading, "会員情報を確認しています…");
      loading.hidden = false;
      consentPanel.hidden = true;

      const [profileResult, termsResult, currentDocumentResult] =
        await Promise.all([
          client
            .from("profiles")
            .select("membership_status")
            .single(),
          client
            .from("terms_acceptances")
            .select("document_type,version")
            .order("accepted_at", { ascending: false }),
          client
            .from("legal_document_versions")
            .select("version")
            .eq("document_type", "terms")
            .eq("is_current", true)
            .single(),
        ]);

      if (
        profileResult.error ||
        termsResult.error ||
        currentDocumentResult.error ||
        !profileResult.data ||
        !currentDocumentResult.data
      ) {
        throw new Error("account_data_unavailable");
      }

      const profile = profileResult.data;
      const acceptances = termsResult.data || [];
      const currentVersion = currentDocumentResult.data.version;
      const hasCurrentAcceptance = acceptances.some(
        (acceptance) =>
          acceptance.document_type === "terms" &&
          acceptance.version === currentVersion,
      );

      const requiresConsent =
        profile.membership_status === "pending_terms" ||
        !hasCurrentAcceptance;
      if (requiresConsent) {
        recordAnonymousFunnelEvent(config, "terms_prompt_view");
      }
      consentPanel.hidden = !requiresConsent;
      consentInput.checked = false;
      consentButton.disabled = true;
      setStatus(consentStatus, "");
      loading.hidden = true;
      return profile.membership_status;
    };

    consentInput.addEventListener("change", () => {
      consentButton.disabled = !consentInput.checked;
    });

    let acceptingTerms = false;
    consentButton.addEventListener("click", async () => {
      if (acceptingTerms || !consentInput.checked) {
        return;
      }
      acceptingTerms = true;
      consentButton.disabled = true;
      setStatus(consentStatus, "規約への同意を登録しています…");
      try {
        const result = await client.rpc("accept_current_terms");
        if (result.error) {
          throw new Error("terms_acceptance_failed");
        }
        clearPendingTermsAcceptance();
        rememberPendingDestination(ACCOUNT_PATH);
        const membershipStatus = await loadAccountData();
        await setupLineAccountLink(membershipStatus);
        setStatus(status, "利用規約への同意を登録しました。", "success");
      } catch {
        setStatus(
          consentStatus,
          "同意を登録できませんでした。時間をおいて、もう一度お試しください。",
          "error",
        );
      } finally {
        acceptingTerms = false;
        if (!consentPanel.hidden) {
          consentButton.disabled = !consentInput.checked;
        }
      }
    });

    let signingOut = false;
    logout.addEventListener("click", async () => {
      if (signingOut) {
        return;
      }
      signingOut = true;
      logout.disabled = true;
      setStatus(status, "ログアウトしています…");
      try {
        const { error } = await client.auth.signOut({ scope: "local" });
        if (error) {
          throw new Error("sign_out_failed");
        }
        email.textContent = "";
        window.location.replace(LOGIN_PATH);
      } catch {
        signingOut = false;
        logout.disabled = false;
        setStatus(
          status,
          "ログアウトを完了できませんでした。時間をおいて、もう一度お試しください。",
          "error",
        );
      }
    });

    let deletingAccount = false;

    deleteStart.addEventListener("click", () => {
      if (deletingAccount) {
        return;
      }
      deletePanel.hidden = false;
      deleteStart.disabled = true;
      deleteConsent.checked = false;
      deleteConfirm.disabled = true;
      setStatus(deleteStatus, "");
    });

    deleteConsent.addEventListener("change", () => {
      deleteConfirm.disabled =
        deletingAccount || !deleteConsent.checked;
    });

    deleteCancel.addEventListener("click", () => {
      if (deletingAccount) {
        return;
      }
      deletePanel.hidden = true;
      deleteConsent.checked = false;
      deleteConfirm.disabled = true;
      deleteStart.disabled = false;
      setStatus(deleteStatus, "");
    });

    deleteConfirm.addEventListener("click", async () => {
      if (deletingAccount || !deleteConsent.checked) {
        return;
      }

      deletingAccount = true;
      logout.disabled = true;
      deleteConfirm.disabled = true;
      deleteCancel.disabled = true;
      setStatus(deleteStatus, "退会処理を実行しています…");

      try {
        const result = await client.functions.invoke("delete-account", {
          body: {
            confirmation: "delete-my-account",
          },
        });

        if (result.error) {
          throw new Error("account_deletion_failed");
        }

        clearPendingTermsAcceptance();
        rememberPendingDestination(ACCOUNT_PATH);
        email.textContent = "";

        try {
          await client.auth.signOut({ scope: "local" });
        } catch {
          // The Auth user has already been deleted. Redirect regardless.
        }

        window.location.replace(LOGIN_PATH);
      } catch {
        deletingAccount = false;
        logout.disabled = false;
        deleteCancel.disabled = false;
        deleteConfirm.disabled = !deleteConsent.checked;
        setStatus(
          deleteStatus,
          "退会処理を完了できませんでした。時間をおいて、もう一度お試しください。",
          "error",
        );
      }
    });

    try {
      const membershipStatus = await loadAccountData();
      await setupLineAccountLink(membershipStatus);
    } catch {
      setStatus(
        loading,
        "会員情報を取得できませんでした。時間をおいて再読み込みしてください。",
        "error",
      );
    }
  }

  async function start() {
    const page = document.body.dataset.page;
    let client;
    let config;

    try {
      config = getAuthConfig();
      client = createAuthClient(config);
    } catch {
      if (page === "account") {
        setStatus(
          document.querySelector("[data-account-loading]"),
          "認証設定を読み込めませんでした。時間をおいて再読み込みしてください。",
          "error",
        );
      } else {
        setStatus(
          document.querySelector(
            "[data-login-session-status], [data-form-status], " +
              "[data-callback-status]",
          ),
          "認証サービスを利用できません。時間をおいて再読み込みしてください。",
          "error",
        );
      }
      return;
    }

    if (page === "auth-login") {
      await setupLogin(client, config);
    } else if (page === "auth-callback") {
      await handleCallback(client);
    } else if (page === "account") {
      await setupAccount(client, config);
    }
  }

  void start();
})();
