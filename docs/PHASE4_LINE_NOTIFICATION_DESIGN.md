# Phase 4 LINE通知設計

## 1. 状態

本書はPhase 4の採用方式と実装順序を定める。
実装開始条件は[Launch Readiness Review](./LAUNCH_READINESS_REVIEW.md)を参照する。
Launch Readiness Gateは完了しており、本書の順序で実装を開始する。
本番LINE配信はfeature flag、shadow enqueue、単一会員、限定βの順に有効化する。

2026-08-21に最初の前方migrationとして、`notification_channel`の`line`追加、
`line_account_links`、`line_link_sessions`、本人向け安全な連携状態RPCを実装した。
ブラウザへLINE user IDや連携sessionのSELECT権限は付与しない。
本人かつactive会員にはRLS下で安全な状態列だけをcolumn-level Grantし、
`SECURITY INVOKER` RPCも同じRLSへ従わせる。

## 2. 採用方式

次の構成を採用する。

- 既存のSupabase Authメールマジックリンクを会員認証の正として維持する。
- LINE Login v2.1を、ログイン済み会員とLINEアカウントの連携にだけ使用する。
- LINE Messaging APIを、友だち追加、block/unfollow反映、利用者別通知配信に使用する。
- LINE Login channelとMessaging API channelは、必ず同じLINE providerへ作成する。
- LINE Login channelへLINE公式アカウントを関連付け、add friend optionを使用する。
- LINEを唯一のログイン手段にせず、メール認証を省略しない。

同一providerではLINE LoginとMessaging APIで同じLINE user IDが発行される。
この性質を利用し、ブラウザで認証されたLINE accountと通知先を一致させる。

公式資料:

