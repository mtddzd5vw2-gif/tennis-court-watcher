# Phase 4 LINE account link Runbook

## 1. Scope

This runbook deploys the server-side boundary that links an already
authenticated active member to one LINE account.

Included:

- `start-line-account-link`: authenticated POST that creates hashed one-time
  state and nonce values and returns a LINE Login authorization URL.
- `complete-line-account-link`: public LINE callback that exchanges the
  one-time code, verifies the ID token, checks friendship, and persists the
  one-to-one link.
- `unlink-line-account`: authenticated, explicitly confirmed, idempotent
  unlink operation.
- `20260821073000_add_line_account_link_rpcs.sql`: service-role-only atomic
  session and link RPCs.

Not included:

- My Page buttons and result messages.
- LINE webhook handling.
- LINE notification queue or push worker.
- Enabling LINE delivery for any member.

Existing email notification delivery remains unchanged.

## 2. Provider preparation

Create a LINE Login channel under the same LINE provider as the existing
Messaging API channel. Link that LINE Login channel to the existing LINE
Official Account.

Register this exact callback URL on the LINE Login channel:

```text
https://<SUPABASE_PROJECT_REF>.supabase.co/functions/v1/complete-line-account-link
```

The authorization request uses `scope=openid profile` and
`bot_prompt=aggressive`. Email scope is intentionally not requested.

Official references:

- [Integrating LINE Login with a web app](https://developers.line.biz/en/docs/line-login/integrate-line-login/)
- [LINE Login v2.1 API](https://developers.line.biz/en/reference/line-login/)
- [Add a LINE Official Account as a friend](https://developers.line.biz/en/docs/line-login/link-a-bot/)

## 3. Edge Function secrets

Configure these values only in Supabase Edge Function Secrets. Do not place
values in GitHub, local `.env` files committed to the repository, logs, or
browser configuration.

| Name | Value |
| --- | --- |
| `LINE_LOGIN_CHANNEL_ID` | LINE Login channel ID, not the Messaging API channel ID |
| `LINE_LOGIN_CHANNEL_SECRET` | LINE Login channel secret |
| `LINE_LOGIN_CALLBACK_URL` | Exact registered Supabase callback URL above |
| `LINE_LINK_RESULT_URL` | `https://tenniscourtwatcher.com/account/index.html` |

`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are hosted Edge Function
built-ins. The service role key must never be exposed to the browser.

## 4. Pre-deployment validation

Keep scheduled availability runs enabled; this migration and these Functions
do not change scraping or existing email delivery. No LINE delivery feature
flag is enabled by this rollout.

```powershell
git switch main
git pull --ff-only origin main

npx --yes supabase@2.115.0 db push `
  --linked `
  --skip-vault `
  --dry-run
```

The dry-run must list only:

```text
20260821073000_add_line_account_link_rpcs.sql
```

## 5. Production deployment

Apply the migration before deploying the Functions:

```powershell
npx --yes supabase@2.115.0 db push `
  --linked `
  --skip-vault `
  --yes

npx --yes supabase@2.115.0 functions deploy start-line-account-link `
  --project-ref <SUPABASE_PROJECT_REF> `
  --use-api

npx --yes supabase@2.115.0 functions deploy complete-line-account-link `
  --project-ref <SUPABASE_PROJECT_REF> `
  --use-api

npx --yes supabase@2.115.0 functions deploy unlink-line-account `
  --project-ref <SUPABASE_PROJECT_REF> `
  --use-api
```

The committed `supabase/config.toml` requires gateway JWT verification for
start and unlink, and disables it only for the LINE callback. The callback
authenticates through the one-time state, the LINE authorization code, the
verified ID token, and the stored nonce hash.

## 6. Acceptance boundary

Before the My Page UI PR, verify these server boundaries without storing or
printing tokens:

1. Start and unlink return `401` without a user JWT.
2. Callback rejects POST with `405`.
3. Callback with missing or malformed state redirects to Account with only
   `line_link=failed`.
4. No Function log includes a URL, authorization code, state, nonce, LINE user
   ID, user access token, channel secret, email address, or Supabase JWT.
5. Supabase security and performance advisors show no new finding caused by
   this migration.
6. Existing availability and email notification workflows continue to pass.

Full positive LINE Login acceptance is performed after the My Page UI is
added. Confirm on a smartphone that:

1. An active signed-in member starts LINE Login.
2. LINE displays the linked Official Account add-friend step.
3. Callback returns to My Page without a Supabase token in the URL.
4. The safe status RPC reports linked or friend action required.
5. A second member cannot claim the same LINE account.
6. Explicit unlink removes the active notification relationship and is safe
   to repeat.

## 7. Rollback

If the callback or provider configuration fails, remove the My Page entry
point or leave it unreleased and redeploy the previous Function version. Do
not roll back the forward migration and do not edit applied migration files.
The new RPCs are unreachable by browser roles and do not affect email.

If a production link must be disabled while investigating, unlink it through
the trusted Function or set its status to `unlinked` through an audited
operator procedure. Never copy the LINE user ID into an issue or log.
