# Phase 4 利用者別LINE通知 Rollout Runbook

## 1. 現在地

2026-08-28時点で、次の実装と本番単一会員canaryまで完了している。

- raw bodyの署名をJSON parse前に検証するMessaging API webhook
- webhook event IDによる重複排除と、順序逆転に耐えるfollow/unfollow反映
- 既存notification rule matchingを共用するLINE候補生成
- `user_id`、`channel`、`slot_id`単位の共通重複防止と、email/LINE workerのchannel分離
- LINE Pushの固定retry key、recipient/payload再確認、bounded retry
- 送信直前の月間使用量確認と180通guard
- shadow no-write、enqueue/claim/送信直前の単一会員canary、最大20会員の限定β、
  全会員許可のfail-closed gate
- 空き枠を捏造しない、固定文面の共通queue canary test job
- 架空利用者を使ったcross-user isolation、RLS/Grant、rollback、retentionのpgTAP
- 全migrationの再適用、Supabase database lint、security/performance advisor
- 本番migration、2 Edge Functions、必要なdelivery secretの反映
- `mie.masa@me.com`だけを対象にした固定文面queue canary 1通の実機受信
- LINE API `accepted`、attempt 1、retry 0、duplicate 0、使用量19→20、email副作用0
- Messaging API channel secretと既存GAS URLをSupabase secretへ反映
- LINE WebhookをSupabase署名検証bridgeへ切り替え、空payload preflight、LINE Verify、
  Use webhook、Webhook redeliveryを有効化
- 既存GASへraw bodyと署名を保持した転送が2xxになることをpreflightで確認
- `line-webhook-retention-cleanup`を03:22 JSTの日次cronとして作成し、manual zero-deleteを確認
- 同じRPCの一時cron smokeが3回連続で成功することを確認し、一時jobを削除
- Actions run `33036338712`でshadow-onlyを実行し、1 rule・12 slotsを評価、候補0、
  LINE/email queue変化0、Push 0、commit/Pages deploy 0を確認

LINE delivery gateはcanary後にOFFへ戻した。既存GASの実メッセージ応答確認は、用途上の
重要度が低いという所有者判断により2026-08-27に省略した。限定βまたは全会員向けdeliveryは
未開始であり、提供中と表示しない。PR #68はmainへ統合済みで、約20時間のshadow観測では
queue書込、Push、email副作用、異常終了は0だった。複数UUIDのserver-side allowlistは
前方migrationとして実装中であり、本番反映前は従来gateだけが有効である。

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
3. GitHub側で単一canary、限定β allowlist、全会員許可のいずれか1モードだけが有効である
4. GitHub Variable `ENABLE_USER_LINE_DISPATCH=true`
5. Supabase Edge Function側でも同じ1モードだけが有効である
6. Supabase Edge Function secret `ENABLE_USER_LINE_NOTIFICATIONS=true`
7. 連携、会員状態、通知条件、lease、recipient、payload fingerprintの送信直前再確認に成功する
8. LINE公式APIの月間使用量が`LINE_MONTHLY_PUSH_LIMIT`未満である

shadow modeは候補を評価して集計だけを返し、queueへ書き込まない。live enqueueとworkerは
canary、allowlist、allow-allのどれもない場合、または複数モードが同時に指定された場合に
失敗する。allowlistは一般APIへ公開しない`private` schemaへUUIDだけを保存し、operator RPC、
enqueue、claim、送信直前の各境界で最大20会員を強制する。空または20会員超のallowlistでは
Pushへ進まない。

単一canaryから直接`LINE_NOTIFICATION_ALLOW_ALL=true`へ進めない。

## 4. SecretとVariable

### 4.1 Supabase Edge Function secrets

