# Tennis Court Watcher 開発ロードマップ

## 位置づけ

本書は、[Project Vision](./PROJECT_VISION.md)を段階的に実現するための開発計画である。
各Phaseの開始前に、直前のPhaseで得られた利用状況、運用負荷、通知品質を確認し、優先順位を見直す。

## 開発方針

- 最初の提供地域と施設種別は、鹿児島市のテニスコートに限定する。
- 地域制限にGPSは使用しない。利用者が対象地域・施設を選択する方式とする。
- 内部の識別子、データモデル、責務境界は、全国・他施設種別へ拡張できる形にする。
- スクレイパー、通知エンジン、会員基盤を分離し、取得元ごとの差異をスクレイパー側へ閉じ込める。
- 現在稼働しているスクレイピング、GitHub Pages、利用者別メール通知を維持しながら段階的に移行する。Phase 0のlegacy管理者LINE通知はPhase 3.4.3で退役済みであり、Phase 4の利用者別LINE通知とは分離して扱う。
- アカウント権限の正は `public.profiles.account_role` とし、メールアドレス、GitHub username、Auth user metadata、フロントエンドだけの判定に依存しない。`account_role` は会員状態や契約・プランとは独立して管理する。
- GitHubリポジトリ、Actions Artifact、Pages公開データにメールアドレスなどの個人情報を保存しない。
- 会員基盤はSupabase Auth/PostgreSQLを正式採用する。認証方式はメールのマジックリンクとし、GitHub Pagesを継続する。認証メールはCloudflare Registrarで管理する `email.tenniscourtwatcher.com` とTokyoリージョンのResend Custom SMTPを使用する。Supabaseの料金枠など、明記した項目は引き続き**要決定**。
- 未決事項は検証または意思決定を終えるまで「要決定」とし、実装上の前提として固定しない。

## Phase一覧

| Phase | 状態 | 到達点 |
| --- | --- | --- |
| Phase 0 | 完成済み | 鹿児島市3施設の空き状況を定期取得してPages表示する。Phase 0で導入したlegacy管理者LINE経路はPhase 3.4.3で退役済み |
| Phase 1 | 完成済み | 規約同意・メール認証を伴う会員登録、ログイン、マイページを提供する |
| Phase 2 | 完了 | 通知条件UI、原子的保存、1利用者5件の上限、空き候補との照合を提供する |
| Phase 3 | 進行中 | Phase 3.5b unsubscribe / re-enableのproduction acceptance完了。残りはPhase 3.5aの24〜48時間aggregate observation完了確認とPhase 3.5cの90日retention cleanup |
| Phase 4 | 計画 | LINE公式アカウントと会員を連携し、利用者別LINE通知を行う |
| Phase 5 | 計画 | 無料・有料プランを提供する |
| Phase 6 | 計画 | 福岡・東京など鹿児島市以外へ展開する |
| Phase 7 | 計画 | 体育館、野球場、会議室などへ施設種別を広げる |

---

## Phase 0: 鹿児島市3施設の空き監視

**状態: 完成済み**

### 目的

鹿児島市のテニスコートについて、「予約サイトを繰り返し見る」負担を減らすための取得・表示・通知の最小ループを確立する。

### 成果物

- 鴨池県営テニスコート、SuMIzeiテニスコート、東開庭球場の公開画面スクレイパー
- 直近15日間の土日祝、8:00〜13:00、連続1時間以上を対象とする空き候補データ
- 取得成功、空き0件、取得エラーを区別する `data/availability.json`
- GitHub Pagesによる空き候補、取得状態、更新時刻の表示
- 新規 `slot_id` を検出する差分通知エンジン（Phase 3.4.3で退役）
- 鴨池県営・SuMIzeiを対象とした単一通知先LINE通知（Phase 3.4.3で退役）
- 通知済み状態を保持するstate file（Phase 3.4.3で削除）
- GitHub Actionsによるテスト、定期取得、データ更新、Pages配信
- HTML、PNG、診断JSONのArtifact保存と自動テスト

