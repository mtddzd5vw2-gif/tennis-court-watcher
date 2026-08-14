# Phase 3.5a Resend Webhook Runbook

## 1. 目的と現在の状態

Resend APIが送信要求を受理した後の`sent`、`delivery_delayed`、`delivered`、`failed`、`bounced`、`complained`、`suppressed`を、署名済みwebhookから`notification_messages`と`notification_provider_events`へ反映する。Resend webhookはat-least-onceかつout-of-orderなので、`svix-id`をeventの冪等性key、top-level `created_at`をprovider順序の正とする。

Phase 3.5aのコード、forward migration、pgTAP、Deno test、静的pytestは完了した。production反映とcanary確認も完了し、現在は手順10の完了条件であるcanary後24〜48時間のaggregate観察中である。本runbookは確認済みの認証境界とrollback guardの記録として維持する。Phase 3.5b unsubscribeのproduction手順は[Phase 3 Email Unsubscribe Runbook](./PHASE3_EMAIL_UNSUBSCRIBE.md)へ分離する。

productionではmigration適用、webhook deploy、missing/invalid signatureの`401`、実署名付き外部Authメールの`ignored_unmatched 200`、通知canaryのsent→delivered、provider eventsのsent/delivered、duplicate replayの`stored_event_count=0`を確認済みである。

## 2. 実装境界

送信payloadには次のtagをexact provider JSONの一部として含める。

```json
{
  "tags": [
    { "name": "tcw_source", "value": "user_notification" },
    { "name": "tcw_message_id", "value": "<notification_messages.id>" }
  ]
}
```

tagを含む`JSON.stringify`結果を、既存設計どおりpayload HMAC fingerprintとResend POST bodyの双方へそのまま使う。同じmessageのretryでpayloadが変わることを許可しない。

webhook payloadから読むのは次だけである。

- top-level `type`
- top-level `created_at`
- `data.email_id`
- `data.tags.tcw_source`
- `data.tags.tcw_message_id`

raw webhook payload、recipient、from、subject、provider response、internal user ID、message IDはDBの追加列、レスポンス、通常ログへ保存・出力しない。ログとレスポンスは`outcome`、`event_type`、`stored_event_count`、`preference_disabled_count`だけに限定する。

## 3. 署名とHTTP契約

`resend-email-webhook`はJWTを要求せず、Resend webhook signing secretによるSvix署名を認証境界とする。`RESEND_WEBHOOK_SIGNING_SECRET`はSupabase Edge Function secretにだけ保存し、リポジトリやローカル設定例へ実値を置かない。

1. `POST`以外は`405`。
2. `Origin` headerがあれば`403`。
3. bodyは64 KiB以下。宣言値と実読込の双方で上限を確認し、超過は`413`。
4. `svix-id`、`svix-timestamp`、`svix-signature`が欠ける場合は`401`。
5. bodyはparse前にraw UTF-8として一度だけ取得し、固定版`svix` libraryで検証する。
6. missing/invalid signatureは`401`、secretやSupabase設定不備は`503`。
7. 署名済みだが不正なJSONは`400`、対応eventの必須field不正は`422`。
8. 対応外の正当な署名eventは`200 ignored_unsupported`。`email.opened`と`email.clicked`は購読しない。
9. DB/RPC失敗は`502 retryable_error`としてResendにretryさせる。
10. duplicate、ignored unmatched、correlation conflict、正常記録はいずれもretryで改善しないため`200`。

署名検証はResendが推奨するSvix方式を使い、`svix-id.timestamp.raw-body`に対する署名と5分のdelivery timestamp toleranceをlibraryへ任せる。Resendのevent `created_at`についてもDB RPCが5分を超える未来時刻を拒否する。

## 4. DB相関と状態順序

`record_resend_email_event`は`security definer`、空の`search_path`、service-role専用である。`PUBLIC`、`anon`、`authenticated`にはexecuteを許可しない。

message相関は次の順序で行う。

1. `data.email_id`と既存`notification_messages.provider_message_id`の一致を最優先する。
2. 一致しない場合だけ、`tcw_source=user_notification`かつ`tcw_message_id`がvalid UUIDならmessage IDを検索する。
3. tagで見つけたmessageのprovider IDがNULLなら、`provider_first_attempt_at`と`provider_payload_fingerprint`が両方ある場合だけprovider IDをbindする。
4. 既存provider IDが別値なら`correlation_conflict`とし、上書きもevent insertもしない。
5. 自アプリtagがなくprovider IDも一致しないeventは`ignored_unmatched`とし、保存しない。

正常に相関したeventは`notification_provider_events`へ正規化して保存し、`(provider, provider_event_id)`で重複排除する。message stateはwebhook到着順ではなく、同じmessageの`occurred_at DESC`で最新eventを選ぶ。同一timestampは次の固定priorityを使う。

```text
complained > suppressed > bounced > failed > delivered > delivery_delayed > sent
```

状態mappingは次のとおり。

| event | message status | provider status | error / timestamp |
| --- | --- | --- | --- |
| `email.sent` | `accepted` | `sent` | 既存値と3種のprovider event最古時刻の早い方を`accepted_at`へ設定 |
| `email.delivery_delayed` | `accepted` | `delivery_delayed` | 既存値と3種のprovider event最古時刻の早い方を`accepted_at`へ設定 |
| `email.delivered` | `delivered` | `delivered` | 同じ規則で`accepted_at`、`delivered_at=event時刻`、`failed_at=NULL` |
| `email.failed` | `failed_permanent` | `failed` | `resend_delivery_failed` |
| `email.bounced` | `bounced` | `bounced` | `resend_bounced` |
| `email.complained` | `complained` | `complained` | `resend_complained` |
| `email.suppressed` | `suppressed` | `suppressed` | `resend_suppressed` |

