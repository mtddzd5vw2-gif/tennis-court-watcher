# Tennis Court Watcher サービス仕様書

## 1. 文書の目的

本書は、Tennis Court Watcherの**現在利用者へ提供しているサービス仕様の正**として、
利用者向け機能、主要データ、認証・認可、システム責務、拡張方針を定義する。

2026-08-20時点でPhase 0〜3は完了している。
Phase 1の退会もproduction acceptanceまで完了した。
Phase 4の利用者別LINE通知以降は未実装である。

長期的な目的は[Project Vision](./PROJECT_VISION.md)、
現在地・実装順序・完了条件は[Development Roadmap](./DEVELOPMENT_ROADMAP.md)を参照する。
Phase別設計書に残る導入当時の記述と本書が矛盾する場合は、
明示的な履歴記述を除き本書の現在仕様を優先する。

「案」と記載した項目は設計候補であり、未確定事項は**要決定**と明記する。

## 2. サービス概要

Tennis Court Watcherは、公共施設予約サイトの空き状況を定期的に確認し、見つかった空き候補をWebで表示し、利用者が希望する条件に応じて通知するサービスである。

利用者に提供する中心価値は、予約サイトを何度も確認せず「空きが出たときに通知を待つ」ことである。空き表示と通知は予約可能性を保証せず、実際の予約は各施設の公式予約サイトで利用者自身が行う。

### 2.1 初期提供範囲

- 地域: 鹿児島市
- 施設種別: テニスコート
- 対応施設:
  - 鴨池県営テニスコート
  - SuMIzeiテニスコート
  - 東開庭球場
- 現行監視条件: 今日を含む直近15日間の土日祝、8:00〜13:00、連続1時間以上
- 現行チャネル: GitHub Pages、利用者別メール通知、Phase 1認証メール用のResend Custom SMTP
- 認証メール送信ドメイン: `email.tenniscourtwatcher.com`

初期提供地域は鹿児島市に限定するが、GPSによる閲覧・登録・通知の地域制限は行わない。対象地域と施設は利用者が明示的に選択する。

### 2.2 サービス境界

- 本サービスは空き候補を取得・表示・通知する。
- 本サービスは施設の予約、予約代行、キャンセル、決済を行わない。
- 最新状態と予約可否は公式予約サイトを正とする。
- 取得元の規約、公開範囲、適切なアクセス頻度を継続して確認する。

## 3. 対象利用者

### 3.1 初期の主対象

- 鹿児島市周辺でテニスコートを定期的に利用する個人
- 土日祝の午前に1時間以上の空きを探す利用者
- 施設予約サイトを繰り返し確認する負担を減らしたい利用者
- メールまたはLINEで空き情報を受け取りたい利用者

### 3.2 将来の対象

- 鹿児島市以外で公共テニスコートを利用する個人・グループ
- 体育館、野球場、会議室など他施設種別の利用者
- 複数地域・複数施設の条件をまとめて管理したい利用者

法人・団体向け機能を提供するかは**要決定**。

## 4. 主要ユースケース

Phase 1のUC-04〜UC-07は、会員登録、認証メール、ログイン、セッション保持、マイページ、ローカル範囲ログアウト、退会まで本番確認済みである。Phase 2の通知条件管理、Phase 3の利用者別メール送信もproduction acceptanceまで完了している。

| ID | Phase | 主体 | ユースケース |
| --- | --- | --- | --- |
| UC-01 | 0 | 未ログイン利用者 | 3施設の最新の空き候補と取得状態をWebで確認する |
| UC-02 | 0 | 運用者 | 定期取得を実行し、施設ごとの成功・空き0件・失敗を把握する |
| UC-03 | 0 | legacy単一通知先 | Phase 0当時、新しく検出された対象施設の空きをLINEで受け取る。Phase 3.4.3で退役済み |
| UC-04 | 1 | 未登録利用者 | 利用規約を確認・同意し、メールアドレスで会員登録する |
| UC-05 | 1 | 登録中利用者 | 認証メールを受け取り、メールアドレスを認証する |
| UC-06 | 1 | 会員 | ログインし、自分のマイページを表示する |
| UC-07 | 1 | 会員 | ログアウトまたは退会する |
| UC-08 | 2 | 会員 | 通知したい施設、曜日、時間帯などを保存・変更・停止する |
| UC-09 | 3 | 会員 | 自分の条件に合う新規空きをメールで受け取る |
| UC-10 | 4 | 会員 | LINE公式アカウントを連携し、自分の条件に合う通知を受け取る |
| UC-11 | 5 | 会員 | 有料プランを開始・変更・解約する |
| UC-12 | 6以降 | 会員 | 鹿児島市以外またはテニス以外の施設を選択する |

## 5. 画面一覧

Phase 1の画面名とURLは、GitHub Pagesのリポジトリ配下で動く相対パスとして2026-08-04に決定した。Phase 2の通知条件は一覧と編集フォームを `account/notifications.html` に統合した。Phase 3以降のURLは案であり、実装時に**要決定**。