既存の単一通知先LINE通知はPhase 0を完成させた時点のlegacy notification pathであり、恒久的な管理者専用経路ではなかった。Phase 3.4.2で利用者別メールの自動配信を本番確認した後、Phase 3.4.3で送信コード、workflow設定、baseline state fileを削除した。管理者も一般会員と同じ通知条件、queue、email workerを利用する。

### 完了条件

- 3施設を認証情報なしで取得できる。
- 同じ空き枠を重複表示・重複通知しない。
- 施設単位の取得失敗が他施設の取得を止めない。
- legacy LINE稼働中は、送信失敗時も最新の空き状況を更新し、次回に通知を再試行できた（Phase 3.4.3で経路退役）。
- 現在のdry-runはデータ・Artifact取得を行い、commit、push、Pages deployを行わない。
- GitHub Pagesでスマートフォンから空き候補と取得状態を確認できる。
- 現行のpytestとGitHub Actionsが成功する。

### 対象外

- 会員登録、ログイン、マイページ
- 利用者ごとの通知条件
- 利用者ごとのメール・LINE通知
- 自動予約、予約代行
- 鹿児島市以外、テニスコート以外

---

## Phase 1: 会員基盤

**状態: 完成済み**

### 目的

利用規約への同意とメールアドレス確認を必須とする会員登録、ログイン、ログアウト、マイページを提供し、後続Phaseの利用者別設定と通知の安全な土台を作る。

### 成果物

- 会員登録、メール認証、ログイン、ログアウトの一連の認証フロー
- `email.tenniscourtwatcher.com` とTokyoリージョンのResend Custom SMTPによる認証メール配信
- Confirm sign upとMagic link or OTPの用途を分けた日本語メールテンプレート
- 利用規約本文、規約バージョン、同意日時を記録する仕組み
- 認証済み利用者だけがアクセスできるマイページ
- 会員状態を管理するデータモデルと認可ポリシー
- 個人情報をGitHub管理対象から分離した環境・Secret構成
- 監査、エラー監視、運用手順の最小セット
- Phase 1リリース時点で既存のスクレイピング、Pages、legacy LINE通知を維持した段階的リリース

### 完了条件

- 未同意の利用者は会員登録を完了できない。
- メール未認証の利用者は有効な会員としてマイページを利用できない。
- 認証済み利用者はログイン、マイページ表示、ログアウトができる。
- 規約のバージョンと同意日時を利用者ごとに追跡できる。
- 他の利用者のプロフィールや同意履歴を読み書きできない。
- メールアドレス、認証情報、セッション、認証メール内容がGitHubリポジトリ、Pages、公開Artifactへ保存・出力されない。
- Phase 1完了時点でPhase 0の取得、Pages表示、当時のlegacy LINE通知が回帰テストを含め継続動作した。
- 本番運用に必要な環境変数、バックアップ、障害対応、退会対応の手順が文書化されている。

### 完了確認（2026-08-06）

- Supabase Organization Teamに所属していない一般メールアドレスへの送信に成功した。
- 初回登録のConfirm sign upに成功した。
- 登録済みユーザーのMagic link or OTPによる通常ログインに成功した。
- 同じブラウザで通常セッションが保持されることを確認した。
- 本番環境でprofiles、RLS、規約同意履歴が正しく動作することを確認した。
- Resend EmailsでDeliveredを確認した。
- 認証メールの設定、秘密情報管理、確認、障害対応を [Auth Email Operations](./AUTH_EMAIL_OPERATIONS.md) に文書化した。

### 対象外

- 通知条件の保存
- 利用者別メール通知、利用者別LINE通知
- 課金
- ソーシャルログイン、LINEログイン
- 管理画面の本格実装
- GPSによる地域判定・地域制限

### Phase 1 Sprint計画