provider-authoritative eventを適用するときはworker leaseをclearする。bounce、complaint、suppressionを正常記録した場合だけpreferenceを無効化し、同名の正規化理由とevent時刻を保存する。failed/delayedでは無効化せず、deliveredでも自動再有効化しない。

## 5. ローカル検証

production操作の前に、リポジトリrootで次をすべて成功させる。

```powershell
& .\.venv\Scripts\python.exe -m pytest
supabase test db
deno test supabase/functions/dispatch-email-notifications/helpers_test.ts
deno test supabase/functions/resend-email-webhook/helpers_test.ts
deno check supabase/functions/dispatch-email-notifications/index.ts
deno check supabase/functions/resend-email-webhook/index.ts
deno fmt --check supabase/functions/dispatch-email-notifications supabase/functions/resend-email-webhook
& .\.venv\Scripts\python.exe -c "import tomllib, pathlib; tomllib.loads(pathlib.Path('supabase/config.toml').read_text(encoding='utf-8'))"
git diff --check
git status --short
git diff --stat
```

pgTAPにはaccepted→sent→delivered、delay/failure、3種の自動disable、disableしない条件、重複、順序逆転、direct/tag correlation、conflict/unmatched、table/RPC権限を含める。Deno testにはvalid/invalid signature、missing header、raw-body、method/origin/body size、payload検証、unsupported、DB retry、duplicate、非PIIログを含める。

## 6. sender payload変更前の必須guard

sender tag追加はexact payload fingerprintを変える。既に最初のResend試行を開始したmessageが`processing`または`retry_wait`に残っている状態でsenderを切り替えると、同じidempotency keyに別payloadを送ろうとして`provider_payload_changed`になる。このため、sender tagをproduction deployする直前に必ず次を実行する。

```sql
select status, count(*)
from public.notification_messages
where provider_first_attempt_at is not null
  and status in ('processing','retry_wait')
group by status;
```

結果は0件、すなわち0行でなければならない。1件でも返る場合はsender tag変更を本番deployしない。workerの通常処理でacceptedまたはterminalになるのを待ち、原因を確認してから再実行する。行を手動削除、status上書き、fingerprint消去で通過させない。

## 7. 人間が行うProduction rollout

次の順序を変えず、一段ごとに結果を記録する。

tagなし旧senderが稼働中のままwebhookを有効化すると、workerによる`provider_message_id`記録よりwebhookが先着した場合にtag fallbackできず、`ignored_unmatched`を`200`で確定する可能性がある。このraceを避けるため、Dashboard webhookはcorrelation tag追加済みsenderのdeploy後に初めて作成する。

1. 新しいforward migration `20260814000000_add_resend_delivery_feedback.sql`を適用し、RPCのowner、`search_path`、service-roleだけのexecute、table privilegeを確認する。
2. `RESEND_WEBHOOK_SIGNING_SECRET`を未設定のまま、sender workerとは別に`resend-email-webhook` Functionをdeployする。
3. test requestに対して`configuration_error`の`503`と非PIIログが返ることを確認する。
4. 上記in-flight guardを実行し、`processing`と`retry_wait`が0件、すなわち結果が0行であることを確認・記録する。1行でも返る場合はここで中止する。
5. correlation tag追加済み`dispatch-email-notifications`をdeployする。GitHub workflow、watchdog Cron、production Repository Variablesは変更しない。
6. sender deploy後に、Resend Dashboardでproduction Function URLを宛先とするwebhookを作成し、`email.sent`、`email.delivery_delayed`、`email.delivered`、`email.failed`、`email.bounced`、`email.complained`、`email.suppressed`の7 eventだけを購読する。`email.opened`と`email.clicked`は購読しない。
7. webhook作成時に表示または返却されるsigning secretを取得する。値はshell history、ログ、チケット、ドキュメントへ貼らない。
8. 取得した値を`RESEND_WEBHOOK_SIGNING_SECRET`としてSupabase Edge Function secretへ直ちに設定する。
9. 正しい署名が受理され、missingまたはinvalid signatureが`401`になる認証境界を確認する。
10. 内部test user 1名の1通だけでcanaryを行い、Resend API acceptance、provider event、`sent`→`delivered`、duplicate replayのno-op、ログにID・宛先・bodyがないことを確認する。その後24〜48時間、滞留、event type別件数、`ignored_unmatched`、`correlation_conflict`、Function 5xx、bounce/complaint/suppressionを集計で監視する。

手順6のwebhook作成から手順8のsecret設定までにeventが到着した場合、secret未設定のFunctionは`configuration_error`の`503`を返す。この応答はResendのretry対象となるため、設定完了後の再送で署名検証とcorrelationを行える。secret設定前のeventを`200 ignored_unmatched`で確定させない。

## 8. 停止・rollback

異常時はまずResend Dashboardのwebhookをdisableし、retry流入を止める。Functionを停止してもsenderのAPI acceptanceと既存retry設計は維持される。forward migrationと保存済みprovider eventをdrop/truncateしない。

sender tagを戻す操作もpayload fingerprint変更である。rollback前に同じin-flight guardを実行し、0行でなければsender payloadを戻さない。webhook停止だけで安全を確保できる場合はsenderを変更せず、原因調査と修正版のforward rolloutを優先する。

## 9. 参考仕様

- Resend Verify Webhooks Requests: <https://resend.com/docs/webhooks/verify-webhooks-requests>
- Resend Managing Webhooks: <https://resend.com/docs/webhooks/introduction>
- Resend Event Types: <https://resend.com/docs/webhooks/event-types>
- Resend Managing Tags: <https://resend.com/docs/dashboard/emails/tags>