| 画面 | URL案 | 公開範囲 | Phase | 主な内容 |
| --- | --- | --- | --- | --- |
| 空き状況トップ | `/` | 公開 | 0 | 施設別の空き候補、取得状態、最終確認、公式予約リンク |
| 利用規約 | `legal/terms.html` | 公開 | 1 | 暫定規約本文。一般公開前に発効日とバージョンを確定 |
| プライバシーに関する表示 | `legal/privacy.html` | 公開 | 1 | 暫定案。取得情報、利用目的、保存、第三者提供、問い合わせ |
| 会員登録・ログイン | `auth/login.html` | 公開 | 1 | メールアドレス、規約同意、マジックリンク送信 |
| メール認証結果 | `auth/callback.html` | 公開 | 1 | 認証成功、期限切れ、無効リンク、再送導線 |
| マイページ | `account/index.html` | 会員限定 | 1 | 会員状態、メール認証状態、規約同意状態、ログアウト、退会導線 |
| 通知条件 | `account/notifications.html` | active会員限定 | 2 | 条件一覧、作成・編集・一時停止・有効化・削除、施設、曜日、日付範囲、時間帯、最低連続時間 |
| 通知履歴 | `/mypage/notifications` | 会員限定 | 3候補 | 送信履歴。MVPに含めるか**要決定** |
| LINE連携 | `/mypage/line` | 会員限定 | 4 | 連携状態、連携開始、解除 |
| プラン・支払い | `/mypage/billing` | 会員限定 | 5 | 現在のプラン、申込、解約、請求状態 |
| 退会確認 | `account/index.html`内 | 会員限定 | 1 | 影響説明、本人確認、退会確定 |

本格的な管理画面は初期MVPに含めない。運用者向け操作をどの仕組みで行うかは**要決定**。

## 6. 会員登録フロー

### 6.1 前提

- Phase 1では利用規約同意とメール認証を必須とする。
- 登録にGPS位置情報を要求しない。
- メールアドレス等の個人情報は認証・会員データベースに保存し、GitHubリポジトリへ保存しない。
- 認証基盤はSupabase Auth/PostgreSQLを正式採用し、メールのマジックリンクを使用する。

### 6.2 基本フロー

1. 利用者が会員登録画面を開く。
2. 利用規約とプライバシーに関する表示を確認する。
3. メールアドレスを入力し、現行利用規約への同意を明示する。
4. クライアントで入力と同意を検証し、同じブラウザのsessionStorageへ個人情報を含まない同意保留markerを保存する。
5. 認証基盤に未認証アカウントを作成し、マジックリンクを送信する。
6. Authユーザー作成と同じトランザクションのtriggerで `pending_terms` profileを作成する。
7. 認証メールを送信し、認証待ち画面を表示する。
8. 利用者が同じブラウザで有効な認証リンクを開く。
9. callbackがPKCE codeをセッションへ交換する。
10. 同意保留markerがある場合だけ、引数なしの `accept_current_terms()` RPCがDB現行規約版とDB時刻で同意履歴を追加し、profileを `active` へ更新する。
11. RPCが失敗してもセッションを破棄せず、マイページの再同意UIへ案内する。

同意履歴追加とprofile更新は同じRPCトランザクションで行う。同一規約版への再実行は冪等である。既存Authユーザーはprofileだけを `pending_terms` で補完し、過去の同意を推測しない。

### 6.3 入力と検証

- メールアドレス: 形式検証、正規化、認証基盤の一意制約を使用する。
- 利用規約同意: 初期値は未選択とし、必須とする。
- 表示名: 初期MVPで収集する必要性は**要決定**。不要なら収集しない。

登録済みメールアドレスの存在を第三者に推測されにくい応答とし、登録・再送にはレート制限を設ける。

## 7. 利用規約同意

### 7.1 必須要件

- 登録時点の現行規約を全文または到達しやすい画面で表示する。
- 同意チェックは初期状態でオフとし、利用者の能動的操作を必要とする。
- 同意記録には `user_id`、`document_type`、`version`、`accepted_at`、`source` を保持する。
- 同意日時はクライアント送信値を信用せず、サーバーまたはデータベース時刻を使用する。
- 規約本文はバージョンごとに変更不能な履歴として管理する。

Phase 1の同意証跡へIPアドレス、User-Agent、氏名、電話番号、住所は保存しない。

### 7.2 規約改定

- 新しい規約は新規バージョンとして追加し、過去の同意記録を上書きしない。
- 重要な改定時に再同意を必須にするかは**要決定**。
- 再同意が必要な場合、マイページ上で同意完了まで利用可能な機能を制限できる設計とする。
- 発効日、告知方法、旧規約の表示期間、法務確認の要否は**要決定**。

## 8. メール認証

### 8.1 要件

- 新規登録時は認証メールを送信し、認証完了まで会員限定機能を許可しない。
- 認証リンクは推測困難で、有効期限を持ち、一度の目的に限定する。
- コールバック先は許可済みURLに限定し、任意の外部URLへリダイレクトしない。
- 期限切れ、改変済み、使用済みのリンクは失敗として扱い、再送導線を表示する。
- 再送には待機時間と回数制限を設ける。
- 認証メール、画面、ログにaccess token、refresh tokenや永続セッションを含めない。

### 8.2 本番メール構成

- DNS管理・ドメイン登録はCloudflare Registrarを使用する。
- メール配信はTokyoリージョンのResend Custom SMTPを使用する。
- 送信用サブドメインは `email.tenniscourtwatcher.com` とする。
- 送信元は `Tennis Court Watcher <no-reply@email.tenniscourtwatcher.com>` とする。
- SPF、DKIM、DMARC（`p=none`）を認証し、ResendでVerifiedとDeliveredを確認する。
- 初回登録はConfirm sign up、登録済みユーザーのログインはMagic link or OTPの日本語テンプレートを使用する。
- 同一利用者への再送は60秒以上空ける。
- ResendのEnable Sendingは有効、不要なEnable Receivingは無効とする。
- Resend APIキーはSMTP passwordとして使用する。値は本仕様書を含む文書、GitHub、Pages、Artifact、ログ、スクリーンショットへ記載しない。

