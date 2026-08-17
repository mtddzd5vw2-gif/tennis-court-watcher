# Phase 3.5b Email Unsubscribe / Re-enable Runbook

## 1. 目的、実測結果、現在地

利用者別空き通知メールから、本人が安全にメール通知を停止できるようにする。通常の確認画面に加えてRFC 8058 one-click unsubscribeを提供し、本人opt-outとResend由来のbounce・complaint・suppressionを分離する。

production canary前の実測で、Supabase Edge Functionをcapabilityの公開endpointにする設計を棄却した。

- Supabase hosted GET returned `text/plain`。Functionが`text/html; charset=utf-8`を返してもhosted Edge Function側で`text/plain`へ変更され、人間向け確認画面として使用できなかった。
- 架空tokenをqueryへ付けたGETで、Supabase Invocation Logsの`request.url` / `request.search` / `event_message`へtokenを含む値が保存された。
- したがって、`https://<project>.supabase.co/functions/v1/unsubscribe-email-notifications?token=<token>`という直接公開capability URLはsender rollout前に棄却した。実tokenを含むメールは送信していない。

Cloudflare Workerからbody-only POSTする版のproduction fake token log boundaryでは、token境界は改善したが、service-to-service認証headerに追加blockerが見つかった。

- Supabase Invocation Rawの`request.url`は固定Function URL、`request.search`はなしであり、架空unsubscribe tokenはURL、`event_message`、header metadataに出なかった。このtoken URL leakage境界はPASSした。
- 一方、`Authorization: Bearer <UNSUBSCRIBE_WORKER_SECRET>`を送ると、Supabase Gatewayが`request.sb.apikey.authorization.prefix`へsecret先頭10文字を保存し、`request.sb.apikey.authorization.error = "invalid"`も保存した。
- credential fragmentがplatform logに残るためBearer契約を棄却した。修正版は`X-Unsubscribe-Worker-Secret`だけを使い、Workerから`Authorization` headerを送らず、Functionも認証に使用しない。

2026-08-17時点のproduction差分は次のとおりである。

| 対象 | production状態 |
| --- | --- |
| Phase 3.5b DB migration | 適用済み |
| Supabase `unsubscribe-email-notifications` | custom header修正版をdeploy済み。version 5 ACTIVE |
| Cloudflare unsubscribe Worker / custom domain | custom header修正版をdeploy済み。`unsubscribe.tenniscourtwatcher.com`だけを公開入口として使用 |
| `UNSUBSCRIBE_WORKER_SECRET` | Bearer版でprefixが保存された旧値を廃止し、新規header-safe値へrotation済み |
| fake token log boundary | 架空UUIDでGET、human POST、RFC 8058 POSTを確認済み。Workers Logsには保存されず、Supabase Invocation URL/search/event messageにもtokenはなく、`request.sb.apikey.authorization.prefix`も存在しない。Cloudflare Security Analyticsでは公開URIの`/u/<opaque-uuid>`がprovider-edge telemetryとして保存されることを実測した |
| footer / headers / public Worker URL版sender | 未deploy |
| Account UI / Pages | この実測ではproduction反映を変更していない |
| `ENABLE_USER_EMAIL_NOTIFICATIONS` | 変更していない |

実tokenはまだpublic Workerへ通していない。2026-08-17のfake boundary実測を受け、Cloudflare provider-edgeで公開URIを処理する際のopaque token pathと、アプリケーションログ・credential漏えいを同一視しないtrust boundaryへ本runbookを更新する。DB schemaとruntime codeはこのrunbook修正では変更しない。Phase 3.5aの24〜48時間aggregate observationは別に継続する。

## 2. architectureとcredential境界

```text
email footer / List-Unsubscribe
  -> https://unsubscribe.tenniscourtwatcher.com/u/<opaque-uuid>
  -> Cloudflare Worker
       GET: generic confirmation HTML、DB問い合わせなし、side effectなし
       POST: tokenをpathから検証し、専用custom header付きで固定URLへform body転送
  -> https://<project>.supabase.co/functions/v1/unsubscribe-email-notifications
       X-Unsubscribe-Worker-Secret: <UNSUBSCRIBE_WORKER_SECRET>
       body: interaction=human|one_click&token=<uuid>
  -> service-role-only unsubscribe_email_notifications_by_token(uuid)
  -> notification_email_preferences
```