各Sprintは、単独でレビュー・検証でき、既存サービスを停止せずに統合できる大きさとする。番号は実装順を示し、期間は固定しない。

#### Sprint 1.1: 会員基盤の境界とデータ層

**目的**

既存の公開監視機能と会員機能の境界を決め、個人情報を安全に保管できる最小のバックエンドを用意する。

**成果物**

- Supabase Auth/PostgreSQLの採用決定記録と技術検証結果
- 開発・ステージング・本番の環境分離方針
- `auth.users`と公開プロフィール、規約同意を分離した初期スキーマ
- Row Level Security（RLS）と最小権限の初期ポリシー
- クライアント公開可能な設定とサーバー専用Secretの分類
- Phase 0との接続点を定めた責務図

**完了条件**

- ローカルまたは検証環境でマイグレーションを再現できる。
- 匿名利用者と会員が、他者の会員データを取得できないことをテストできる。
- GitHubへ個人情報を保存しない構成がレビューされている。
- 2026-08-04のSupabase正式採用決定が設計へ記録されている。

**対象外**

- 本番の会員登録画面
- 通知条件・通知送信

#### Sprint 1.2: 利用規約と会員登録

**目的**

利用規約を確認し、明示的に同意した利用者だけが登録を開始できるようにする。

**成果物**

- 利用規約画面
- 会員登録画面
- 必須同意チェックと、規約バージョン・同意日時の記録
- 入力検証、重複登録時の安全な応答、送信中・失敗時の表示
- 規約改定時の扱いに関する運用案

**完了条件**

- 規約への同意なしでは登録要求を送信できない。
- 同意した規約バージョンと日時を改ざん困難なサーバー側処理で保存できる。
- 登録済みかどうかを第三者が推測しにくいエラー表示になっている。
- 利用規約本文、運営者表示、問い合わせ先、改定通知方法が確定している。法務確認の要否は**要決定**。

**対象外**

- メール認証完了後のマイページ
- 通知条件

#### Sprint 1.3: メール認証

**目的**

登録したメールアドレスを本人が利用できることを確認し、認証済み会員だけを有効化する。

**成果物**

- 認証メール送信
- 認証待ち画面
- 認証コールバックと認証完了・失敗画面
- 認証メール再送
- 期限切れ、使用済み、改変されたリンクの処理

**完了条件**

- メール未認証の利用者はマイページへ進めない。
- 有効な認証リンクを一度使用すると会員が認証済みになる。
- 無効・期限切れリンクから認証状態を変更できない。
- 再送の回数制限またはレート制限が有効である。
- メール送信元、独自ドメイン、配信事業者、再送間隔が決定され、SPF・DKIM・DMARCと一般メールアドレスへの配信を確認できる。

**対象外**

- 空き情報のメール通知
- LINE連携

#### Sprint 1.4: ログイン、セッション、ログアウト

**目的**

認証済み会員が安全にセッションを開始・終了できるようにする。

**成果物**

- ログイン画面
- 認証済み状態を確認するルート保護
- ログアウト機能
- セッション期限切れ・認証失敗時の導線

**完了条件**

- 有効なマジックリンクとメール認証済み状態でのみログインできる。
- 未認証、無効化、退会済みの会員は保護画面へアクセスできない。
- ログアウト後に保護画面へ戻れない。
- セッション情報をURL、ログ、GitHub管理ファイルへ出力しない。
- パスワード認証とパスワード再設定がPhase 1へ混入していない。

**対象外**

- 複数端末の詳細なセッション管理
- 多要素認証

#### Sprint 1.5: マイページと会員ライフサイクル

**目的**

会員が自分の登録状態を確認し、通知機能追加前のアカウント管理を行えるようにする。

**成果物**

- マイページ
- メール認証状態、規約同意状態、会員状態の表示
- 退会受付と処理方針
- 問い合わせ導線
- 後続Phaseの通知設定へつなぐ拡張領域