| 名前 | 用途 |
| --- | --- |
| `LINE_MESSAGING_CHANNEL_SECRET` | Messaging API channelのwebhook署名検証。LINE Login channel secretとは別物 |
| `LINE_WEBHOOK_BRIDGE_ENABLED` | 既存GASへのpass-throughを厳密な`true`/`false`で制御。切替前は`false` |
| `LINE_LEGACY_WEBHOOK_URL` | 既存GAS deployment URL。`script.google.com/macros/s/.../exec`だけ許可 |
| `LINE_CHANNEL_ACCESS_TOKEN` | Messaging API Pushと月間使用量取得 |
| `LINE_DELIVERY_WORKER_SECRET` | GitHub ActionsからLINE workerだけを呼び出す高entropy bearer secret |
| `LINE_DELIVERY_PAYLOAD_HMAC_KEY` | recipientとpayloadの整合性fingerprint。worker secretと別の値 |
| `LINE_MONTHLY_PUSH_LIMIT` | ハード上限`180`。1以上180以下 |
| `LINE_NOTIFICATION_CANARY_USER_ID` | worker claimと送信直前再検証が強制する単一canary UUID |
| `LINE_NOTIFICATION_USE_ALLOWLIST` | 限定βだけ`true`。canary、allow-allとの同時有効は拒否 |
| `LINE_NOTIFICATION_ALLOW_ALL` | 全会員向けの別承認点まで`false` |
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
LINE_NOTIFICATION_USE_ALLOWLIST=false
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
gh variable set LINE_NOTIFICATION_USE_ALLOWLIST --body "false"
gh variable set LINE_NOTIFICATION_ALLOW_ALL --body "false"
gh variable set ENABLE_SCHEDULED_RUNS --body "false"
```

既存emailのVariableは変更しない。対象PRをmergeして`main`を取得した後、migrationを
dry-runし、対象が`20260828001723_add_line_notification_rollout_allowlist.sql`だけであることを確認する。

```powershell
npx --yes supabase@2.115.0 db push --linked --skip-vault --dry-run
npx --yes supabase@2.115.0 db push --linked --skip-vault --yes
npx --yes supabase@2.115.0 db push --linked --skip-vault --dry-run
```

最後の結果が`Remote database is up to date.`になるまで次へ進まない。

### 5.2 Secret設定とFunction deploy

Git管理外の`.env.line-delivery.local`を作り、4つの秘密値と単一canary UUID、次の3値だけを置く。

```text
LINE_NOTIFICATION_CANARY_USER_ID=<Supabase Auth UUID>
LINE_MONTHLY_PUSH_LIMIT=180
LINE_NOTIFICATION_USE_ALLOWLIST=false
LINE_NOTIFICATION_ALLOW_ALL=false
ENABLE_USER_LINE_NOTIFICATIONS=false
LINE_WEBHOOK_BRIDGE_ENABLED=false
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

既存GASを直接上書きしない。現在のGAS URLを`LINE_LEGACY_WEBHOOK_URL`へ保存し、
`LINE_MESSAGING_CHANNEL_SECRET`を設定する。bridgeを`true`にしてFunctionをdeployした後、
LINEから受信したものと同じ形式の署名付き空payloadをFunctionへ直接送り、Supabaseの
署名検証とGASの2xxを同時に確認する。raw body、signature、secret、GAS URLをログへ出さない。

preflight成功後に限り、LINE Developers ConsoleのMessaging API channelで次をWebhook URLへ設定する。

```text
https://<project-ref>.supabase.co/functions/v1/line-messaging-webhook
```

Verifyを成功させ、Use webhookを有効のまま維持し、Webhook redeliveryを有効にする。
既存GASの無害なコマンドを1回送り、従来応答が変わらないことを実機確認する。その後、
block/unfollowとfollow/block解除をテストし、連携状態だけが反映されることを確認する。

bridgeが2xxを返さない、既存GAS応答が変わる、LINE側error statisticsが増える場合は、
LINE Developers ConsoleのWebhook URLを直前のGAS URLへ戻す。FunctionやDBを巻き戻さず、
bridge secretを`false`に戻して前方修正する。