具体的なSMTP設定、メールテンプレート、DNS確認、配信障害、APIキー漏えい時の手順は [Auth Email Operations](./AUTH_EMAIL_OPERATIONS.md) を参照する。

### 8.3 引き続き決定が必要な事項

- 認証リンクの有効期限
- 未認証アカウントの保持期間と自動削除
- 同一メールアドレスの再登録時の挙動

## 9. ログイン・ログアウト

### 9.1 ログイン

- メールアドレスへ送るマジックリンクによるログインをPhase 1の方式とする。
- メール認証済みで、退会・停止されていない会員だけを有効な会員として扱う。
- 認証失敗時は、メールアドレスの存在や未認証状態を過度に区別しない。
- 連続失敗へのレート制限を適用する。
- ログイン後の遷移先はマイページを基本とする。

`persistSession: true` と `autoRefreshToken: true` を使用し、同じブラウザではログアウトしない限り通常セッションを保持する。ログイン画面は送信フォームを表示する前に既存セッションを確認し、ログイン済みならマイページへ遷移する。ブラウザデータの削除、セッション無効化、別端末・別ブラウザでは再認証が必要になる。

### 9.2 ログアウト

- `signOut({ scope: "local" })` により、操作中ブラウザのセッションだけを終了する。
- ローカルに保持した会員データを消去する。
- ログアウト後は会員限定画面を表示できず、公開画面へ戻す。
- Phase 1では全端末ログアウトを提供しない。

### 9.3 パスワード

Phase 1はマジックリンク認証を採用するため、パスワードの設定・保存・再設定機能を提供しない。

## 10. マイページ

### 10.1 Phase 1で表示する情報

- 会員状態
- マスクしたメールアドレスまたは認証基盤が提供する本人のメールアドレス
- メール認証状態
- 同意済み利用規約のバージョンと同意日
- 会員登録日時と同意履歴
- ログアウト
- 二段階確認による退会導線
- 問い合わせ先
- Phase 2の通知条件画面への導線

メールアドレス変更機能をPhase 1に含めるかは**要決定**。

### 10.2 後続Phaseで追加する情報

- メール・LINEの通知チャネル状態
- LINE連携状態
- 通知履歴
- 現在のプラン、利用上限、契約状態

### 10.3 認可

- マイページは認証必須とする。
- URLやリクエスト中の `user_id` を信用せず、認証セッションの利用者IDを使用する。
- Phase 1の利用者は自分のprofileと規約同意履歴だけを参照できる。profile・同意履歴の直接書込みはできず、現行規約への同意だけを引数なしRPCで行う。

## 11. 通知条件

通知条件設定はPhase 2で提供し、実装は完了している。通知条件の一覧・新規作成・編集・一時停止・有効化・削除UI、原子的保存RPC、1利用者5件の上限、純粋Pythonの照合エンジン、service-role専用の条件取得RPCは実装済みである。Phase 2は条件管理と空き候補に対する照合結果の生成までを責務とする。

Phase 3ではqueue、email delivery worker、production deployment、canary、automatic enqueue/dispatch、delivery feedback、unsubscribe / re-enable、90日retention cleanupまで実装しproduction acceptanceを完了した。

### 11.1 確定データ構造

- 条件本体は `notification_rules` に保存し、`auth.users.id` を所有者とする。
- 条件名は空白不可・80文字以内とする。
- 対象施設は `notification_rule_facilities`、対象曜日は `notification_rule_weekdays` に複数登録する。
- 曜日はISO 8601の1（月曜日）〜7（日曜日）で保存する。
- `date_from` は対象開始日、`date_to` は対象終了日とする。`date_from = null` は開始日の下限なし、`date_to = null` は終了日の上限なしを表す。
- `start_time < end_time` とし、時刻は対象施設が属する地域の `regions.timezone` で評価する。
- 最低連続時間は30〜720分の30分単位とする。
- `is_enabled` は有効・一時停止状態を表し、子テーブル登録前の不完全な条件を誤って有効扱いしないよう初期値を `false` とする。
- DB上は施設または曜日が0件の不完全な条件を保存できる。UIでは施設1件以上・曜日1件以上を必須検証し、照合処理では0件の条件を無効として扱う。
- 通知条件は有効・停止中を含めて1利用者最大5件とする。5件でも既存条件の編集・有効化・一時停止・削除は可能で、削除すれば再び追加できる。

無料・有料プランごとの差分と利用者が設定可能な最大期間は**要決定**である。通知チャネル、配信キュー、再試行、配信停止、通知履歴はPhase 3以降の責務であり、Phase 2の通知条件には含めない。

通知条件UIは、active会員本人の条件だけをRLS配下で読み込み、「登録済み n / 5件」を表示し、施設1件以上・曜日1件以上を含む入力検証を行う。5件では新規作成ボタンを無効化して既存条件の削除を案内し、削除後の再取得で4件以下になれば再び有効化する。新規フォーム開始時と保存直前にも件数を確認するが、編集・有効化・一時停止・削除は上限の影響を受けない。

作成と編集は `save_notification_rule` RPCを使用し、条件本体・施設・曜日を1トランザクションで保存する。RPCは利用者ID引数を受け取らず、`security invoker`、`auth.uid()`、`set search_path = ''`、既存RLSを維持する。新規作成のINSERTはDBの上限triggerを通るため、RPC経由の6件目も拒否される。編集は `user_id` を変更しないUPDATEであり、5件時も保存できる。一時停止と有効化は本人行の `is_enabled` 更新、削除は本人行のDELETEを使用する。不完全な条件はUIから有効化しない。