- [LINE user IDの発行単位](https://developers.line.biz/en/docs/messaging-api/getting-user-ids/)
- [LINE Login Web integration](https://developers.line.biz/en/docs/line-login/integrate-line-login/)
- [Add friend option](https://developers.line.biz/en/docs/line-login/link-a-bot/)
- [Webhook署名検証](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/)

## 3. 連携フロー

1. active会員がマイページで「LINEを連携」を選ぶ。
2. server-side Edge FunctionがSupabase JWTを検証し、会員本人を確定する。
3. 256bit以上のrandom `state` と `nonce` を発行し、hash、会員ID、失効時刻を保存する。
4. LINE Login authorization endpointへ `scope=openid`、`bot_prompt=aggressive` で遷移する。
5. callbackでauthorization error、`state`、失効、未使用を検証する。
6. server-sideでauthorization codeをtokenへ交換する。
7. ID tokenの署名、issuer、audience、expiry、nonceを検証し、`sub`をLINE user IDとして得る。
8. 1会員1LINE、1LINE1会員のunique制約下で原子的に連携する。
9. access tokenとID tokenは検証後に破棄し、DBへ保存しない。
10. 連携結果と、友だち追加・通知受取状態を会員へ表示する。

callback URLへSupabase access tokenをquery stringで渡さない。
連携状態は一回限り、10分以内、再利用不可とする。

## 4. データモデル方針

### 4.1 `line_account_links`

| 列 | 用途 |
| --- | --- |
| `user_id` | Auth userへのFK、1会員1件 |
| `line_user_id` | LINE provider内のuser ID、unique、暗号化または同等の保護対象 |
| `status` | `active`、`blocked`、`unlinked`、`delivery_failed` |
| `linked_at` | 連携完了日時 |
| `unlinked_at` | 解除日時 |
| `last_webhook_at` | 最終LINE event日時 |
| `created_at` / `updated_at` | 監査用時刻 |

LINE user IDは個人情報に準じ、GitHub、Actions log、Artifact、公開Pagesへ出さない。
一般会員向けData APIから行を直接更新させず、本人JWTを検証するEdge Functionへ操作を集約する。

### 4.2 `line_link_sessions`

一回限りの`state`と`nonce`のhash、会員ID、失効・消費日時を保持する。
平文token、authorization code、LINE access tokenは保存しない。
短期retention cleanupの対象とする。

### 4.3 通知queue

- `notification_channel` enumへ前方migrationで`line`を追加する。
- 既存の`unique (user_id, channel, slot_id)`をLINEにも適用する。
- email専用payload validatorとenqueue RPCを汎用化またはLINE専用に分離する。
- メールとLINEはchannelごとに別delivery itemとし、同じchannel内の重複だけを防ぐ。
- LINE送信workerはmessage ID、attempt、provider status、error codeを正規化する。
- channel access tokenやchannel secretはEdge Function secretとして管理し、DBやGitHubへ保存しない。

### 4.4 無料枠と運用監視

- LINE公式アカウントはコミュニケーションプランの月200通を維持し、
  自動で有料プランへ変更しない。
- 利用者別LINE Pushは1会員・1回のworker実行につき1通へ集約する。
- 運用上限は月180通とし、LINE送信workerは送信直前に月間使用量を確認する。
- 180通到達後は新しいLINE Pushを送らず、既存のemail channelへ
  フォールバックする。メール停止中の会員へ同意なく再送しない。
- `GET /v2/bot/message/quota` と
  `GET /v2/bot/message/quota/consumption` の集計値だけを使用し、
  LINE user IDやmessage本文を監視処理へ渡さない。
- 月間使用量にはMessaging APIだけでなくLINE Official Account Managerからの
  配信も含まれるため、既存用途との並行運用分も同じ予算で管理する。
- GitHub Actionsは毎日12:07 JSTに使用量を確認する。毎週土曜は必ず週次報告を
  メール送信し、180通到達時は土曜を待たず当月1回だけ警告する。
- 報告先、LINE channel access token、報告専用Resend API keyは
  GitHub Secretsに保存する。宛先メールアドレスをrepository、Actions log、
  Artifactへ出さない。
- 当月の警告送信済み状態は宛先やtokenを含まない月単位のActions cacheで保持する。
  cache消失時にもResendのidempotency keyで同一日の重複送信を抑止する。

公式資料:

- [LINE月間上限・使用量API](https://developers.line.biz/en/reference/messaging-api/#get-quota)
- [Messaging API料金と通数](https://developers.line.biz/en/docs/messaging-api/pricing/)

## 5. 認可とRLS

- `public` schemaへ追加する全tableでRLSを有効にする。
- Data APIへの公開とGrantを明示し、自動公開を前提にしない。
- `TO authenticated`だけで許可せず、必ず本人所有条件を加える。
- LINE user IDを返す一般会員向けviewやRPCを作らない。
- service-role専用RPCは`PUBLIC`、`anon`、`authenticated`からexecuteを剥奪する。
- `SECURITY DEFINER`を権限エラー回避のために使用しない。
- migration適用後にSupabase security/performance advisorを実行する。

## 6. Webhook

- raw request bodyと`x-line-signature`をHMAC-SHA256で検証してからJSON parseする。
- LINE Platformの送信元IP allowlistへ依存しない。
- webhook event ID等でat-least-once deliveryを冪等化する。
- follow/unfollow/block関連eventは連携状態へ反映する。
- unknown user、未連携user、失効eventは情報を返さず安全に無視する。
- raw payload、message本文、LINE user IDをapplication logへ保存しない。

## 7. UX

マイページでは次だけを表示する。

- 未連携: 「LINEで空き通知を受け取る」
- 連携途中: 処理中と再試行案内
- 連携済み: 「LINE通知は連携済み」
- block/unfollow: 友だち追加またはblock解除案内
- 解除: 二段階確認後に解除

LINE display name、profile image、status message、email addressは取得しない。
メール通知とLINE通知は別々に有効・停止できる設計とする。

## 8. 実装順序

1. LINE provider、公式アカウント、Messaging API channel、LINE Login channelを同一providerに準備する。
2. schema、RLS、Grant、link session、account linkのmigrationを作る。— 完了
3. LINE Login開始・callback・解除Edge Functionを実装する。
4. My Pageへ連携状態と操作UIを追加する。
5. webhook署名検証、冪等化、block/unfollow反映を実装する。
6. 月間使用量の週次報告と180通警告を有効化する。
7. `line` channelのqueue、worker、retry、重複防止、180通送信guardを実装する。
8. dry-runと架空利用者によるcross-user isolationを検証する。
9. 管理者を含むβ会員を同じ基盤で連携する。
10. feature flagでshadow enqueue、単一会員、限定βの順に有効化する。
11. delivery、block、解除、退会、上限到達、rollbackをproduction acceptanceする。

## 9. 完了条件

- 別会員または別LINE accountへ誤連携できない。
- 連携state、nonce、authorization codeを再利用できない。
- 連携解除、block、通知停止、退会後は配信されない。
- 同じLINE通知を同じ利用者へ重複送信しない。
- LINE user ID、token、secretが公開データまたはlogへ出ない。
- メール通知はPhase 4導入中も継続し、LINE障害の影響を受けない。
- LINE月間使用量が180通へ到達した後はLINE Pushを増やさず、週次報告と
  当月1回の到達警告が運用宛先へ届く。