ここまで完了したら`ENABLE_SCHEDULED_RUNS`を作業前の値へ戻す。LINEの4 Variableは
初期値のままなので、利用者別LINE Pushは発生しない。

## 6. 段階有効化

### Stage 1: shadow no-write

```powershell
gh variable set ENABLE_USER_LINE_ENQUEUE --body "true"
gh variable set LINE_NOTIFICATION_SHADOW_MODE --body "true"
gh variable set ENABLE_USER_LINE_DISPATCH --body "false"
gh variable set LINE_NOTIFICATION_USE_ALLOWLIST --body "false"
gh variable set LINE_NOTIFICATION_ALLOW_ALL --body "false"

gh workflow run update-availability.yml `
  --ref feature/user-line-notifications `
  -f dry_run=true `
  -f line_shadow_only=true
```

`line_shadow_only=true`はLINE enqueueへ渡すshadow modeを強制し、email enqueue/dispatch、
LINE dispatch、availability commit、Pages deployを抑止する。PRのmerge前でもこの手動runで
条件一致を1回観察できる。Actionsには件数集計だけが出ること、
`notification_messages`と`notification_delivery_items`へ`channel='line'`の新規行が
作られないことを確認する。

2026-08-27のrun `33036338712`では、1 rule・12 slotsを評価し、match/enqueue/eligible
candidateはいずれも0、DB insert/linkも0だった。前後のLINE messageは1件、LINE delivery
itemは0件、email message/delivery itemは各1件のままで変化しなかった。

### Stage 2: 単一会員enqueue

GitHubとSupabase Edge Functionの両方の`LINE_NOTIFICATION_CANARY_USER_ID`を
canary本人のSupabase Auth UUIDへ設定する。Supabase側の
`LINE_NOTIFICATION_USE_ALLOWLIST=false`と`LINE_NOTIFICATION_ALLOW_ALL=false`も確認する。
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

既存queueだけを送る手動確認では、通常のavailability更新workflowをlive modeで流さず、
副作用を限定したdispatch-only modeを使用する。

```powershell
gh workflow run update-availability.yml `
  --ref main `
  -f dry_run=false `
  -f line_shadow_only=false `
  -f line_dispatch_only=true