上限の最終的な強制箇所は `notification_rules` の `before insert or update of user_id` triggerである。`security invoker` と空の `search_path` を使用し、RLSや既存policyを変更しない。新規作成または所有者変更時は、`new.user_id` から生成した安定した64bitキーでtransaction advisory lockを取得してから移動先の全条件を数える。同一利用者の並行作成を直列化し、6件以上になる操作を拒否する。DBの競合エラーはUIで日本語へ変換し、一覧を再取得して実件数とボタン状態を同期する。

現行のMonitoring Policyでは、直近15日間の土日・日本の祝日、8:00〜13:00について、スクレイパーが連続60分以上の空き候補を生成する。

通知条件の最低連続時間は30分以上を指定できるため、取得済みの60分以上候補との実際の重複時間が条件の最低連続時間以上なら、60分未満を希望する条件も一致し得る。通常の平日や8:00〜13:00外は現在取得しない。平日の曜日条件でも、その日が日本の祝日として取得対象であれば一致し得る。

Monitoring Policy外の条件も保存可能である。通知条件画面には現行取得範囲の案内を表示しており、範囲外条件の警告表現と60分未満を希望する条件の説明はLaunch Readiness GateのMonitoring Policy UXで現在の照合仕様へ整合させる。祝日は祝日専用条件ではなく、実際の日付のISO曜日で判定する。

### 11.2 照合ルール

- スクレイパーが正規化した空き枠と、有効な利用者条件を通知エンジンが照合する。
- 日別entryが `status = success`、枠が `status = available` の場合だけ照合する。`error`、`selector_pending`、`fallback_from_previous` などの保持データから新しい一致を生成しない。
- 条件が有効で施設と曜日が各1件以上あり、施設ID、空き日付のISO曜日、任意の `date_from` / `date_to` を満たすことを確認する。日付境界は含む。
- 条件時間帯と空き時間帯の実際の重複分数を求め、`minimum_duration_minutes` 以上の場合だけ一致する。枠全体の `duration_minutes` は最低時間判定に使わない。
- 同一利用者の複数条件が同じ `slot_id` へ一致しても、利用者・`slot_id` の候補は1件にまとめる。一致した条件は、条件ID、重複開始・終了時刻、重複分数を持つ `matched_rules` として決定的にソートする。
- 同じ `slot_id` でも利用者が異なる場合は別候補とする。入力枠の重複は `slot_id` 単位で除去し、候補順も決定的にする。
- チャネル展開、配信済み判定、キュー、再試行、delivery feedback、配信停止はPhase 3で実装済みである。重複防止の正は利用者・channel・`slot_id` とし、同じ利用者へ同じchannel・`slot_id`を再通知しない。利用者向け通知履歴画面は未実装である。
- match詳細は `data/`、GitHub Pages、公開Artifactへ保存せず、CLIログは評価件数と一致件数の集計だけを出力する。

通知条件は `list_notification_rules_for_matching()` から取得する。このRPCはactive会員の有効かつ施設・曜日が各1件以上ある条件だけを対象に、条件ID、利用者ID、日付範囲、開始・終了時刻、最低時間、施設ID配列、ISO曜日配列だけを返し、メールアドレスを返さない。`security invoker`、`stable`、空の `search_path`、完全修飾名を使用し、既存RLSやpolicyを変更しない。実行権限は `PUBLIC`、`anon`、`authenticated` から剥奪して `service_role` だけへ付与するため、Web UIやpublishable keyからは呼び出せない。

GitHub Actionsではスクレイピング後に通知条件照合を行い、Phase 3の有効化条件を満たす場合は `matching -> enqueue -> dispatch` の順で利用者別メール配信へ進む。service-role credentialは必要なstepにだけ渡し、match詳細、利用者ID、条件ID、秘密値をPages、Artifact、通常ログへ保存・出力しない。

Phase 3.4.2でscheduled production runによる自動メール配信を確認済みであり、Phase 3.4.3で単一通知先legacy LINE経路とそのstate fileを削除した。

## 12. メール通知

利用者別メール通知はPhase 3で提供し、production acceptanceまで完了している。Resendを使用するqueue / delivery worker、自動enqueue / dispatch、delivery feedback、unsubscribe / re-enable、90日retention cleanupを本番運用している。Phase 1の認証メールとはAPI key、目的、テンプレート、配信停止、送信処理を分離する。

### 12.1 通知内容

- サービス名
- 施設名
- 利用日と曜日
- コートまたは予約対象名
- 空き時間帯
- 公式予約サイトURL
- 情報取得時刻または「最新情報は公式サイトで確認」の注意
- 通知条件またはマイページへの導線
- 配信停止導線

### 12.2 配信要件

- メール認証済みで、退会・停止されておらず、メール通知が有効な利用者だけを対象とする。
- キューを介して再試行可能にし、スクレイピング処理の成否から送信処理を分離する。
- DBの重複防止の正は `notification_delivery_items` の `unique (user_id, channel, slot_id)` とする。同じ利用者の複数条件が同じ `slot_id` に一致しても、その利用者・channelでは1回だけ通知対象とする。
- バウンス、苦情、配信停止を記録し、以後の配信に反映する。
- 認証メールはマーケティング通知停止の影響を受けないが、法令・事業者ポリシーに従う。

現行の配信事業者はResendである。queue、再試行、delivery feedback、配信停止、90日retentionは実装済みである。将来の利用規模に応じた送信上限、アラート閾値、ダイジェスト等の追加配信方式は必要性を確認して見直す。

## 13. 将来のLINE連携

