# Phase 4 利用者別LINE通知 Rollout Runbook

## 1. 現在地

2026-08-21時点で、次の実装と隔離ローカル環境での検証が完了している。

- raw bodyの署名をJSON parse前に検証するMessaging API webhook
- webhook event IDによる重複排除と、順序逆転に耐えるfollow/unfollow反映
- 既存notification rule matchingを共用するLINE候補生成
- `user_id`、`channel`、`slot_id`単位の共通重複防止と、email/LINE workerのchannel分離
- LINE Pushの固定retry key、recipient/payload再確認、bounded retry
- 送信直前の月間使用量確認と180通guard
- shadow no-write、単一会員canary、全会員許可のfail-closed gate
- 架空利用者を使ったcross-user isolation、RLS/Grant、rollback、retentionのpgTAP
- 全migrationの再適用、Supabase database lint、security/performance advisor

本番Supabaseへのmigration、Edge Function deploy、LINE Developers Consoleのwebhook登録、
shadow/canary、実配信acceptanceは未実施である。完了するまで利用者向けLINE配信を
提供中と表示しない。

## 2. 変更しない境界

- Phase 0の単一宛先legacy LINE経路はPhase 3.4.3で退役済みであり、復活させない。
- 管理者も一般会員と同じnotification rule、queue、workerを利用する。
- 既存email matching、enqueue、dispatchの有効化状態とcredentialは変更しない。
- LINE障害、上限到達、webhook障害はavailability取得、Pages公開、email通知を止めない。
- LINE channel access tokenとMessaging API channel secretをGitHub availability workflowへ渡さない。

## 3. 安全装置

LINE Pushが実行されるには、すべての条件が同時に必要である。

1. GitHub Variable `ENABLE_USER_LINE_ENQUEUE=true`
2. GitHub Variable `LINE_NOTIFICATION_SHADOW_MODE=false`
3. GitHub Secret `LINE_NOTIFICATION_CANARY_USER_ID`が対象会員のSupabase Auth UUIDと一致する、
   またはGitHub Variable `LINE_NOTIFICATION_ALLOW_ALL=true`
4. GitHub Variable `ENABLE_USER_LINE_DISPATCH=true`
5. Supabase Edge Function secret `ENABLE_USER_LINE_NOTIFICATIONS=true`
6. 連携、会員状態、通知条件、lease、recipient、payload fingerprintの送信直前再確認に成功する
7. LINE公式APIの月間使用量が`LINE_MONTHLY_PUSH_LIMIT`未満である

shadow modeは候補を評価して集計だけを返し、queueへ書き込まない。live enqueueは
canaryも明示的な全会員許可もない場合に失敗する。`allow all`とcanaryの同時指定も
設定ミスとして拒否する。

現実装のrollout allowlistは単一会員だけである。複数会員の限定βを行う前に、
複数UUIDのserver-side allowlistを別の前方変更として実装・検証する。
単一canaryから直接`LINE_NOTIFICATION_ALLOW_ALL=true`へ進めない。

## 4. SecretとVariable

### 4.1 Supabase Edge Function secrets

| 名前 | 用途 |
| --- | --- |
| `LINE_MESSAGING_CHANNEL_SECRET` | Messaging API channelのwebhook署名検証。LINE Login channel secretとは別物 |
| `LINE_CHANNEL_ACCESS_TOKEN` | Messaging API Pushと月間使用量取得 |
| `LINE_DELIVERY_WORKER_SECRET` | GitHub ActionsからLINE workerだけを呼び出す高entropy bearer secret |
| `LINE_DELIVERY_PAYLOAD_HMAC_KEY` | recipientとpayloadの整合性fingerprint。worker secretと別の値 |
| `LINE_MONTHLY_PUSH_LIMIT` | 初期値`180`。1以上200以下 |
| `ENABLE_USER_LINE_NOTIFICATIONS` | Edge Function内の最終gate。初期値`false` |

`SUPABASE_URL`と`SUPABASE_SERVICE_ROLE_KEY`はSupabase hosted Edge Functionへ自動提供される。