```

`line_dispatch_only=true`はスクレイピング、テスト、email enqueue/dispatch、LINE enqueue、
Artifact、availability commit、Pages deployをすべて抑止する。`dry_run=true`または
`line_shadow_only=true`との同時指定はfail closedする。

安全な実空き候補がない場合は、service-roleの
`enqueue_line_canary_test(canary_user_id, message_id)`を1回だけ実行する。
`message_id`はoperatorが生成したUUIDを保持し、同じoperationでは必ず再利用する。
このtest jobは「【テスト通知】鹿児島テニス空き情報 LINE通知の動作確認です。」
だけを保持し、`notification_delivery_items`へ架空の空き枠を作らない。

- canary端末へ期待した空き通知が1通届く
- messageは`accepted`または重複再試行時の`accepted_retry`になる
- 同じmessageを再実行しても重複Pushしない
- LINE月間使用量が想定どおり増える
- email通知とavailability/Pagesが正常である

確認後は両方のdispatch gateを再び`false`へ戻し、24時間のaggregate観察を行う。

### Stage 4: 限定β

allowlist migration、Edge Function、workflowをmainへ統合して本番反映した後にだけ開始する。
切替中はenqueue、dispatch、Edge Function最終gateをすべて停止し、5分のlease失効後に
`active_processing_count=0`を確認する。次にSupabase SQL Editorまたは同等の信頼済みDB管理経路から、
private管理関数へ1〜20件のSupabase Auth UUIDを配列で渡す。この関数はData APIの
`service_role`を含むアプリケーションroleから実行できない。リストを原子的に置換し、
外れた会員の未送信backlogを取消す。

```sql
select *
from private.replace_line_notification_beta_allowlist(
  array[
    '<beta-member-auth-uuid-1>'::uuid,
    '<beta-member-auth-uuid-2>'::uuid
  ]
);
```

`allowlisted_count`が依頼件数と一致し、`cancelled_message_count`が想定内であることを確認する。
UUIDを通常ログ、Issue、PR本文へ記録しない。GitHubとSupabaseのcanary UUIDを解除し、両側で
`LINE_NOTIFICATION_USE_ALLOWLIST=true`、`LINE_NOTIFICATION_ALLOW_ALL=false`にそろえる。

最初は`LINE_NOTIFICATION_SHADOW_MODE=true`、dispatch OFFで手動runし、eligible件数が
allowlist会員だけでqueue変化0であることを確認する。次にshadowをOFF、dispatchはOFFのまま
1回enqueueし、allowlist外のdelivery item/messageが0であることを集計確認する。最後に
Supabase最終gate、GitHub dispatchの順で有効化する。少数会員で1回だけPushを確認した後は
異常、重複、quota、queue滞留、email副作用をaggregateで観察する。全会員許可は別の承認点とする。

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
限定βを終了する場合は、その後に信頼済みDB管理経路から
`private.replace_line_notification_beta_allowlist(array[]::uuid[])`で
allowlistを空にし、GitHubとSupabaseの`LINE_NOTIFICATION_USE_ALLOWLIST=false`へ戻す。

webhookはblock/unfollow状態を安全に保つため、Push停止時も原則として維持する。
bridge切替中のwebhook自体に問題がある場合は停止ではなく、まずWebhook URLを直前の
GAS URLへ戻して既存Botを保護する。

DB migrationは前方修正を原則とし、既存queue schemaやenumを巻き戻さない。

## 8. Retentionと通常監視

- 共通の`cleanup_email_notification_history(1000)`はchannelに依存せず、既存の日次cronで
  90日超の完了済みLINE message/delivery itemも安全条件下でcleanupする。
- `cleanup_line_webhook_events(1000)`は90日超のwebhook event ledgerをbounded削除する。
  2026-08-27にmanual zero-deleteを確認し、既存email cleanupを変更せず障害範囲を分離した
  `line-webhook-retention-cleanup`を毎日03:22 JST（`22 18 * * *`）に作成した。
  同じcommandを使う10秒間隔の一時smoke jobは3回連続で成功し、その後削除した。
  恒久jobの初回定時実行結果は翌日以降に`cron.job_run_details`で確認する。
- 通常ログはcandidate、eligible、claimed、accepted、retry、failure、cancelled、quotaの
  集計だけとし、会員UUID、LINE user ID、空き枠payload、tokenを出さない。
- 180通到達時はworkerがclaim前に停止する。email channelは独立して継続する。

## 9. Production acceptance完了条件

- webhook署名不正を拒否し、duplicate replayがno-opになる
- 空のVerify payloadとmessage-only eventが同じraw body・署名で既存GASへ届く
- GASの2xxだけを成功とし、timeout/network/非2xxでLINEへ5xxを返す
- bridge切替後も既存GASの実機応答が変わらず、error statisticsが増えない
- follow/unfollowの順序逆転で古いeventが現在状態を上書きしない
- shadowでLINE queueへ書き込まない
- 単一canary以外へenqueue・Pushしない
- 限定βでは1〜20会員のprivate allowlist以外へenqueue・claim・Pushしない
- allowlistから外した会員の未送信backlogと送信直前leaseが取消される
- 旧RPC signatureをservice-roleから実行できない
- email workerがLINE messageを、LINE workerがemail messageをclaimしない
- 同一messageのtimeout/5xx再試行で重複Pushしない
- block、解除、退会、通知停止後は送信しない
- 180通到達時に新規Pushせず、emailとPagesを止めない
- rollback後に未送信LINE backlogとactive leaseが残らない
- secret、LINE user ID、payload本文がrepository、Actions log、Artifact、公開Pagesにない

単一会員canaryの項目は完了済みである。限定βは追加3項目を含む証跡を残した後にだけ
完了扱いとする。