tokenはunsubscribe権限のcapabilityであり、`UNSUBSCRIBE_WORKER_SECRET`はCloudflareを迂回した大量RPC実行を拒否するservice-to-service認証である。同じ32 random bytes以上のheader-safe値をCloudflare secretとSupabase Function secretの両方へ設定し、`X-Unsubscribe-Worker-Secret`だけで渡す。`Authorization`、URL、bodyには入れない。WorkerへSupabase service-role key、anon key、利用者credentialは置かない。Supabase Function自身だけがplatform-managed `SUPABASE_URL`と`SUPABASE_SERVICE_ROLE_KEY`を使ってservice-role RPCを呼ぶ。

## 3. DBとcapability契約

`notification_email_unsubscribe_tokens`は`user_id`を主キーとし、uniqueなUUID token、`created_at`、`rotated_at`だけを保持する。メールアドレスは保持しない。既存の`notification_email_preferences`は適用済みmigrationでbackfillされ、今後のpreference作成時はtriggerでtoken rowを作る。

token tableはRLSを有効にし、`PUBLIC`、`anon`、`authenticated`、`service_role`を含む直接table privilegeを付与しない。操作は空の`search_path`と完全修飾名を使うsecurity-definer RPCに限定する。

| RPC | 呼出しrole | 契約 |
| --- | --- | --- |
| `get_email_unsubscribe_token_for_message(uuid)` | service_roleのみ | 存在するemail messageの所有者tokenだけをsenderへ返す |
| `unsubscribe_email_notifications_by_token(uuid)` | service_roleのみ | valid、既にOFF、unknownを同じ`processed` outcomeにする |
| `email_unsubscribe_token_is_valid(uuid)` | service_roleのみ | 適用済みmigrationに残るがhotfix後のruntimeからは呼ばない |

本人opt-outでは`disabled_reason IS NULL`かつ現在ONの場合だけ`is_enabled=false`、`disabled_at=now()`へ変更する。既にOFFならtimestampを動かさない。`resend_bounced`、`resend_complained`、`resend_suppressed`を含む既存reasonとtimestampは上書きしない。

activeなauthenticated本人は従来どおり自分のpreferenceをSELECTし、`is_enabled`列だけUPDATEできる。通常の`false -> true`で、その利用者のemail messageに`processing`または`retry_wait`が1件でもあれば再有効化UPDATE全体を例外でrollbackする。この場合はpreferenceをOFFのまま、tokenとmessageのstatus、lease、fingerprintを変更しない。該当messageが0件の場合だけtokenをrotationする。`pending`は再有効化を妨げず、cancelもしない。provider suppression reasonがある行は既存CHECK constraintがONを拒否するためrotationしない。

## 4. Cloudflare Worker公開HTTP契約

公開URLは次の形式だけをsenderが生成する。

```text
https://unsubscribe.tenniscourtwatcher.com/u/<opaque-uuid>
```

### GET

- pathからUUID tokenを読むが、DBやSupabaseへ問い合わせない。
- preferenceを変更しない。
- valid、unknown、形式不正で同じ日本語確認HTMLを返す。
- confirmation formはtokenをhidden fieldやqueryへ複製せず、同じWorker URIへ`POST`する。
- `Content-Type: text/html; charset=utf-8`、`Cache-Control: no-store`、`Referrer-Policy: no-referrer`、CSP、`X-Content-Type-Options: nosniff`を返す。
- script、beacon、外部asset、browser analyticsを含めない。

### POST

- `application/x-www-form-urlencoded`だけを受け付ける。
- `Content-Length`が2048 bytesを超える場合は読込前に拒否し、長さ未指定のstreamも実読込が2048 bytesを超えた時点でreaderをcancelする。invalid UTF-8も拒否する。
- bodyが厳密に`List-Unsubscribe=One-Click`ならRFC 8058 one-click、厳密に`interaction=human`なら人間操作として扱う。
- `UNSUBSCRIBE_WORKER_SECRET`がUTF-8で32 bytes未満または未設定ならupstreamを呼ばず`503`にする。
- tokenはpathから検証し、Supabaseの固定Function URLにはquery、path segment、headerとして追加しない。
- Supabaseへは`interaction=one_click&token=<uuid>`または`interaction=human&token=<uuid>`というform bodyだけを送る。
- Supabaseへ`X-Unsubscribe-Worker-Secret: <UNSUBSCRIBE_WORKER_SECRET>`だけを認証headerとして送る。`Authorization` headerは送らず、secretをURL、body、response、logへ含めない。
- one-click成功はredirectせず、空bodyの`200`を返す。human成功は日本語success HTMLを返す。
- malformed、unknown、既にOFF、validで利用者存在を判別できる表示を返さない。
- Supabase 5xxは成功に見せず5xxで返す。network failureと予期しないupstream statusは`502`とする。upstream redirectは追跡しない。