利用者別LINE通知はPhase 4で提供する。Phase 0の単一通知先によるlegacy運用者向けLINE通知はPhase 3.4.3で退役済みであり、Phase 4では再利用しない。

### 13.1 連携要件

- 会員としてログインした状態から連携を開始する。
- 短時間で失効する一回限りの連携状態を用いて、別会員への誤連携を防ぐ。
- LINEユーザーIDは個人情報に準じてデータベースへ保存し、GitHubへ保存しない。
- 連携解除、LINE側ブロック、配信不能、退会を通知対象へ反映する。
- 1会員に紐づけ可能なLINEアカウント数と、1LINEアカウントを複数会員へ紐づける可否は**要決定**。

### 13.2 移行

- Phase 0の既存管理者LINE通知はlegacy notification pathとしてPhase 3.4.3で退役した。
- Phase 4は管理者専用LINE経路を再構築せず、管理者を含む会員共通LINE notification基盤として導入する。
- 導入日、重複配信防止、監視、ロールバックを定める。
- LINE Loginを認証に使用するか、Messaging API連携だけに使用するかは**要決定**。

## 14. 無料・有料プラン案

料金、上限値、提供開始時期はいずれも**要決定**。次は検討用の案であり、確定仕様ではない。

| 機能 | 無料プラン案 | 有料プラン案 |
| --- | --- | --- |
| 公開空き状況の閲覧 | 利用可 | 利用可 |
| 通知条件数 | 少数に制限 | 上限を拡大 |
| 対象施設数 | 鹿児島市内の一部または少数 | 複数施設 |
| メール通知 | 基本通知 | 条件数・頻度を拡大 |
| LINE通知 | 制限付きまたは対象外 | 利用可 |
| 詳細条件 | 基本の曜日・時間帯 | 複数時間帯、詳細条件 |
| 複数地域 | 対象外または制限 | 利用可 |
| 通知履歴 | 短期間 | 長期間 |

「優先通知」は取得時刻そのものに差を付けると公平性・運用負荷へ影響するため、意味と実現可能性を確認するまで**要決定**とする。

有料化前に、決済事業者、価格、税、返金、解約、支払い失敗、特定商取引法等の表示、無料利用者の既存データの扱いを決定する。

Phase 5のeffective entitlementは次の確定方針で判定する。

- `account_role = admin`: billing不要でPro相当
- `account_role = member` かつ有効な有料subscriptionあり: Pro
- `account_role = member` かつ有効な有料subscriptionなし: Free

アカウント権限とsubscription/planは別概念とし、adminの権限を表すためのsubscription rowは作らない。

## 15. データモデル

Phase 1会員・規約テーブルに加え、Phase 2の地域・施設マスターと通知条件テーブルをmigrationで確定した。配信、通知履歴、課金などPhase 3以降のモデルは引き続き論理モデル案とする。

### 15.1 会員・規約

| エンティティ | 主な項目 | 備考 |
| --- | --- | --- |
| `auth.users` | `id`, `email`, `email_confirmed_at`, 認証メタデータ | Supabase Auth採用時。認証基盤が管理 |
| `profiles` | `id`, `account_role`, `membership_status`, `latest_terms_version`, `latest_terms_accepted_at`, `created_at`, `updated_at` | `auth.users(id)` をcascade参照。メールアドレスを重複保存しない |
| `legal_document_versions` | `document_type`, `version`, `effective_at`, `is_current`, `created_at` | 同一文書種別のcurrentは部分一意indexで1件 |
| `terms_acceptances` | `id`, `user_id`, `document_type`, `version`, `accepted_at`, `source` | 追記専用。同一利用者・文書種別・版は一意 |

開発用現行規約版は `2026-08-04-draft` である。一般公開前に正式本文・版番号・発効日へ更新し、正式版への再同意を求める。

`profiles.account_role`（`member` / `admin`）をアカウント権限のauthoritative sourceとする。`membership_status`、subscription、planとは独立した属性であり、メールアドレス、GitHub username、Auth user metadata、フロントエンドだけの判定からadminを推測しない。

### 15.2 施設・空き

| エンティティ | 主な項目 | 備考 |
| --- | --- | --- |
| `regions` | `id`, `country_code`, `prefecture_code`, `municipality_code`, `name`, `timezone`, `is_active`, `sort_order`, `created_at` | GPSに依存しない地域マスター |
| `facility_types` | `id`, `name`, `is_active`, `sort_order`, `created_at` | 初期値は `tennis-court` |
| `facilities` | `id`, `region_id`, `facility_type_id`, `name`, `is_active`, `sort_order`, `created_at` | IDは `availability.json.facility_id` と一致 |

Phase 2の初期データは鹿児島市とテニスコート種別、鴨池県営テニスコート、SuMIzeiテニスコート、東開庭球場である。予約対象リソース、取得元、取得実行、空き枠のDBモデルは後続の候補であり、今回実装しない。公開用 `availability.json` は維持し、Phase 0のlegacy notification state fileはPhase 3.4.3で削除した。

Phase 2のテーブル・RLS・初期マスター、`save_notification_rule` RPC、service-role専用 `list_notification_rules_for_matching` RPC、1利用者5件の上限triggerはmigrationとしてリポジトリへ追加している。リポジトリへの追加だけではSupabase環境へ自動適用されないため、適用状況は対象環境ごとのmigration履歴で確認する。上限migrationは、適用前に既に6件以上を持つ利用者がいる場合、利用者IDやメールアドレスを表示せず失敗する。適用済みmigrationは変更せず、修正は新しいmigrationで前方適用する。

### 15.3 通知

