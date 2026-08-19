(() => {
  "use strict";

  const LOGIN_PATH = "../auth/login.html";
  const ACCOUNT_PATH = "../account/index.html";
  const PENDING_TERMS_KEY = "tcw.pendingTermsAcceptance";
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

  function rememberPendingTermsAcceptance() {
    try {
      window.sessionStorage.setItem(PENDING_TERMS_KEY, "1");
    } catch {
      // Authentication can continue. The account page provides a retry path.
    }
  }

  function clearPendingTermsAcceptance() {
    try {
      window.sessionStorage.removeItem(PENDING_TERMS_KEY);
    } catch {
      // The marker contains no account data and expires with this browser tab.
    }
  }

  function formatTimestamp(value) {
    if (!value) {
      return "未登録";
    }
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

  function enableLoginForm(client, config, form) {
    const emailInput = form.elements.email;
    const consentInput = form.elements["terms-consent"];
    const submitButton = form.querySelector('button[type="submit"]');
    const status = form.querySelector("[data-form-status]");
    let submitting = false;

    const isValid = () =>
      emailInput.value.trim() !== "" &&
      emailInput.validity.valid &&
      consentInput.checked;

    const updateSubmitState = () => {
      submitButton.disabled = submitting || !isValid();
    };

    emailInput.addEventListener("input", updateSubmitState);
    consentInput.addEventListener("change", updateSubmitState);
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
          },
        })
        .then(({ error }) => {
          if (error) {
            throw new Error("magic_link_request_failed");
          }
          rememberPendingTermsAcceptance();
          form.reset();
          setStatus(
            status,
            "メールを確認してください。ログイン用リンクを送信できる場合は、まもなく届きます。",
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

  async function setupLogin(client, config) {
    const form = document.querySelector("[data-auth-form]");
    const sessionStatus = document.querySelector(
      "[data-login-session-status]",
    );
    if (!form || !sessionStatus) {
      return;
    }

    try {
      const result = await getLoginSession(client);
      if (!result || result.error) {
        throw new Error("session_lookup_failed");
      }
      if (result.data && result.data.session) {
        setStatus(
          sessionStatus,
          "ログイン済みです。マイページへ移動します。",
          "success",
        );
        await new Promise((resolve) => {
          window.setTimeout(resolve, AUTHENTICATED_REDIRECT_DELAY_MS);
        });
        window.location.replace(ACCOUNT_PATH);
        return;
      }
      sessionStatus.hidden = true;
    } catch {
      setStatus(
        sessionStatus,
        "ログイン状態を確認できませんでした。ログインが必要な場合は、以下からお試しください。",
        "error",
      );
    }

    enableLoginForm(client, config, form);
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

    setStatus(status, "メール認証を確認しています…");
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
      window.location.replace(ACCOUNT_PATH);
    } catch {
      setStatus(
        status,
        "認証を完了できませんでした。リンクの期限を確認し、もう一度ログインしてください。",
        "error",
      );
      retry.hidden = false;
    }
  }

  async function setupAccount(client) {
    const loading = document.querySelector("[data-account-loading]");
    const content = document.querySelector("[data-account-content]");
    const email = document.querySelector("[data-account-email]");
    const emailVerified = document.querySelector("[data-account-email-verified]");
    const membershipStatus = document.querySelector("[data-membership-status]");
    const createdAt = document.querySelector("[data-account-created-at]");
    const latestTermsVersion = document.querySelector("[data-latest-terms-version]");
    const latestTermsAcceptedAt = document.querySelector(
      "[data-latest-terms-accepted-at]",
    );
    const termsHistory = document.querySelector("[data-terms-history]");
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

    email.textContent = session.user && session.user.email
      ? session.user.email
      : "確認済み";
    emailVerified.textContent =
      session.user && session.user.email_confirmed_at
        ? "認証済み"
        : "確認状態を取得できません";
    content.hidden = false;
    logout.disabled = false;
    deleteStart.disabled = false;

    const renderTermsHistory = (acceptances) => {
      termsHistory.replaceChildren();
      if (!acceptances.length) {
        const empty = document.createElement("li");
        empty.textContent = "同意履歴はありません";
        termsHistory.append(empty);
        return;
      }
      for (const acceptance of acceptances) {
        const item = document.createElement("li");
        item.textContent =
          `${acceptance.version}（${formatTimestamp(acceptance.accepted_at)}）`;
        termsHistory.append(item);
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
            .select(
              "membership_status,latest_terms_version," +
                "latest_terms_accepted_at,created_at",
            )
            .single(),
          client
            .from("terms_acceptances")
            .select("document_type,version,accepted_at,source")
            .order("accepted_at", { ascending: false }),
          client
            .from("legal_document_versions")
            .select("version,effective_at")
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

      membershipStatus.textContent = profile.membership_status;
      createdAt.textContent = formatTimestamp(profile.created_at);
      latestTermsVersion.textContent =
        profile.latest_terms_version || "未同意";
      latestTermsAcceptedAt.textContent = formatTimestamp(
        profile.latest_terms_accepted_at,
      );
      renderTermsHistory(acceptances);

      const requiresConsent =
        profile.membership_status === "pending_terms" ||
        !hasCurrentAcceptance;
      consentPanel.hidden = !requiresConsent;
      consentInput.checked = false;
      consentButton.disabled = true;
      setStatus(consentStatus, "");
      loading.hidden = true;
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
        await loadAccountData();
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
      await loadAccountData();
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
      await setupAccount(client);
    }
  }

  void start();
})();