### Cloudflare logs

`cloudflare/unsubscribe-worker/wrangler.jsonc`で次を明示する。

```json
{
  "workers_dev": false,
  "preview_urls": false,
  "vars": {
    "SUPABASE_UNSUBSCRIBE_URL": "https://oocqyeariwuppkeaeioh.supabase.co/functions/v1/unsubscribe-email-notifications"
  },
  "observability": {
    "logs": {
      "invocation_logs": false
    }
  }
}
```

これによりrequest method/URLを自動保存するWorkers invocation logsを止め、custom domain以外の`workers.dev`とPreview URL入口を無効にする。Worker sourceは`console.log`、`console.error`、`console.warn`を呼ばず、token、secret、`X-Unsubscribe-Worker-Secret`の名前・値、Authorization、request URL、path、query、user ID、emailをcustom logsへ出さない。

productionのlog boundaryは次の3層で定義する。

- **credential/PII boundary**: `UNSUBSCRIBE_WORKER_SECRET`、Authorization credential、Supabase service-role credential、email、`user_id`はCloudflare provider-edge telemetryを含む全log sinkへ保存してはならない。
- **application log boundary**: unsubscribe tokenとunsubscribe URL/path/queryはWorkers invocation/custom logs、Workerが明示的に出力するTail/Logpush対象ログ、Supabase Invocation URL/search/event message、Supabase custom logs、sender通常ログへ保存してはならない。WorkerからSupabaseへtokenを渡す箇所は固定Function URLへのrequest bodyだけとする。
- **provider-edge URI boundary**: RFC 8058で外部へ提示する公開HTTPS URIは`https://unsubscribe.tenniscourtwatcher.com/u/<opaque-uuid>`であり、CloudflareがTLS/HTTP edgeとしてこのURIを受信するため、Cloudflare Security Analytics等のzone security/HTTP telemetryに`/u/<opaque-uuid>`が保存されることは許容する。これはapplication log leakageとは扱わない。ただしpathはrandom UUID capabilityだけとし、email、`user_id`、credential、意味のある識別情報を符号化しない。

productionではdeploy後にDashboard/API上の実効設定が`invocation_logs=false`、custom domain only、workers.dev disabled、Preview URLs disabledであることを確認する。架空tokenで有効なlog sinkを確認し、application log boundaryとcredential/PII boundaryを満たすことを必須とする。Cloudflare zone security/HTTP telemetryでopaque UUID pathが観測されること自体はprovider-edge trust boundary内の期待動作であり、rollout blockerにしない。

`SUPABASE_UNSUBSCRIBE_URL`は手作業で設定せず、`wrangler.jsonc`の`vars`に次のproduction URLを固定し、repository configをsource of truthにする。runtimeもこのhost/pathだけを許可し、別project、credentials、port、query、hashを拒否する。localhostと`127.0.0.1`だけはlocal test用に許可する。

```text
https://oocqyeariwuppkeaeioh.supabase.co/functions/v1/unsubscribe-email-notifications
```

## 5. Supabase Function内部POST契約

`unsubscribe-email-notifications`は`verify_jwt=false`の公開Functionだが、ブラウザ向けendpointとして掲載・使用しない。Workerからの固定URL POSTだけをruntime契約とする。