### 4.2 GitHub Secrets

| 名前 | 用途 |
| --- | --- |
| `LINE_DELIVERY_WORKER_SECRET` | Supabase側と完全に同じ値 |
| `LINE_NOTIFICATION_CANARY_USER_ID` | 単一canaryのSupabase Auth UUID。通常ログへ出さない |

### 4.3 GitHub Variablesの初期値

```text
ENABLE_USER_LINE_ENQUEUE=false
ENABLE_USER_LINE_DISPATCH=false
LINE_NOTIFICATION_SHADOW_MODE=true
LINE_NOTIFICATION_ALLOW_ALL=false
```

既存の`ENABLE_NOTIFICATION_MATCHING`と`ENABLE_SCHEDULED_RUNS`はLINE専用設定ではない。
本番変更中だけscheduled runを停止し、作業後に元の値へ戻す。

## 5. 本番導入順

### 5.1 事前停止と確認

```powershell
$ErrorActionPreference = "Stop"

gh variable set ENABLE_USER_LINE_ENQUEUE --body "false"
gh variable set ENABLE_USER_LINE_DISPATCH --body "false"
gh variable set LINE_NOTIFICATION_SHADOW_MODE --body "true"
gh variable set LINE_NOTIFICATION_ALLOW_ALL --body "false"
gh variable set ENABLE_SCHEDULED_RUNS --body "false"
```

既存emailのVariableは変更しない。対象PRをmergeして`main`を取得した後、migrationを
dry-runし、対象が`20260821095256_add_line_notification_delivery.sql`だけであることを確認する。

```powershell
npx --yes supabase@2.115.0 db push --linked --skip-vault --dry-run
npx --yes supabase@2.115.0 db push --linked --skip-vault --yes
npx --yes supabase@2.115.0 db push --linked --skip-vault --dry-run
```

最後の結果が`Remote database is up to date.`になるまで次へ進まない。

### 5.2 Secret設定とFunction deploy

Git管理外の`.env.line-delivery.local`を作り、4つの秘密値と次の2値だけを置く。

```text
LINE_MONTHLY_PUSH_LIMIT=180
ENABLE_USER_LINE_NOTIFICATIONS=false
```

秘密値をterminal historyへ直接書かず、env fileから設定する。

```powershell
npx --yes supabase@2.115.0 secrets set --env-file .env.line-delivery.local
npx --yes supabase@2.115.0 functions deploy line-messaging-webhook --use-api
npx --yes supabase@2.115.0 functions deploy dispatch-line-notifications --use-api
```

`.env.line-delivery.local`は`.gitignore`対象である。`git status --short`に出ないこと、
Function secret名だけが存在し値を表示しないことを確認する。

### 5.3 LINE webhook登録

LINE Developers ConsoleのMessaging API channelで、次をwebhook URLへ設定する。

```text
https://<project-ref>.supabase.co/functions/v1/line-messaging-webhook
```

Verifyを成功させ、Use webhookを有効にする。Webhook redeliveryも有効にする。
block/unfollowとfollow/block解除をテストし、連携状態だけが反映されることを確認する。
raw payload、LINE user ID、signature、tokenをログへ出さない。

ここまで完了したら`ENABLE_SCHEDULED_RUNS`を作業前の値へ戻す。LINEの4 Variableは
初期値のままなので、利用者別LINE Pushは発生しない。

## 6. 段階有効化

### Stage 1: shadow no-write

```powershell
gh variable set ENABLE_USER_LINE_ENQUEUE --body "true"
gh variable set LINE_NOTIFICATION_SHADOW_MODE --body "true"
gh variable set ENABLE_USER_LINE_DISPATCH --body "false"
gh variable set LINE_NOTIFICATION_ALLOW_ALL --body "false"
```

条件一致がある通常runを1回観察する。Actionsには件数集計だけが出ること、
`notification_messages`と`notification_delivery_items`へ`channel='line'`の新規行が
作られないことを確認する。

### Stage 2: 単一会員enqueue