| エンティティ | 主な役割 | 備考 |
| --- | --- | --- |
| `notification_rules` | 利用者の通知条件本体 | Phase 2。1利用者最大5件 |
| `notification_rule_facilities` | 通知条件と施設の関連 | Phase 2 |
| `notification_rule_weekdays` | 通知条件とISO曜日の関連 | Phase 2 |
| `notification_email_preferences` | 利用者ごとのメール通知ON/OFFと停止理由 | Phase 3。メールアドレスは保持しない |
| `notification_delivery_items` | 利用者・channel・`slot_id`単位の重複防止台帳と送信用snapshot | `unique (user_id, channel, slot_id)` が重複防止の正 |
| `notification_messages` | 配信queue、claim、retry、provider状態 | 利用者単位の送信試行 |
| `notification_message_items` | messageとdelivery itemの関連 | delivery itemを1つのmessageへ関連付ける |
| `notification_provider_events` | Resend delivery feedbackの重複排除・状態反映 | raw webhook payloadや宛先は保存しない |
| `notification_email_unsubscribe_tokens` | メール配信停止用capability | browser roleから直接参照しない |

Phase 3のメール通知データモデルは実装・production rollout済みである。
メールアドレスはSupabase Authを正とし、通知用public schemaへ複製しない。
利用者別LINE通知用のデータモデルはPhase 4で前方追加する。

詳細は [Phase 2 通知条件データモデル設計](./PHASE2_NOTIFICATION_RULES_DESIGN.md)、
[Phase 3 利用者別メール通知設計](./PHASE3_USER_EMAIL_NOTIFICATION_DESIGN.md)を参照する。

### 15.4 課金案

| エンティティ | 主な項目 | 備考 |
| --- | --- | --- |
| `plans` | `id`, `code`, `name`, `limits`, `active` | 権限と上限 |
| `subscriptions` | `id`, `user_id`, `plan_id`, `provider_customer_id`, `provider_subscription_id`, `status`, `current_period_end` | カード情報は保存しない |
| `billing_events` | `provider_event_id`, `type`, `received_at`, `processed_at`, `status` | Webhookの冪等処理 |

決済情報は決済事業者に委ね、カード番号・セキュリティコードを本サービスのデータベースやログへ保存しない。

## 16. 認証・認可

### 16.1 認証

- Phase 1はメールアドレスのマジックリンクとメール認証を使用し、パスワード認証は使用しない。
- 認証処理とセッション管理は正式採用したSupabase Authへ委ねる。
- GitHub Pagesからはブラウザ公開用キーだけを使い、認可の最終境界はRLSとする。
- LINE連携はPhase 4であり、Phase 1のメール認証を省略しない。
- 一般利用者はSupabase Authentication Usersとして管理し、Dashboard管理権限を持つSupabase Organization Teamへ追加しない。Team所属は認証メールの送信やログインの前提ではない。

### 16.2 認可

- 公開空き状況と規約は匿名で閲覧可能とする。
- マイページ、通知条件、通知履歴、LINE連携、契約情報は認証必須とする。
- RLSまたは同等のサーバー側認可により、利用者単位の行アクセスを強制する。
- 管理用Service Role等はサーバー処理だけで使用し、ブラウザ、Pages、リポジトリへ含めない。
- 管理者権限の付与・解除は、明示的なAuth user UUIDを受け取るtrusted server専用RPCで行う。role変更監査と緊急停止方法は将来の運用要件に応じて追加する。

### 16.3 Phase 1〜3 RLS

- Phase 1の3つのpublicテーブルと、Phase 2で追加する6テーブルすべてでRLSを有効にする。
- `profiles`: `id = auth.uid()` の本人だけがSELECT可能。ブラウザからのINSERT/UPDATE/DELETEは許可しない。
- `profiles.account_role`: authenticated本人は自分のroleをSELECTできるが変更できない。anonは参照・変更できない。`set_account_role(uuid, account_role)` は `SECURITY DEFINER`、空のsearch path、service-role専用とする。
- `terms_acceptances`: `user_id = auth.uid()` の本人だけがSELECT可能。ブラウザからのINSERT/UPDATE/DELETEは許可しない。
- `legal_document_versions`: authenticated利用者はcurrentのtermsだけをSELECT可能。anonにはDB権限を与えない。
- `accept_current_terms()` はauthenticatedだけがEXECUTEでき、anonとPUBLICから実行権限を剥奪する。
- `notification_rules`と関連表: `authenticated` のうち、本人かつ `profiles.membership_status = 'active'` の利用者だけがCRUD可能。1利用者最大5件をDB triggerで強制し、有効・停止中の両方を数える。
- `enforce_notification_rule_limit()` は `security invoker` と既存RLSのままtriggerから実行する。PUBLIC、anon、authenticatedには直接EXECUTEを許可しない。
- `save_notification_rule()` は `security invoker` と既存RLSのまま動作し、`auth.uid()` で本人を確定する。PUBLICとanonから実行権限を剥奪し、authenticatedだけにEXECUTEを許可する。
- `list_notification_rules_for_matching()` は `security invoker` のservice-role専用RPCとし、ブラウザロールへEXECUTEを許可しない。メールアドレスは返さない。
- `notification_email_preferences`: active会員本人は自分の設定を参照し、許可された範囲でメール通知ON/OFFを変更できる。provider起因のsuppression状態はブラウザから解除しない。
- `notification_delivery_items`、`notification_messages`、`notification_message_items`、`notification_provider_events`、`notification_email_unsubscribe_tokens`: RLSを有効にし、通常のbrowser roleへ直接参照・更新権限を与えない。必要な処理はservice-role専用RPCまたは信頼済みEdge Functionから行う。
- `regions`、`facility_types`、`facilities`: `authenticated` はSELECTのみ可能とし、`anon` にはDB参照権限を与えない。ブラウザroleによるINSERT、UPDATE、DELETEは許可しない。