**完了条件**

- 会員は自分の情報だけを閲覧できる。
- 退会すると新しいログインができず、後続通知の対象から外せる状態になる。
- 個人情報の削除・匿名化・保持期間が決定し、処理を監査できる。
- Phase 2の通知条件を追加してもプロフィールや認証スキーマの破壊的変更を必要としない。

**対象外**

- 通知条件の実入力
- 支払い情報

#### Sprint 1.6: 統合、セキュリティ確認、段階リリース

**目的**

Phase 1全体を運用可能な品質にし、Phase 0へ影響を与えずに公開する。

**成果物**

- 登録から退会までのE2Eテスト
- RLS、レート制限、Secret、ログのセキュリティ確認
- 障害対応、バックアップ、問い合わせ対応のRunbook
- 段階公開とロールバック手順
- Phase 0回帰テスト

**完了条件**

- 正常系と主要な異常系の自動テストが成功する。
- 個人情報やSecretがPages、リポジトリ、Actionsログ、Artifactへ混入しないことを確認できる。
- 会員機能を無効化してもPhase 0を継続できる。
- 利用規約、プライバシーに関する表示、運用責任者、問い合わせ窓口がリリース可能な状態である。

**対象外**

- Phase 2以降の機能を先行して本番提供すること

---

## Phase 2: 通知条件設定

**状態: 完了**

### 目的

利用者が、通知を受けたい空き条件を自分で登録・変更・停止できるようにする。

### 成果物

- 通知条件の一覧・作成・編集・削除・一時停止UI
- 地域、施設、曜日、日付範囲、時間帯、最小連続時間を表現できる条件モデル
- 鹿児島市3施設を選択するマスターデータ
- 条件の妥当性検証
- 利用者ごとの条件数上限（有効・停止中を含めて最大5件）
- 条件と現在の空き候補を照合する評価ロジック

### 実装状況（2026-08-07）

- 地域・施設種別・施設マスターと、通知条件・施設・曜日のデータモデルをmigrationへ追加した。
- 本人かつactive会員だけが通知条件を操作できるRLSと、マスターをauthenticated read-onlyにする最小権限を追加した。
- 通知条件の一覧・新規作成・編集・一時停止・有効化・削除UIを追加した。
- UIに「登録済み n / 5件」を表示し、5件で新規作成を無効化して削除案内を表示する。削除後の再取得で4件以下になれば新規作成を再び有効化する。
- 条件本体・施設・曜日を1トランザクションで保存する `save_notification_rule` RPCを追加した。
- 有効・停止中を含む通知条件を1利用者最大5件に制限した。最終的な強制箇所は `notification_rules` のtriggerであり、新規フォーム開始時と保存直前にもUIで上限を確認する。
- 上限triggerは `new.user_id` から生成した64bitキーでtransaction advisory lockを取得してから件数を数え、同一利用者の並行作成でも6件以上にならないよう直列化する。5件時も既存条件の編集・有効化・一時停止・削除は可能で、削除後は追加できる。
- 日別取得が `success` の空き枠と有効な条件を、施設、ISO曜日、任意の日付範囲、実際の時間帯重複で判定する純粋Python照合エンジンを追加した。
- 同一利用者・同一 `slot_id` を1候補にまとめ、複数の一致条件を決定的にソートした `matched_rules` として保持する。別利用者は別候補とする。
- active会員の有効かつ完全な条件だけを返す `list_notification_rules_for_matching()` を、`security invoker` のservice-role専用RPCとして追加した。
- GitHub Actionsは `ENABLE_NOTIFICATION_MATCHING=true` の場合だけスクレイピング後に照合し、service-role keyをそのstepのSecret環境変数だけへ渡す。照合失敗をwarningに留め、availability取得・JSON commit・Pages更新をブロックしない。
- match詳細は `data/`、GitHub Pages、公開Artifactへ保存せず、CLIログも集計値だけとする。
- 現在の取得範囲は直近15日間の土日・日本の祝日、8:00〜13:00、60分以上である。範囲外の条件も保存できるが、対象データを取得しないため現時点では一致しない。祝日は実際の日付の曜日で判定する。
- [Phase 2 通知条件データモデル設計](./PHASE2_NOTIFICATION_RULES_DESIGN.md)とUI・RPC・照合エンジン・workflowの静的テストを追加した。
- Phase 2は完了である。通知条件の保存・管理、1利用者5件の上限、空き候補との照合までを実装済みである。
- Phase 3.1のqueue foundation、Phase 3.2のemail delivery worker、Phase 3.3のproduction deploymentとcanary検証、Phase 3.4のautomatic enqueue/dispatchとlegacy経路整理は完了した。
- リポジトリへのmigration追加だけではSupabase環境へ自動適用されないため、適用状況は環境ごとに確認する。

