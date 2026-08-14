# Phase 3.5b Email Unsubscribe / Re-enable Runbook

## 1. 目的と状態

利用者別空き通知メールから、本人が安全にメール通知を停止できるようにする。通常の確認画面に加えてRFC 8058 one-click unsubscribeを提供し、本人opt-outとResend由来のbounce・complaint・suppressionを分離する。

Phase 3.5bのforward migration、sender、公開Edge Function、Account UI、pgTAP、Deno test、静的pytestはコード実装対象である。production migration、Function deploy、sender deploy、Pages deploy、secret変更、canary、commit、pushはこの実装作業に含めない。90日retention cleanupはPhase 3.5cで扱う。

Phase 3.5aはproduction反映・canary確認済みで、現在24〜48時間のaggregate観察中である。次を確認した。

- migration applied
- webhook deployed
- missing/invalid signatureは`401`
- 実署名付き外部Authメールは`ignored_unmatched 200`
- 通知canaryはsentからdeliveredへ遷移
- provider eventsにsent/deliveredを保存
- duplicate replayは`stored_event_count=0`

## 2. DBとcapability境界

`notification_email_unsubscribe_tokens`は`user_id`を主キーとし、uniqueなUUID token、`created_at`、`rotated_at`だけを保持する。メールアドレスは保持しない。既存の`notification_email_preferences`をmigrationでbackfillし、今後のpreference作成時はtriggerでtoken rowを作る。

token tableはRLSを有効にし、`PUBLIC`、`anon`、`authenticated`、`service_role`を含む直接table privilegeを付与しない。操作は空の`search_path`と完全修飾名を使うsecurity-definer RPCに限定する。

| RPC | 呼出しrole | 契約 |
| --- | --- | --- |
| `get_email_unsubscribe_token_for_message(uuid)` | service_roleのみ | 存在するemail messageの所有者tokenだけを返す |
| `email_unsubscribe_token_is_valid(uuid)` | service_roleのみ | 確認GETで存在を検証し、外部表示には真偽を出さない |
| `unsubscribe_email_notifications_by_token(uuid)` | service_roleのみ | valid、既にOFF、unknownを同じ`processed` outcomeにする |

本人opt-outでは`disabled_reason IS NULL`かつ現在ONの場合だけ`is_enabled=false`、`disabled_at=now()`へ変更する。既にOFFならtimestampを動かさない。`resend_bounced`、`resend_complained`、`resend_suppressed`を含む既存reasonとtimestampは上書きしない。

activeなauthenticated本人は従来どおり自分のpreferenceをSELECTし、`is_enabled`列だけUPDATEできる。通常の`false -> true`で、その利用者のemail messageに`processing`または`retry_wait`が1件でもあれば再有効化UPDATE全体を例外でrollbackする。この場合はpreferenceをOFFのまま、tokenとmessageのstatus、lease、fingerprintを変更しない。該当messageが0件の場合だけtokenをrotationする。`pending`は再有効化を妨げず、cancelもしない。まだprovider payload fingerprintが確定していないため、再有効化後の新tokenで通常送信する。provider suppression reasonがある行は既存CHECK constraintがONを拒否するためrotationしない。

## 3. Sender契約

senderはclaimしたmessageごとにtoken RPCを呼び、次のURLを一度だけ構築する。

```text
https://<project>.supabase.co/functions/v1/unsubscribe-email-notifications?token=<token>
```

本文末尾のtext/html双方へ「メール通知を停止する」を追加し、Resend JSONへ次を含める。

```json
{
  "headers": {
    "List-Unsubscribe": "<https://<project>.supabase.co/functions/v1/unsubscribe-email-notifications?token=<token>>",
    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"
  }
}
```

footer、headers、token、correlation tagsを含むexact provider JSONを一度だけserializeし、その同じ文字列をHMAC payload fingerprintとResend POST bodyの双方に使う。通常retryでは利用者tokenが変わらないためJSONも変わらない。`processing`または`retry_wait`がある間は再有効化とtoken rotationを拒否するため、fingerprint済みmessageのpayloadは変わらない。`pending`はまだfingerprintが確定していないため、rotation後のtokenで初回payloadを構築する。