## 17. 個人情報とセキュリティ

### 17.1 個人情報の扱い

- 必要な情報だけを収集し、利用目的を表示する。
- メールアドレス、LINEユーザーID、契約識別子、同意履歴、問い合わせ情報を個人情報またはそれに準ずる情報として扱う。
- メールアドレス等をGitHubリポジトリ、Issue、Pages、Actions Artifact、テストfixtureへ保存しない。
- 本番データを開発・テストへコピーしない。テストには架空データを使用する。
- ログではメールアドレス、トークン、Cookie、認証リンク、LINEユーザーIDを削除またはマスクする。

### 17.2 Secret管理

- Secretはホスティング基盤またはGitHub Actions Secrets等の専用機能で管理する。
- Phase 2照合用 `SUPABASE_SERVICE_ROLE_KEY` は照合stepだけへ渡し、ジョブ全体の環境変数、ブラウザ、Pages、公開Artifact、ログへ出さない。
- ブラウザへ配布可能な公開キーと、Service Role、メール・LINE・決済の秘密鍵を区別する。
- Resend APIキーはドメイン限定のSending accessで作成し、Supabase Custom SMTP passwordとしてだけ使用する。APIキーの値自体は本仕様書へ記載しない。
- 本番Secretへのアクセス権を最小化し、ローテーション手順を用意する。
- Secret検知をCIへ導入するかは**要決定**。

### 17.3 セキュリティ要件

- 通信はHTTPSに限定する。
- 認証、登録、マジックリンク再送、退会にレート制限を設ける。
- CSRF、XSS、オープンリダイレクト、セッション固定、権限昇格を設計・テスト対象とする。
- 依存関係とGitHub Actionsを継続的に更新・監査する。
- バックアップ、復元テスト、監査ログ、インシデント対応手順を用意する。
- データ保持期間、バックアップ保持期間、監査ログ保持期間は**要決定**。

### 17.4 公開データ

`availability.json` とGitHub Pagesは公開情報として扱う。個人別条件、通知履歴、メールアドレス、LINE識別子、契約情報を公開JSONへ含めない。

## 18. 退会・通知停止

### 18.1 通知停止

- チャネル単位の停止と、通知条件単位の一時停止を区別する。
- メールには配信停止導線を設ける。
- LINEの連携解除・ブロック・配信不能を通知状態に反映する。
- 通知停止後にキュー済みメッセージが送られないよう、送信直前にも状態を確認する。
- 認証・セキュリティに必要なトランザクションメールまで停止するかは目的別に定義する。

### 18.2 退会

現行のPhase 1退会処理は2026-08-20に本番確認済みである。

1. ログイン済み利用者がマイページで削除対象と影響を確認する。
2. 二段階確認UIで明示的に退会を実行する。
3. 現在の認証済みJWTをEdge Functionで検証し、利用者IDはリクエスト本文ではなく認証主体から決定する。
4. `profiles.membership_status` を `withdrawal_pending` へ先にロックする。
5. Auth Admin APIでAuthユーザーをhard deleteし、関連する利用者所有データをFK cascadeで削除する。
6. ブラウザのローカルセッションを破棄してログイン画面へ戻る。

Phase 4のLINE連携解除、Phase 5の有料契約処理は各Phaseで追加する。同じメールアドレスによる再登録、追加再認証、法定保持データ、バックアップからの消去時期は一般公開前の運用・法務事項として**要決定**である。

## 19. 責務分離

| コンポーネント | 責務 | 持たない責務 |
| --- | --- | --- |
| スクレイパー | 取得元への適切な頻度でのアクセス、DOM/API解析、空き枠の共通形式への正規化、取得状態・診断情報の記録 | 会員情報の参照、利用者条件の評価、メール・LINE送信 |
| 空きデータ層 | 施設・予約対象・空き枠・観測時刻の保持、Pages用公開データの提供 | 認証、通知先の保持 |
| 通知エンジン | 新規・再出現枠の判定、利用者条件との照合、重複防止、配信キュー作成、再試行状態管理 | 取得元固有DOMの解析、パスワード管理 |
| チャネルアダプター | メール・LINE事業者への送信、応答の正規化、バウンス・ブロック等の反映 | 条件判定、施設スクレイピング |
| 会員基盤 | 登録、規約同意、メール認証、セッション、プロフィール、退会状態、認可 | 取得元へのアクセス、空き判定 |
| Supabase Auth / Resend | Phase 1認証リンクの生成、Custom SMTPによる認証メール送信、配信イベントの提供 | Phase 2の通知条件管理、Phase 3の利用者別空き通知 |
| Web UI | 公開空き表示、会員操作、通知条件操作、状態の表示 | Service Roleの保持、認可の最終判断 |
| 課金基盤 | プラン、契約状態、決済Webhook、権限への反映 | カード情報の直接保持、空き取得 |

### 19.1 連携原則

- スクレイパーは個人情報を必要としない。
- 通知エンジンはスクレイパーの内部DOM構造ではなく、正規化済み空き枠だけを受け取る。
- 会員基盤は通知チャネルを有効化できる状態を提供し、実際の送信は通知エンジンとチャネルアダプターが行う。
- 各処理は安定IDと冪等性キーを使い、再実行で重複通知や重複課金を起こさない。
- コンポーネント間の方式は、初期は同一リポジトリ・同一データベースでもよいが、モジュールと権限の境界を維持する。