- GETは`405 Allow: POST`で、存在確認、HTML表示、unsubscribeを一切行わない。
- `UNSUBSCRIBE_WORKER_SECRET`がUTF-8で32 bytes未満または未設定なら`503`にする。
- `request.headers.get("x-unsubscribe-worker-secret")`だけを読み、SHA-256 digestを使ったconstant-time比較で同一secretを検証する。missing/invalid custom headerは`401`とし、bodyを読まずRPCも呼ばない。`Authorization` parserやfallbackは持たず、Originには依存しない。
- query token、`List-Unsubscribe=One-Click`直接body、公開confirmation画面の旧契約を削除する。
- `application/x-www-form-urlencoded`のbodyに、重複や追加fieldなしで`interaction=human|one_click`と`token=<uuid>`を要求する。
- UUIDなら既存`unsubscribe_email_notifications_by_token()`を呼び、valid、unknown、replay、既にOFFを同じ空`200`にする。形式不正tokenも同じ空`200`にする。
- RPC/DB障害は空`502`とし、Workerが5xxとして外部へ返す。
- custom logはaggregateの`outcome`と`interaction`だけで、request URL、query、token、secret、custom auth headerの名前・値、Authorization、user ID、emailを含めない。

Supabase Invocation Logsには固定Function URLしか渡らないため、`request.url`、`request.search`、`event_message`へcapabilityは載らない。tokenはrequest body内だけに存在する。

## 6. Sender契約

senderはclaimしたmessageごとにtoken RPCを呼び、`EMAIL_UNSUBSCRIBE_PUBLIC_BASE_URL`から次のURLを一度だけ構築する。

```text
EMAIL_UNSUBSCRIBE_PUBLIC_BASE_URL=https://unsubscribe.tenniscourtwatcher.com
https://unsubscribe.tenniscourtwatcher.com/u/<token>
```

本文末尾のtext/html双方とResend headersは同じURLを使用する。

```json
{
  "headers": {
    "List-Unsubscribe": "<https://unsubscribe.tenniscourtwatcher.com/u/<token>>",
    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"
  }
}
```

footer、headers、token、correlation tagsを含むexact provider JSONを一度だけserializeし、その同じ文字列をHMAC payload fingerprintとResend POST bodyの双方に使う。通常retryでは利用者tokenとpublic base URLが変わらないためJSONも変わらない。`processing`または`retry_wait`がある間は再有効化とtoken rotationを拒否するため、fingerprint済みmessageのpayloadは変わらない。`pending`はまだfingerprintが確定していないため、rotation後のtokenで初回payloadを構築する。

token、unsubscribe URL、path、query string、`user_id`、メールアドレス、provider request bodyは通常ログへ出さない。

## 7. Account UI

通知条件ページで本人の`notification_email_preferences`を表示する。

- `disabled_reason IS NULL`: 通常のON/OFF操作を許可する。
- provider suppression reasonあり: toggleを無効化し、「配信エラーのためメール通知を停止しています。安全確認が必要なため、この画面から再開できません。」と表示する。
- browser queryは`is_enabled`、`disabled_reason`、timestampの参照だけとし、更新payloadは`is_enabled`だけにする。
- Resend Suppression Listを自動解除せず、利用者へ`disabled_reason`更新権限を追加しない。

## 8. ローカル検証

production操作前にrepository rootで次を成功させる。

```powershell
& .\.venv\Scripts\python.exe -m pytest
supabase test db
deno test cloudflare/unsubscribe-worker/src/index_test.ts
deno test supabase/functions/dispatch-email-notifications/helpers_test.ts
deno test supabase/functions/resend-email-webhook/helpers_test.ts
deno test supabase/functions/unsubscribe-email-notifications/helpers_test.ts
deno test supabase/functions/update-availability-watchdog
deno check cloudflare/unsubscribe-worker/src/index.ts
deno check supabase/functions/dispatch-email-notifications/index.ts
deno check supabase/functions/resend-email-webhook/index.ts
deno check supabase/functions/unsubscribe-email-notifications/index.ts
deno check supabase/functions/update-availability-watchdog/index.ts
deno fmt --check cloudflare/unsubscribe-worker/src supabase/functions
git status --short
git diff --stat
```

pgTAPはbackfill、自動作成、unique、direct access拒否、RPC privilege、valid/repeated/unknown/manual OFF、provider reason保持、processing/retry_wait中の再有効化拒否と不変性、in-flightなしとpendingのみのrotation、suppression時の拒否を確認する。

Worker testはGET HTML/no-side-effect、human POST、RFC 8058 blank POST、invalid token generic、bounded body、upstream 5xx/no redirect、custom secret headerのみ送信、Authorization非送信、secret fail-closed、production upstream pin、custom log不使用、upstream URL/bodyへのsecret非混入、body-only tokenを確認する。Supabase Function testはsecret設定、missing/invalid custom header拒否、Authorization非使用、body tokenのhuman/one_click、query契約削除、GET no-side-effect、generic/idempotent behavior、bounded body、非PII/secret aggregate log、DB 5xxを確認する。sender testはproduction public origin pin、public Worker URL、footer、exact headers、exact serialized JSON fingerprint、retry stabilityを確認する。provider suppression preservationはpgTAPで維持確認する。