token、unsubscribe URL、query string、`user_id`、メールアドレス、provider request bodyは通常ログへ出さない。

## 4. 公開Edge Function HTTP契約

`unsubscribe-email-notifications`は`verify_jwt=false`で公開し、UUID capabilityとservice-role RPCを認証・認可境界とする。レスポンスは`Cache-Control: no-store`とし、HTMLにはCSP、`Referrer-Policy: no-referrer`、`X-Content-Type-Options: nosniff`を付ける。

### GET

- token形式を検証し、正しいUUIDなら存在確認RPCを呼ぶ。
- preferenceを変更しない。
- 日本語の確認ページとPOST formを返す。
- valid、unknown、形式不正で画面上の説明を変えない。

### POST

- `application/x-www-form-urlencoded`だけを受け付ける。
- `Content-Length`が2048 bytesを超える場合は読込前に拒否し、長さ未指定のstreamも実読込が2048 bytesを超えた時点でreaderをcancelする。invalid UTF-8も`invalid_request`として拒否する。
- RFC 8058はquery tokenと`List-Unsubscribe=One-Click`を受け、`200`の空bodyを返す。
- 人間操作は確認formのbody tokenを受け、日本語の「メール通知を停止しました」画面を返す。
- valid、既にOFF、unknown、形式不正は外部から利用者存在を判別できないgeneric successにする。
- RPC/DB障害は成功に見せず`502`とし、再試行可能な障害として扱う。
- GET/POST以外は`405`と`Allow: GET, POST`を返す。

通常ログは`outcome`とinteraction種別のaggregateだけとし、token、URL、query、利用者ID、メールアドレスを含めない。

## 5. Account UI

通知条件ページで本人の`notification_email_preferences`を表示する。

- `disabled_reason IS NULL`: 通常のON/OFF操作を許可する。
- provider suppression reasonあり: toggleを無効化し、「配信エラーのためメール通知を停止しています。安全確認が必要なため、この画面から再開できません。」と表示する。
- browser queryは`is_enabled`、`disabled_reason`、timestampの参照だけとし、更新payloadは`is_enabled`だけにする。
- Resend Suppression Listを自動解除せず、利用者へ`disabled_reason`更新権限を追加しない。

## 6. ローカル検証

production操作前に、repository rootで次を成功させる。

```powershell
& .\.venv\Scripts\python.exe -m pytest
supabase test db
deno test supabase/functions/dispatch-email-notifications/helpers_test.ts
deno test supabase/functions/resend-email-webhook/helpers_test.ts
deno test supabase/functions/unsubscribe-email-notifications/helpers_test.ts
deno check supabase/functions/dispatch-email-notifications/index.ts
deno check supabase/functions/resend-email-webhook/index.ts
deno check supabase/functions/unsubscribe-email-notifications/index.ts
deno fmt --check supabase/functions/dispatch-email-notifications supabase/functions/resend-email-webhook supabase/functions/unsubscribe-email-notifications
git status --short
git diff --stat
```

pgTAPはbackfill、自動作成、unique、direct access拒否、RPC privilege、valid/repeated/unknown/manual OFF、provider reason保持、processing/retry_wait中の再有効化拒否と不変性、in-flightなしとpendingのみのrotation、suppression時の拒否を確認する。Deno testはGET no-side-effect、人間POST、RFC 8058 POST、generic response、method、bounded streaming body、invalid UTF-8、非PIIログ、DB 5xxを確認する。sender testはfooter、exact headers、exact JSON fingerprint、retry stabilityを確認する。

## 7. Production rollout前の必須guard

footerとheadersの追加はexact payload fingerprintを変える。sender切替前後に新しいclaimが入るTOCTOUを避けるため、次のmaintenance boundary内で必ず確認する。

```sql
select status, count(*)
from public.notification_messages
where status in ('processing','retry_wait')
group by status;
```