### 完了条件

- 認証済み会員が自分の条件だけを管理できる。
- GPSを使わず、利用者の明示選択で対象地域・施設が決まる。
- 現在の鹿児島市向け条件を保存でき、将来の地域・施設種別追加でも条件スキーマを流用できる。
- 同じ利用者の同じ空きに複数条件が一致しても、利用者・`slot_id` ごとに1件の照合結果を生成できる。
- 有効・停止中を含む通知条件を1利用者最大5件に制限し、DB triggerで並行作成を含めて強制できる。

### 対象外

- 実際の利用者別メール・LINE送信
- 自然言語による条件入力
- AIによる空き予測

Phase 2は通知条件の保存・編集・停止と照合結果の生成までを担当する。条件に基づいて実際に利用者別メールを送信する処理はPhase 3で実装する。

Actionsで照合を実行するには、Repository Variable
`ENABLE_NOTIFICATION_MATCHING=true` と `SUPABASE_URL`、Repository Secret
`SUPABASE_SERVICE_ROLE_KEY` が必要である。照合用migrationと上限migrationの適用状況は環境ごとに確認し、適用済みmigrationは編集しない。

---

## Phase 3: 利用者別メール通知

### 目的

新しく検出した空き候補を利用者の通知条件と照合し、該当する利用者へメールで知らせる。

### 現在状況（2026-08-18）

- Phase 3.1: queue foundationは完了した。
- Phase 3.2: email delivery workerは完了した。
- Phase 3.3: production migrationとEdge Function deploymentを完了し、実メールcanaryのaccepted、実メールボックスでの受信、Resend Deliveredを確認した。秘密値、メールアドレス、provider message IDは記録しない。
- Phase 3.4: automatic enqueue/dispatchとlegacy経路整理は完了した。
  - Phase 3.4.1: automation foundation complete。GitHub Actionsから利用者別メール候補をenqueueし、delivery workerを1回dispatchするコードと、default OFFの独立した安全フラグを追加した。
  - Phase 3.4.2: production staged enablementを完了し、本番scheduled runでautomatic scheduled emailを確認した。
  - Phase 3.4.3: legacy administrator LINEを退役し、単一通知先送信コードとbaseline state fileを削除した。
  - Phase 3.4.4: scheduler reliability hardening。3.4.4aでlive runのsource checkoutをbranch headへ固定し、3.4.4bでGitHub native scheduleをprimaryとしたまま、45分以上qualifying runが生成されない場合だけSupabase DB/Edge Function/Cronからfallback dispatchするwatchdogを実装した。production rolloutは `off -> observe 24〜48h -> dispatch` とし、Cron jobはmigration外で手動作成する。