## 9. Production rollout前の必須guard

footer、headers、public URLの変更はexact payload fingerprintを変える。sender切替前後に新しいclaimが入るTOCTOUを避けるため、次のmaintenance boundary内で必ず確認する。

```sql
select status, count(*)
from public.notification_messages
where status in ('processing','retry_wait')
group by status;
```

結果は0件、すなわち0行でなければならない。0行でなければsenderをdeployしない。message削除、status上書き、fingerprint消去でguardを通過させない。rollbackで旧senderへ戻す場合もpayload変更なので、同じfeature flagと再確認を含むmaintenance boundaryを必須とする。

## 10. 人間が行うProduction rollout順

production migrationは適用済みであり、新しいDB migrationはない。Bearer版Function/Workerはfake boundaryまでdeploy済みだがsenderは未使用である。まずcredentialをrotationし、custom header修正版Function/Workerを反映してから、次の順序を崩さない。

**新secret rotation + Supabase Function deploy + Cloudflare Worker deploy + custom domain + invocation_logs=false確認 → 新しいfake token log boundary → maintenance boundary → sender deploy → canary**

1. 対象Supabase/Cloudflare環境と適用済みmigrationを再確認する。現行`UNSUBSCRIBE_WORKER_SECRET`はprefixがplatform logへ出たcredentialとして廃止し、旧secretを再利用しない。承認済みsecret managerでUTF-8 32 random bytes以上の新しいheader-safe値を生成し、同じ値をSupabase Function secretとCloudflare Worker secretへ協調設定する。secret値をterminal output、docs、chatへ出さない。Cloudflare plaintext `vars`やrepository fileへ入れない。
2. repository hotfix版`unsubscribe-email-notifications`をdeployする。架空UUIDのbody-only human/one_click POSTは正しい`X-Unsubscribe-Worker-Secret`だけが空`200`、missing/invalid custom headerおよびAuthorization-onlyは`401`、GETと旧query contractはside effectなしであることを確認する。
3. repositoryの`wrangler.jsonc`をsource of truthとしてWorkerをdeployし、`unsubscribe.tenniscourtwatcher.com` custom domainを関連付ける。service-role credentialは設定しない。
4. Cloudflare Dashboard/APIで実効設定が`observability.logs.invocation_logs=false`、custom domain only、workers.dev disabled、Preview URLs disabledであることを確認する。source/configの静的値だけで完了扱いにしない。
5. 架空UUIDだけでGET、human POST、RFC 8058 POSTを実行する。Workers invocation/custom logs、利用中のTail/Logpush、Supabase Invocation Logs/custom logsを確認し、tokenまたはunsubscribe URL/path/queryがapplication log boundaryへ残らないこと、custom header値やcredential/PIIが保存されないことを確認する。Supabaseでは`request.url`が固定Function URLで、`request.search`とtoken入り`event_message`がなく、Invocation Rawに`request.sb.apikey.authorization.prefix` field自体が出ないことを必須とする。Cloudflare zone Security Analytics/HTTP telemetryは別途確認し、公開URIの`/u/<opaque-uuid>`がprovider-edge telemetryとして保存されることは許容する。ただしそこへemail、`user_id`、`UNSUBSCRIBE_WORKER_SECRET`その他credentialが出てはならない。application log boundaryまたはcredential/PII boundaryに違反した場合だけ実tokenを通さず中止する。これを新しいfake token log boundaryとする。
6. maintenance事前guardで`retry_wait`が0件であることを確認する。0件でなければ通常workerでdrainしてからやり直す。
7. `ENABLE_USER_EMAIL_NOTIFICATIONS=false`にして新規claimを停止する。
8. 同じguardを再実行し、`processing`と`retry_wait`がともに0行であることを確認する。
9. 0行でなければdeployせず中止する。`retry_wait`が発生した場合はflagを元の値へ戻し、通常workerでdrainしてからmaintenance boundaryを最初からやり直す。
10. `EMAIL_UNSUBSCRIBE_PUBLIC_BASE_URL=https://unsubscribe.tenniscourtwatcher.com`を設定したfooter/header版`dispatch-email-notifications`をdeployする。scheduler watchdog、GitHub workflow、Repository Variables、他のSecretsは変更しない。
11. `ENABLE_USER_EMAIL_NOTIFICATIONS`をmaintenance前の値へ戻す。
12. 必要ならAccount UIを含むPages Artifactを通常の既存手順で反映する。
13. 内部test user 1名へ1通だけcanary送信し、日本語footer、exact 2 headers、同一public URL、両headerをcoverする有効なDKIM署名、Resend accepted、webhook sent/deliveredを確認する。
14. canaryのWorker GETで通知が変化しないこと、人間POSTでOFFになること、再有効化でtokenが変わり旧linkがno-opになることを確認する。
15. 別canaryでRFC 8058形式POSTがredirectなしの空`200`でOFFになり、replayが同じgeneric successになることを確認する。
16. canaryでも同じcredential/PII boundary、application log boundary、provider-edge URI boundaryを再確認し、その後24〜48時間、Function/Worker 5xx、sender retry、unsubscribe件数、bounce/complaint/suppression、provider payload変更エラーをaggregateで監視する。