結果は0件、すなわち0行でなければならない。0行でなければsenderをdeployしない。message削除、status上書き、fingerprint消去でguardを通過させない。rollbackで旧senderへ戻す場合もpayload変更なので、同じfeature flagと再確認を含むmaintenance boundaryを必須とする。

## 8. 人間が行うProduction rollout案

1. 対象環境とmigration履歴を確認し、Phase 3.5b forward migrationを適用する。
2. token backfill件数がpreference件数と一致し、token tableのdirect privilegeが4 roleすべてで無いこと、3 RPCがservice-role専用であることを確認する。
3. `unsubscribe-email-notifications`をdeployする。新しいsecretは追加しない。既存platform-managed `SUPABASE_URL`と`SUPABASE_SERVICE_ROLE_KEY`だけを使用する。
4. 架空tokenのGET、人間POST、RFC 8058 POSTがgeneric responseになり、ログへquery/token/PIIが出ないことを確認する。
5. guardで`retry_wait`が0件であることを事前確認する。0件でなければ通常workerでdrainしてからやり直す。
6. `ENABLE_USER_EMAIL_NOTIFICATIONS=false`にして新規claimを停止する。
7. 同じguardを再実行し、`processing`と`retry_wait`がともに0行であることを確認する。
8. 0行でなければdeployせず中止する。`retry_wait`が発生した場合はflagを`true`へ戻し、通常workerでdrainしてからmaintenance boundaryを最初からやり直す。
9. footer/header追加済み`dispatch-email-notifications`をdeployする。scheduler watchdog、GitHub workflow、Repository Variables、Secretsは変更しない。
10. `ENABLE_USER_EMAIL_NOTIFICATIONS=true`へ戻す。
11. Account UIを含むPages Artifactを通常の既存手順で反映する。
12. 内部test user 1名へ1通だけcanary送信し、日本語footer、2 headers、両headerをcoverする有効なDKIM署名、Resend accepted、webhook sent/deliveredを確認する。
13. canaryの確認GETで通知が変化しないこと、人間POSTでOFFになること、再有効化でtokenが変わり旧linkがno-opになることを確認する。
14. 別canaryでRFC 8058形式POSTが空`200`でOFFになり、replayが同じgeneric successになることを確認する。
15. 24〜48時間、Function 5xx、sender retry、unsubscribe件数、bounce/complaint/suppression、provider payload変更エラーをaggregateで監視する。

異常時はsenderを即時rollbackせず、まず`ENABLE_USER_EMAIL_NOTIFICATIONS=false`で新規claimを停止する。送信済みメールのlinkを壊さないため、migration、token table、RPC、公開Functionはdropしない。sender rollbackが必要なら、事前の`retry_wait=0`確認、flag停止、`processing`/`retry_wait`の0行再確認、旧sender deploy、flag復帰、canaryという同じmaintenance boundaryで行う。再確認が0行でなければrollback deployを中止し、`retry_wait`発生時はflagを戻して通常workerでdrainしてからやり直す。

## 9. 明示的な対象外と残るリスク

対象外はResend Suppression Listの自動解除、bounce/complaint後の自動再有効化、retention cleanup、Phase 4 LINE、production deploy、secret変更、commit、pushである。

公開capability URLはメールを閲覧できる者が利用できるため、転送されたメールからも停止できる。これはunsubscribeの性質であり、tokenを高entropy・unique・非ログとし、再有効化時rotationで旧linkを無効化する。メールsecurity scannerによるGETでは停止せず、RFC 8058の明示的POSTだけにside effectを限定する。provider suppression解除には別途、宛先確認とResend側の運用手順が必要であり、本実装は自動化しない。RFC 8058は両unsubscribe headerが有効なDKIM署名でcoverされることも要求するため、これはproduction canaryのraw headerで確認するまで残る運用上の確認事項である。

参考仕様:

- <https://www.rfc-editor.org/rfc/rfc8058.html>
- <https://resend.com/docs/api-reference/emails/send-email>
- <https://resend.com/docs/dashboard/emails/add-unsubscribe-to-transactional-emails>