- Phase 3.5: Resend delivery feedbackと配信停止を段階導入する。
  - Phase 3.5a: sender correlation tag、service-role専用event RPC、Svix署名検証Edge Function、sent/delayed/delivered/failed/bounced/complained/suppressedの状態反映、pgTAP/Deno/pytest、runbookのコード実装は完了した。at-least-onceは`svix-id`、out-of-orderはprovider event `created_at`と固定priorityで処理する。raw payloadと宛先情報は保存・ログ出力しない。
  - Phase 3.5a production rollout: migration適用、webhook deploy、missing/invalid signatureの`401`、実署名付き外部Authメールの`ignored_unmatched 200`、通知canaryのsent→delivered、provider eventsのsent/delivered、duplicate replayの`stored_event_count=0`まで確認済み。24〜48時間のaggregate観察を継続し、完了時に異常status、retry滞留、bounce/complaint/suppressionを再確認する。
  - Phase 3.5b: production rolloutとacceptanceを2026-08-18に完了した。本文footerはAccount UI設定画面だけを指し、本文からunsubscribe capability tokenを除去した。synthetic canary 1件はaccepted→delivered、raw sourceで`List-Unsubscribe`と`List-Unsubscribe-Post`の独立headerを確認した。詳細は[Phase 3 Email Unsubscribe Runbook](./PHASE3_EMAIL_UNSUBSCRIBE.md)を正とする。
  - Phase 3.5c: 90日retention cleanupのmigration、service-role専用bounded RPC、pgTAP/pytest、production runbookを実装する。message単位でprovider event/message itemをcascade削除し、delivery itemは過去日・90日超・参照なしの場合だけ最後に削除する。production rolloutとcron作成はcode merge後に別途行う。詳細は[Phase 3 Retention Cleanup Runbook](./PHASE3_RETENTION_CLEANUP.md)を正とする。

管理者も一般会員と同じ通知条件、配信queue、email workerを利用する。管理者専用のメール通知経路は作らない。

### 成果物

- 通知対象を決定するジョブ
- メール配信キュー、送信処理、再試行、失敗管理
- 利用者・空き枠・条件・チャネル単位の重複防止
- 通知メールと配信停止導線
- 配信履歴と運用メトリクス

### 完了条件

- 条件に一致した認証済み・通知有効な利用者だけに送信する。
- 同じ空き枠を同じ利用者へ重複送信しない。
- 一時的な送信失敗を安全に再試行し、恒久エラーを停止できる。
- メール内リンクから認証済みの通知停止または安全なワンクリック停止ができる。
- バウンス・苦情・配信停止を次回以降の送信に反映できる。
- 配信事業者、送信ドメイン、送信数上限、保持期間は**要決定**。

### 対象外

- LINE公式アカウント連携
- SMS、プッシュ通知
- 空き枠の確保や予約

---

## Phase 4: LINE公式アカウント連携と利用者別LINE通知

### 目的

会員とLINEユーザーを安全に紐づけ、利用者ごとの通知条件に合う空き候補をLINEで届ける。

### 成果物

- LINE公式アカウントの友だち追加・連携導線
- 会員とLINEユーザーIDの安全な紐づけ、解除、再連携
- 利用者別LINE配信キューと重複防止
- ブロック、配信不能、連携解除の反映
- 会員共通LINE通知基盤と、legacy管理者LINE停止後も重複配信を起こさない導入手順

### 完了条件

- LINE連携操作を行った会員本人にだけLINEユーザーIDを紐づけられる。
- 条件一致した利用者へ個別に通知し、別利用者の条件や識別子を露出しない。
- 連携解除・退会・通知停止を以後の配信へ反映できる。
- 管理者を含む全会員が同じLINE notification基盤を利用し、管理者専用LINE経路を再構築しない。
- LINE Loginを併用するか、Messaging APIのみで連携するかは**要決定**。

### 対象外

- LINEを唯一の会員認証手段にすること
- LINE上での予約完結

---

## Phase 5: 有料プラン

### 目的

継続運用費を支えながら、無料利用者にも基本価値を残す料金体系を導入する。

### 成果物

- 無料・有料プランの権限定義
- 申込、決済、更新、解約、支払い失敗対応
- 契約状態と機能制限を一元管理する仕組み
- 特定商取引法等を含む必要表示と問い合わせ運用
- 売上・解約・転換率の最小メトリクス