`LINE_NOTIFICATION_CANARY_USER_ID`をcanary本人のSupabase Auth UUIDへ設定する。
canaryはactive会員、LINE連携`active`、有効な通知条件を持つ必要がある。

```powershell
gh variable set LINE_NOTIFICATION_SHADOW_MODE --body "false"
gh variable set ENABLE_USER_LINE_DISPATCH --body "false"
```

通常runを1回実行し、LINE queueがcanary 1会員分だけ増え、他会員・email messageへ
変化がないことをservice-roleで集計確認する。recipient IDやpayload本文を表示しない。

### Stage 3: 単一会員Push

Supabaseの`ENABLE_USER_LINE_NOTIFICATIONS=true`を設定し、その後でGitHubの
`ENABLE_USER_LINE_DISPATCH=true`を設定する。1回だけworkerを実行し、次を確認する。

- canary端末へ期待した空き通知が1通届く
- messageは`accepted`または重複再試行時の`accepted_retry`になる
- 同じmessageを再実行しても重複Pushしない
- LINE月間使用量が想定どおり増える
- email通知とavailability/Pagesが正常である

確認後は両方のdispatch gateを再び`false`へ戻し、24時間のaggregate観察を行う。

### Stage 4: 限定β

複数UUIDのserver-side allowlist実装とcross-user testを別PRで完了するまで開始しない。
allowlist導入後もshadow、限定β、24時間観察を行い、全会員許可は別の承認点とする。

## 7. Rollback

誤配信疑い、quota異常、provider障害、queue滞留、recipient不一致が1件でもあれば、
次の順で停止する。

1. GitHub Variable `ENABLE_USER_LINE_DISPATCH=false`
2. GitHub Variable `ENABLE_USER_LINE_ENQUEUE=false`
3. Supabase Edge Function secret `ENABLE_USER_LINE_NOTIFICATIONS=false`
4. 最長5分のworker leaseが失効するまで待つ
5. service-roleとして次を実行し、`active_processing_count=0`を確認する

```sql
select * from public.cancel_line_notification_backlog();
```

このRPCは未送信の`pending`/`retry`とlease失効済み`processing`だけを取消し、
送信受理済み履歴とemail channelを変更しない。`active_processing_count`が0でない場合は
再実行せず、残るleaseが失効してからもう一度確認する。

webhookはblock/unfollow状態を安全に保つため、Push停止時も原則として維持する。
webhook自体に問題がある場合だけLINE Developers Consoleで停止する。

DB migrationは前方修正を原則とし、既存queue schemaやenumを巻き戻さない。

## 8. Retentionと通常監視

- 共通の`cleanup_email_notification_history(1000)`はchannelに依存せず、既存の日次cronで
  90日超の完了済みLINE message/delivery itemも安全条件下でcleanupする。
- `cleanup_line_webhook_events(1000)`は90日超のwebhook event ledgerをbounded削除する。
  本番導入時に既存retention cronへ追加し、manual zero-deleteと初回成功を確認する。
- 通常ログはcandidate、eligible、claimed、accepted、retry、failure、cancelled、quotaの
  集計だけとし、会員UUID、LINE user ID、空き枠payload、tokenを出さない。
- 180通到達時はworkerがclaim前に停止する。email channelは独立して継続する。

## 9. Production acceptance完了条件

- webhook署名不正を拒否し、duplicate replayがno-opになる
- follow/unfollowの順序逆転で古いeventが現在状態を上書きしない
- shadowでLINE queueへ書き込まない
- 単一canary以外へenqueue・Pushしない
- email workerがLINE messageを、LINE workerがemail messageをclaimしない
- 同一messageのtimeout/5xx再試行で重複Pushしない
- block、解除、退会、通知停止後は送信しない
- 180通到達時に新規Pushせず、emailとPagesを止めない
- rollback後に未送信LINE backlogとactive leaseが残らない
- secret、LINE user ID、payload本文がrepository、Actions log、Artifact、公開Pagesにない

全項目の証跡を残した後に限り、単一会員canaryを完了扱いとする。