## 20. 現行機能を維持した移行方針

- `scripts/scrape.py`、`data/availability.json`、`index.html`、現在のGitHub Actionsをavailability取得・Pages公開の稼働系として扱う。
- 会員基盤はPhase 0と独立して追加し、最初からスクレイパーへ認証依存を持ち込まない。
- Phase 3の構築・canary中も公開Pagesを継続し、Phase 3.4.2で利用者別メールのautomatic scheduled runを確認した。
- データベース導入後も、Pagesが必要とする公開データの生成を維持する。
- 通知条件照合、email enqueue、email dispatchは独立した有効化Variableとcredential境界を持ち、失敗時もavailability取得・Pages公開を継続する。
- Phase 3.4.3でlegacy管理者LINE通知を停止・削除し、管理者も通常の利用者通知pipelineへ移行した。

## 21. 全国展開方針

### 21.1 地域モデル

- 国、都道府県、市区町村、施設を別の識別子で管理する。
- 表示名を識別子に使用せず、安定した内部IDを付与する。
- 地域ごとのタイムゾーン、休日カレンダー、言語、予約制度を設定可能にする。
- GPSは使用せず、検索、一覧、お気に入り、通知条件で地域を選択する。

### 21.2 取得元の追加

- 予約システム単位でスクレイパーアダプターを実装する。
- 施設設定はコードから分離し、同じ予約システムの施設でアダプターを再利用する。
- 取得元ごとに利用規約、robots関連情報、アクセス頻度、認証要否、障害状況を記録する。
- 認証回避、アクセス制限回避、利用者の予約認証情報の保存は行わない。
- 新地域は検証、限定公開、監視、一般公開の順に追加する。

福岡・東京は候補であり、追加順、自治体、対象施設は需要と取得可能性を検証して**要決定**。

## 22. 他施設種別への拡張方針

- 「施設」と「予約対象リソース」を分け、テニスコート、体育室、グラウンド、会議室を共通の空き枠へ正規化する。
- 種別共通項目は地域、施設名、予約対象名、開始・終了時刻、状態、予約URLとする。
- 面数、収容人数、競技種目、設備、全面・半面などの固有情報は拡張属性または種別別テーブルで扱う。
- 通知条件は共通条件と種別固有条件を分け、無関係な条件を利用者へ表示しない。
- スクレイパーの追加を中心に拡張し、通知エンジン、会員基盤、チャネルアダプターを再利用する。

体育館、野球場、会議室は候補であり、最初に追加する種別は需要、取得難易度、空き枠表現を検証して**要決定**。

## 23. 非機能要件

### 23.1 可用性・障害分離

- 1施設の取得失敗で他施設の取得・表示を停止しない。
- スクレイピング失敗と通知送信失敗を区別する。
- 取得失敗中の過去データは現在の空きとして通知しない。
- 会員基盤や通知チャネルの障害時にも、公開空き表示を可能な範囲で継続する。

### 23.2 性能・拡張性

- 取得、条件照合、配信を分離し、それぞれを再実行・水平分割できる構造にする。
- 地域・施設・日付をキーとして処理範囲を分割できるようにする。
- 全国展開前に、条件数と空き枠数に対する照合方式を負荷試験する。

目標応答時間、通知遅延、稼働率、最大会員数は各Phaseの利用規模を基に**要決定**。

### 23.3 アクセシビリティ

- キーボード操作、ラベル、フォーカス、エラーのテキスト表示、十分なコントラストを考慮する。
- 重要な状態を色だけで表現しない。
- 対象とする適合水準は**要決定**。

## 24. 現在の技術MVPに含めない機能

2026-08-20時点の技術MVPはPhase 0〜3であり、
空き表示、会員基盤、通知条件、利用者別メール通知までを含む。

現在の技術MVPには次を含めない。

- 自動予約、予約代行、キャンセル、施設利用料の支払い
- 施設予約サイトへのログイン、利用者ID・パスワードの保存
- GPSによる地域制限、現在地の常時取得
- 鹿児島市以外の地域対応
- テニスコート以外の施設種別
- 利用者別LINE通知
- LINEログイン、ソーシャルログイン
- 有料プラン、決済
- 利用者向け通知履歴画面
- AIによる空き予測・おすすめ
- 地図表示、混雑分析
- ネイティブモバイルアプリ、ブラウザプッシュ、SMS
- グループ・法人アカウント
- 本格的な運用管理画面

次の機能はLaunch Readiness、利用者需要、取得元の規約、
運用コストを確認したうえで順次追加する。

## 25. 要決定事項一覧

2026-08-20時点で残る主な要決定事項を追跡する。
決定時は理由、決定日、影響範囲をArchitecture Decision Record等へ残す。

- Supabaseの料金枠、バックアップ要件、開発・ステージング・本番の環境分離
- 利用規約・プライバシーポリシーの正式内容、運営者表示、問い合わせ先、規約改定時の再同意
- 認証リンクの運用上の有効期限、未認証アカウント保持期間
- メールアドレス変更機能の要否
- 同意証跡、監査ログ、バックアップの保持期間と削除方式
- 退会失敗時に `withdrawal_pending` 利用者の本人profile・同意履歴参照をさらに制限するか
- 退会時に現在のJWTとは別の追加再認証を要求するか
- Monitoring Policyを需要に応じて拡張する基準と監視頻度
- LINE連携方式、LINE Loginの採否、LINEアカウント紐づけ制約
- 無料・有料プランの機能、価格、決済事業者、法務・税務・返金
- 目標稼働率、応答時間、通知遅延、監視・アラート閾値
- 次に展開する地域・自治体と施設種別