異常時はsenderを即時rollbackせず、まず`ENABLE_USER_EMAIL_NOTIFICATIONS=false`で新規claimを停止する。送信済みメールのlinkを壊さないため、migration、token table、RPC、body-only Supabase Function、Worker custom domainはdropしない。sender rollbackが必要なら、事前の`retry_wait=0`確認、flag停止、`processing`/`retry_wait`の0行再確認、旧sender deploy、flag復帰、canaryという同じmaintenance boundaryで行う。再確認が0行でなければrollback deployを中止し、`retry_wait`発生時はflagを戻して通常workerでdrainしてからやり直す。

## 11. 明示的な対象外と残るリスク

対象外はResend Suppression Listの自動解除、bounce/complaint後の自動再有効化、Phase 3.5c retention cleanup、Phase 4 LINE、production deploy、secret変更、commit、pushである。

- 公開capability URLはメールを閲覧できる者が利用できるため、転送されたメールからも停止できる。tokenはrandom UUIDのopaque capabilityとし、emailや`user_id`を符号化せず、application logには保存せず、再有効化時rotationで旧linkを無効化する。
- メールsecurity scannerによるGETでは停止しない。RFC 8058の明示的POSTと人間のconfirmation POSTだけにside effectを限定する。
- `invocation_logs=false`はWorkers invocation logsだけを制御する。Cloudflare Security Analytics等のprovider-edge telemetryは外部から受けた公開URIのpathを保持し得る。2026-08-17のfake UUID実測でも`/u/<opaque-uuid>`がsampled logへ保存された。これはprovider-edge URI boundary内では許容するが、credential/PIIまたはapplication内部情報まで保存されることは許容しない。
- Cloudflare provider-edgeにもunsubscribe capability pathを一切見せないことを将来必須要件にする場合、現在のRFC 8058 public URI方式とは両立しないため、単なるlogging設定変更ではなくunsubscribe architecture自体を再設計する。
- `UNSUBSCRIBE_WORKER_SECRET`の設定漏れはfail-closedで`503`、不一致は`401`になる。Bearer版で使用した旧値はprefixがplatform logへ出たため修正版deploy時に必ずrotationし、再利用しない。同一新規値の安全な設定と将来rotationはSupabase/Cloudflareを協調して行う必要がある。
- 適用済みmigrationの`email_unsubscribe_token_is_valid()`はservice-role-onlyで残るがruntime未使用である。削除には別migrationが必要なため今回のhotfixでは変更しない。
- provider suppression解除には別途、宛先確認とResend側の運用手順が必要であり、本実装は自動化しない。
- RFC 8058は両unsubscribe headerが有効なDKIM署名でcoverされることも要求するため、production canaryのraw headerで確認するまで残る運用上の確認事項である。

参考仕様・設定:

- <https://www.rfc-editor.org/rfc/rfc8058.html>
- <https://developers.cloudflare.com/workers/observability/logs/workers-logs/>
- <https://resend.com/docs/api-reference/emails/send-email>
- <https://resend.com/docs/dashboard/emails/add-unsubscribe-to-transactional-emails>