### Admin entitlement方針

Phase 5でeffective entitlementを判定するときは、アカウント権限と課金状態を別々の入力として扱う。

- `account_role = admin` はbilling不要でPro相当とする。
- `account_role = member` かつ有効な有料subscriptionがある場合はProとする。
- `account_role = member` かつ有効な有料subscriptionがない場合はFreeとする。

`account_role` とsubscription/planは別概念である。adminのPro相当権限を表現するためにsubscription rowを偽造しない。account role変更はUUIDで対象を指定するtrusted server操作に限定する。メールアドレス等のidentity属性からroleを推測せず、将来、運用規模と要件に応じてrole変更監査履歴を追加する。

### 完了条件

- 契約状態に基づく認可をサーバー側で強制できる。
- 解約・支払い失敗・返金時の状態遷移が定義されている。
- Webhookを検証し、重複イベントを安全に処理できる。
- 価格、無料枠、無料期間、決済事業者、返金方針、税務・法務対応は**要決定**として残さず、提供開始前に決定されている。

### 対象外

- 法人向け個別契約
- 広告配信
- 自動予約の販売

---

## Phase 6: 福岡・東京など他地域への展開

### 目的

鹿児島市で確立した監視・通知・会員機能を、他自治体・地域のテニスコートへ展開する。

### 成果物

- 国・都道府県・市区町村・施設を分離した地域マスター
- 取得元ごとのスクレイパーアダプター
- 地域・取得元ごとの稼働監視、頻度制御、利用規約確認記録
- 地域選択UIと地域別運用手順
- 新地域追加時の受け入れテスト

### 完了条件

- 鹿児島市向けコードを複製せず、新しい取得元をアダプターとして追加できる。
- 利用者がGPSなしで複数地域から対象を選択できる。
- 地域ごとのタイムゾーン、休日、施設ルール、予約URLを正しく扱える。
- 各取得元の規約・アクセス頻度・技術的安定性を確認している。
- 最初に追加する自治体と優先順位は利用状況を基に決定する。候補は福岡・東京だが**要決定**。

### 対象外

- 全自治体の一括対応
- 取得制限の回避
- 公式予約システムに代わる予約受付

---

## Phase 7: 他施設種別への展開

### 目的

テニスコート向けに確立した共通基盤を、体育館、野球場、会議室などの公共施設へ拡張する。

### 成果物

- 施設種別と種別固有属性を扱う拡張可能なデータモデル
- 面・室・区画などを共通化する予約対象リソースモデル
- 施設種別ごとの通知条件テンプレート
- 種別別の表示、単位、利用時間、連続枠ルール
- 新施設種別の品質・運用基準

### 完了条件

- 通知エンジンと会員基盤を変更せず、スクレイパーと種別設定の追加を中心に展開できる。
- コート、体育室、グラウンド、会議室などを共通の「予約対象リソース」として扱える。
- 種別固有の条件を無関係な種別へ強制しない。
- 最初に追加する施設種別は需要と取得可能性を検証して決定する。候補は体育館、野球場、会議室だが**要決定**。

### 対象外

- 民間施設を含むマーケットプレイス化
- 決済・予約・入退場管理の代行
- すべての施設種別に共通しない属性の過度な標準化

## 継続して確認する品質指標

- 施設・日付ごとの取得成功率と連続失敗時間
- 空き検出から通知までの遅延
- 重複通知率と通知失敗率
- メールのバウンス・苦情・配信停止率
- 会員登録開始、メール認証、登録完了の転換率
- 退会率、有料転換率
- 取得先へのアクセス頻度と利用規約の遵守状況
- 個人情報・Secretのリポジトリ、Pages、ログ、Artifactへの混入有無

指標の目標値とアラート閾値は、各Phaseの開始時に**要決定**とする。
