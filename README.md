# Tennis Court Watcher

鹿児島市のテニスコート予約サイトを確認し、直近15日間の土日祝にある8:00〜13:00の空き候補を、GitHub Pagesと利用者別メールで知らせるプロジェクトです。

> [!IMPORTANT]
> 鴨池県営テニスコート、SuMIzeiテニスコート、東開庭球場の空き取得は、いずれも認証不要の実画面に対応済みです。スクレイパーは予約サイトの利用者ID・パスワードを使用・保存せず、自動予約も行いません。会員ログインはこれとは分離したSupabase Authのメールマジックリンクを使用します。

> [!NOTE]
> 取得元から公開空き情報の自動確認・表示・通知について利用許可を取得済みです。認証情報を使わない現在の範囲を維持し、アクセス頻度、失敗率、HTTP 403/429、予約サイトの規約・仕様変更を継続監視します。判断根拠と運用指標は[Launch Readiness Review](docs/LAUNCH_READINESS_REVIEW.md)を参照してください。

## Documentation

現在利用者へ提供している仕様はService Specification、
現在地・次の作業はDevelopment Roadmapを正とします。
Phase別設計書は詳細設計と実装履歴を含みます。
- [Project Vision](docs/PROJECT_VISION.md)
- [Development Roadmap](docs/DEVELOPMENT_ROADMAP.md)
- [Service Specification](docs/SERVICE_SPECIFICATION.md)
- [Launch Readiness Review](docs/LAUNCH_READINESS_REVIEW.md)
- [Phase 4 LINE Notification Design](docs/PHASE4_LINE_NOTIFICATION_DESIGN.md)
- [Phase 4 LINE Notification Rollout](docs/PHASE4_LINE_NOTIFICATION_ROLLOUT.md)
- [Phase 1 Auth Design](docs/PHASE1_AUTH_DESIGN.md)
- [Phase 2 Notification Rules Design](docs/PHASE2_NOTIFICATION_RULES_DESIGN.md)
- [Auth Email Operations](docs/AUTH_EMAIL_OPERATIONS.md)
- [Phase 3 User Email Notification Design](docs/PHASE3_USER_EMAIL_NOTIFICATION_DESIGN.md)
- [Phase 3 Resend Webhook Runbook](docs/PHASE3_RESEND_WEBHOOK.md)
- [Phase 3 Email Unsubscribe Runbook](docs/PHASE3_EMAIL_UNSUBSCRIBE.md)
- [Phase 3 Scheduler Watchdog](docs/PHASE3_SCHEDULER_WATCHDOG.md)

## 現在の機能

- 今日を含む直近15日間から土曜日、日曜日、日本の祝日を抽出
- 8:00〜13:00内で1時間以上ある空きだけを保持
- 同一コートの連続した空きセルを結合
- 鴨池県営のVue生成DOMをコート行・時刻ヘッダー・状態セル単位で解析
- SuMIzeiと東開のP-Kashikan公開フォームを施設設定と対象日で遷移し、共通処理でコート行の状態セルを解析
- 成功、空き0件、取得エラーを区別して `data/availability.json` に保存
- JSONを読み込むスマートフォン向けGitHub Pages画面
- 施設ごとの取得状態・最終確認時刻と、全体・施設別の空き候補件数を表示
- 最終更新から60分を超えると更新遅延、120分を超えると更新停止を警告
- 空きなしの正常取得日は初期状態で折りたたみ、施設ごとに表示を切り替え
- 成功・失敗を問わず診断用HTMLとPNGを保存
- pytest、GitHub Actions、Pages自動配信
- Supabase Authのメールマジックリンク送信、PKCE callback、セッション確認、ログアウト
- 認証用画面（ログイン・会員登録、認証callback、マイページ、利用規約、プライバシーポリシー）
- active会員向け通知条件の一覧・新規作成・編集・一時停止・有効化・削除UI
- 会員向け空き通知で土曜・日曜・日本の祝日を個別選択し、8:00〜13:00内の時間帯と利用時間を選ぶUI
- 通知条件本体・施設・曜日・祝日選択を原子的に保存する `save_notification_rule` RPC
- 有効・停止中を含めて1利用者最大5件とするDB triggerと、件数表示・追加制御UI
- 正常取得日の空き枠と有効な通知条件を、実際の重複時間で判定する純粋Python照合エンジン
- active会員の有効な条件だけを返すservice-role専用 `list_notification_rules_for_matching` RPC
- 利用者・チャネル・`slot_id` 単位で重複を防ぐメール配信queueとdelivery worker
- GitHub Actionsによる `matching -> enqueue -> dispatch` の定期メール配信
- 署名済みResend webhookの重複・順序逆転に耐えるdelivery feedbackとbounce/complaint/suppression時の通知停止
- 通知メール内の日本語停止リンク、RFC 8058 one-click unsubscribe、再有効化時のtoken rotation、provider suppressionを解除しないAccount UI
- 固定版 `supabase-js` v2と、Repository Variablesから生成するブラウザ公開設定

空き状況は候補です。予約前に必ず公式サイトで最新情報を確認してください。

## Phase 1 認証プロジェクト基盤

2026-08-04時点で、Supabase Auth、メールのマジックリンク認証、GitHub Pages、PKCEを正式方針としました。ブラウザは固定版 `@supabase/supabase-js@2.106.2` を使用し、公開Project URLとpublishable keyだけで接続します。`flowType: "pkce"`、`persistSession: true`、`autoRefreshToken: true` を明示しています。

本番の認証メールはResend Custom SMTPを使用し、送信用サブドメインは `email.tenniscourtwatcher.com` です。初回登録用と通常ログイン用の日本語テンプレートを分け、Supabase Organization Teamに所属していない一般メールアドレスで初回登録・通常ログイン・配信を確認済みです。Resend APIキーやSMTP passwordなどの秘密値はリポジトリへ保存しません。設定、テンプレート、確認手順、障害対応は [Auth Email Operations](docs/AUTH_EMAIL_OPERATIONS.md) を参照してください。

実装済みの範囲は、メール形式・利用規約同意の確認、`signInWithOtp` によるマジックリンク送信、codeの `exchangeCodeForSession`、認証URLの消去、`getSession` によるログイン画面とマイページのセッション確認、会員profileと規約同意履歴の本人表示、現行規約への同意、現在のブラウザを対象にした `signOut({ scope: "local" })` です。成功・失敗文言からアカウントの存在有無を推測しにくくし、メールアドレス・code・token・認証URLをconsoleへ出しません。

`persistSession: true` と `autoRefreshToken: true` により、ログアウトしない限り、同じブラウザでは通常セッションが保持されます。ログインページはフォーム表示前に既存セッションを確認し、ログイン済みならマイページへ移動します。ブラウザを閉じても通常は次回そのまま利用できますが、ログアウト、ブラウザデータの削除、セッションの無効化、別の端末やブラウザからの利用時には再認証が必要です。ログアウトは操作したブラウザのセッションだけを終了し、全端末ログアウトは行いません。このセッション確認は画面UXのためのもので、会員データの最終的な認可境界は引き続きPostgreSQLのRLSです。

PKCEのcode verifierはリンクを要求したブラウザ側に保存されるため、マジックリンクは原則としてログイン操作を開始した同じブラウザで開く必要があります。別端末・別ブラウザで開いて認証に失敗した場合は、利用するブラウザでログイン画面から再送してください。

`supabase/migrations/20260804000000_create_member_profiles.sql` に `legal_document_versions`、`profiles`、`terms_acceptances`、新規Authユーザー用trigger、既存ユーザーbackfill、RLS、最小権限Grant、引数なしの `accept_current_terms()` RPCを実装しています。`20260806000000_fix_accept_current_terms_conflict.sql` は、適用済みの関数を制約名指定の `ON CONFLICT` へ置き換えます。`20260821034956_finalize_terms_version.sql` は正式規約 `2026-08-21` をcurrentへ切り替え、過去の同意履歴を保持したままactive会員へ再同意を要求します。ブラウザから会員データを直接変更する権限はなく、同意登録だけをRPCへ集約します。退会Edge Functionと二段階確認UIは2026-08-19に実装し、2026-08-20に本番deployとproduction acceptanceを完了しました。本人JWTから利用者を確定し、membership_statusをwithdrawal_pendingへロックしてからサーバー側特権処理でAuthユーザーを削除します。Authユーザー削除後に関連する利用者所有データがFK cascadeで削除されることも本番で確認済みです。Phase 4はLINE account linkのDB基盤とserver-side境界を本番反映し、My Pageの連携・状態・解除UIまで実装済みで、スマホ実機の正方向acceptanceも完了しました。利用者別LINE配信のqueue・webhook・workerは初期OFFで実装し、隔離したローカル環境で検証済みです。本番migration・Function deploy・段階有効化は未実施です。課金は未実装です。

正式な現行規約版は `2026-08-21`、発効日は2026-08-21です。運営者はグランドスラム（鹿児島市内テニスサークル）で、問い合わせ先は法務ページに簡易難読化して表示します。重要な規約改定では新しい版を追加し、過去の同意履歴を保持したまま再同意を求めます。

追加した画面は次のとおりです。すべてGitHub Pagesのリポジトリ配下で動く相対リンクを使用します。

- `auth/login.html`: マジックリンクによるログイン・会員登録画面
- `auth/callback.html`: メール認証callback画面
- `account/index.html`: 最小限のマイページ
- `account/notifications.html`: 空き通知の一覧・作成・編集・停止・有効化・削除画面
- `legal/terms.html`: 正式な利用規約
- `legal/privacy.html`: 正式なプライバシーポリシー

法務ページは2026-08-21に正式化し、運営者、問い合わせ窓口、版番号、発効日、取得情報、利用目的、委託先、第三者提供、90日通知データ保持、退会時削除、開示等請求、規約改定時の再同意を確定しました。詳細は[Phase 1 Auth Design](docs/PHASE1_AUTH_DESIGN.md)を参照してください。

Phase 2は完了です。`supabase/migrations/20260807000000_create_notification_rules.sql` に鹿児島市3施設のマスター、通知条件・施設・曜日の関連、本人かつactive会員に限定したRLSを定義し、`20260807100000_add_notification_rule_save_rpc.sql` に原子的保存用 `save_notification_rule` RPCを追加しています。`20260807130000_limit_notification_rules_per_user.sql` は、有効・停止中を含む通知条件を1利用者最大5件に制限し、`20260821022637_add_configurable_notification_targets.sql` は祝日選択と監視範囲内の時間帯選択を追加します。DB triggerが最終的な件数強制箇所で、同一利用者の並行作成はtransaction advisory lockを取得してから件数を数えることで直列化します。UIにも「登録済み n / 5件」を表示し、5件では新規追加を無効化します。既存条件の編集・有効化・一時停止・削除は可能で、削除すれば再び追加できます。`scripts/match_notification_rules.py` の空き候補照合エンジンとservice-role専用取得RPCも実装済みです。Phase 3.4.1の自動化基盤、Phase 3.4.2の本番段階有効化とscheduled email確認は完了し、Phase 3.4.3でlegacy管理者LINE経路を退役しました。Phase 4はLINE account linkを本番反映し、利用者別LINE配信の安全な実装を初期OFFで追加しています。match詳細は公開Artifact、Pages、`data/` へ保存しません。リポジトリへのmigration追加だけではSupabase環境へ自動適用されないため、適用状況は環境ごとに確認してください。詳細は[Phase 2 Notification Rules Design](docs/PHASE2_NOTIFICATION_RULES_DESIGN.md)を参照してください。

照合対象は、日別取得結果が `success` で、枠の `status` が `available` のデータだけです。施設、選択した土曜・日曜または祝日、任意の日付範囲を確認し、通知時間帯と空き時間帯の実際の重複分数が最低連続時間以上なら一致します。同じ利用者の複数条件が同じ `slot_id` へ一致しても利用者・枠の候補は1件にまとめ、別利用者は別候補にします。

現在のMonitoring Policyでは、直近15日間の土日・日本の祝日、8:00〜13:00について、連続60分以上の空き候補を取得します。会員向け空き通知では、土曜・日曜・祝日を個別に選び、同じ8:00〜13:00内を1時間境界・最低2時間の範囲で指定します。利用時間は60、120、180、240、300分から選択し、初期値は120分です。選択した利用時間は通知時間帯に収まる必要があります。祝日は曜日とは独立して保存し、空き日が選択曜日または日本の祝日のどちらかに一致すれば日条件を満たします。通常の平日は取得対象ではないため、祝日でない平日が通知候補になることはありません。

`20260821022637_add_configurable_notification_targets.sql` は、PR #55の固定条件を表すISO曜日1〜7・08:00〜13:00の既存データだけを、土曜・日曜・祝日を選択した新モデルへ変換します。移行後の画面は保存値をそのまま表示し、「以前の設定」という別表示は行いません。

## ファイル構成

```text
.
├── .github/workflows/update-availability.yml
├── account/
│   ├── index.html
│   └── notifications.html
├── assets/
│   ├── config/auth-config.example.js
│   ├── css/auth.css
│   └── js/
│       ├── auth-foundation.js
│       └── notification-rules.js
├── auth/
│   ├── callback.html
│   └── login.html
├── data/availability.json
├── docs/
│   ├── DEVELOPMENT_ROADMAP.md
│   ├── LAUNCH_READINESS_REVIEW.md
│   ├── PHASE1_AUTH_DESIGN.md
│   ├── PHASE2_NOTIFICATION_RULES_DESIGN.md
│   ├── PHASE4_LINE_NOTIFICATION_DESIGN.md
│   ├── PROJECT_VISION.md
│   └── SERVICE_SPECIFICATION.md
├── legal/
│   ├── privacy.html
│   └── terms.html
├── scripts/
│   ├── __init__.py
│   ├── generate_auth_config.py
│   ├── enqueue_email_notifications.py
│   ├── dispatch_email_notifications.py
│   ├── match_notification_rules.py
│   ├── report_line_usage.py
│   └── scrape.py
├── supabase/migrations/
│   ├── 20260807000000_create_notification_rules.sql
│   ├── 20260807100000_add_notification_rule_save_rpc.sql
│   ├── 20260807110000_add_notification_rule_matching_rpc.sql
│   ├── 20260807120000_grant_notification_matching_rpc_dependencies.sql
│   └── 20260807130000_limit_notification_rules_per_user.sql
├── tests/
│   ├── fixtures/kamoike_schedule.html
│   ├── fixtures/sumizei_schedule.html
│   ├── fixtures/toukai_schedule.html
│   ├── test_auth_foundation.py
│   ├── test_notification_rules_schema.py
│   ├── test_notification_rules_ui.py
│   ├── test_notification_rule_matching.py
│   ├── test_notification_rule_matching_rpc.py
│   ├── test_notification_matching_service_role_grants.py
│   ├── test_page.py
│   └── test_scrape.py
├── index.html
├── requirements.txt
└── README.md
```

実行時には鴨池県営を `snapshots/kamoike-prefectural/YYYY-MM-DD.html`、P-Kashikanの2施設を `snapshots/{sumizei|toukai-tennis}/YYYY-MM-DD-step-name.html` と同名のPNGへ保存します。P-Kashikanはトップ、施設検索、施設選択後、対象日の空き状況を段階別に保存します。スナップショットはGit管理せず、GitHub ActionsのArtifactとして7日間保存します。

## 鴨池県営の抽出方式

2026年7月21日に対象サイトへPlaywrightでアクセスし、次の実DOMを確認しました。

- 予約結果全体: `.rsv__result[data-reserve]`
- コート行: `.rsv__result[data-reserve] > section.rsv__field`
- コート名: `h3.rsv__result__item:not(.major--item--color) em`
- 時刻ヘッダー: `.rsv__result__time > li`
- 状態帯: `.rsv__result__situation > li`
- 予約可: `.rsv--result--yes` と `area-label="予約可"`
- 予約済み: `.rsv--result--no`
- 予約不可: `.rsv--result--out`

状態セルは開始・終了時刻を直接持たず、`style="width: ...%"` で時間幅を表します。各コート行の時刻ヘッダー先頭・末尾を時間軸の境界とし、分類済み状態セルの合計幅に対する各セルの割合から時刻を復元します。行外の `.rsv__result__example` は凡例なので解析対象にしません。

非表示の予約結果・コート行は除外し、同じ `slot_id` は重複除去します。DOM構造が不足している場合は、空き0件として扱わず `unexpected_dom` を記録します。

## P-Kashikan施設の抽出方式

SuMIzeiは2026年7月21日、東開は2026年7月24日に、Playwrightで認証なしの画面遷移と通信を確認しました。

1. トップの「施設 の空きを見る」から `index.php` の施設空き状況へ遷移
2. `input[name="ShisetsuCode"]` から施設設定に一致するラジオボタンを選択
3. 公開画面が通常使用するフォーム値を対象日に変更して日別画面を表示
4. `.SelectCalendar` 内の時間ヘッダーとコート行だけを解析

施設設定はSuMIzeiが `#scd029`（値 `029`、画面表記「ＳｕＭＩｚｅｉテニスコート」）、東開が `#scd131`（値 `131`、画面表記「東開庭球場」）です。コードと選択後の施設見出しの両方を照合し、対象が見つからない場合は `facility_not_found` としてその施設だけをエラーにします。

内部APIやJSONエンドポイントは使用されていませんでした。画面遷移は `index.php` への通常のPOSTで、次の値を送信します。

| Form値 | 内容 |
| --- | --- |
| `op` | `srch_sst`（施設の空き状況） |
| `ShisetsuCode` | 施設設定のコード（SuMIzei `029`、東開 `131`） |
| `UseYM` | `YYYYMM` |
| `UseDay` | 月内の日 |
| `UseDate` | `YYYYMMDD` |
| `disp_span` | `0`（1日表示） |

実DOMでは、時間軸が `.SelectCalendar table.koma-table th`、各コート名が `td.name` にあります。インターネット予約可能な空きセルは `○` と表示され、セルの `id` と `onmousedown` に施設・コート識別子、日付、`HHMMHHMM` 形式の開始・終了時刻が含まれます。実際に確認した例は次の形です。

```html
<td id="131|003|...#2026/07/25#1"
    onmousedown="setAppStatus('131|003|...', '2026/07/25', 0, '08300900', ...);">
  ○
</td>
```

パーサーはコート行内の `●`、`○`、`〇` だけを空き候補とし、`×`、`-`、`確認中`、予約済み、抽選、メンテナンスなどを除外します。`○` は実属性の時間帯を優先し、`●` は同じ行のセル幅と時間ヘッダーから時間を復元します。凡例は `.SelectCalendar` 外なので解析対象になりません。施設コード、選択日、時間ヘッダー、コート行のいずれかが不整合なら、空き0件ではなく施設単位のエラーにします。

P-Kashikanでは公式画面の時刻境界が内部値やセル幅計算上 `:29` / `:59` になる場合があるため、それぞれ1分進めて `:30` / 次の正時へ補正してから連続枠を結合します。この補正はSuMIzeiと東開だけに適用し、鴨池県営の時刻解析には適用しません。`slot_id` は補正後の公式表示時刻から生成します。

東開の実画面では、コート名は「Aコート(ナイターあり)」「Bコート(ナイターなし)」「C・Dコート(ナイターあり)」です。時間軸は8時台の最初が8:30〜9:00の30分枠、その後は通常60分枠です。監視境界は共通の8:00〜13:00ですが、東開の実データは営業時間に従って8:30からとなり、結合後60分未満の空きは除外します。同じ表示名が複数の内部コート行に現れるため、連続枠はDOM上の同一行内だけで結合してから重複除去します。

## 連続枠の扱い

同じ日・同じコートで終了時刻と次の開始時刻が一致する場合は結合します。JSONには結合後の枠だけを保存し、元の細分化された枠は残しません。これにより利用者別通知の照合とPages表示で同じ空きを重複して扱いません。

8:00〜13:00の境界で空き枠を切り詰め、結合後の長さが60分未満の候補は除外します。

## availability.json

現在のスキーマバージョンは2です。空き枠には次の情報を保存します。

```json
{
  "facility_id": "kamoike-prefectural",
  "facility_name": "鴨池県営テニスコート",
  "date": "2026-08-01",
  "court_name": "コート２",
  "start_time": "11:00",
  "end_time": "13:00",
  "duration_minutes": 120,
  "status": "available",
  "reservation_url": "https://v2.spm-cloud.com/user/kamoike-undo/reserves/daily?date=2026-08-01&category_id=483&area_id=289",
  "slot_id": "安定したSHA-256由来の24文字ID"
}
```

`slot_id` は `facility_id + date + court_name + start_time + end_time` から生成します。

日別データは次の状態を持ちます。

- `success`: 正常取得。空きがない場合も `availability: []` で成功
- `error`: 取得またはDOM解析に失敗。`error_type` と `error_message` を保持
- `selector_pending`: 旧データとの互換用。現在の3施設では生成しない

エラー時も `checked_at` と `reservation_url` を保存します。通常は空の `availability` を保存しますが、P-KashikanがHTTP 403を返した場合は、直前の正常取得データがあれば `status: error` のまま `availability` を保持し、`fallback_from_previous: true` と `last_success_checked_at` を記録します。画面上では取得エラーとして扱い、保持した枠を現在の空き件数には加えません。主な `error_type` は `navigation_timeout`、`navigation_error`、`access_denied`、`facility_not_found`、`date_selection_failed`、`no_schedule_table`、`unexpected_dom` です。

## ローカルセットアップ

Python 3.11以上を使用します。

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --requirement requirements.txt
python -m playwright install chromium
```

### テスト

```bash
python -m pytest
```

テストfixtureは実DOMから抽出した必要最小限の構造だけを匿名化して保存しています。取得したページ全体はfixtureとしてコミットしません。

### ローカルでの認証画面確認

PowerShellでは、ローカルまたは検証用Supabaseプロジェクトの公開値を現在のプロセスへ設定し、Pagesと同じ生成スクリプトを実行します。

```powershell
$env:SUPABASE_URL = "https://<project-ref>.supabase.co"
$env:SUPABASE_PUBLISHABLE_KEY = "<publishable-key>"
$env:AUTH_CALLBACK_URL = "http://localhost:8765/auth/callback.html"
.\.venv\Scripts\python.exe scripts\generate_auth_config.py
.\.venv\Scripts\python.exe -m http.server 8765
```

ブラウザで `http://localhost:8765/auth/login.html` を開きます。Supabase DashboardのAuth Redirect URLsにも `http://localhost:8765/auth/callback.html` を登録してください。生成される `assets/config/auth-config.js` はGit管理外です。確認後もコミットせず、実値をREADMEやテストへ貼り付けないでください。

生成スクリプトは3変数の空値、URL形式、secret/service role形式、publishable keyでない値を拒否し、JavaScript文字列を安全にエスケープします。

### Supabase migrationの適用

このリポジトリはmigrationを自動適用しません。migrationは次の順序で適用します。

1. `supabase/migrations/20260804000000_create_member_profiles.sql`
2. `supabase/migrations/20260806000000_fix_accept_current_terms_conflict.sql`
3. `supabase/migrations/20260807000000_create_notification_rules.sql`
4. `supabase/migrations/20260807100000_add_notification_rule_save_rpc.sql`
5. `supabase/migrations/20260807110000_add_notification_rule_matching_rpc.sql`
6. `supabase/migrations/20260807120000_grant_notification_matching_rpc_dependencies.sql`
7. `supabase/migrations/20260807130000_limit_notification_rules_per_user.sql`
8. `supabase/migrations/20260821022637_add_configurable_notification_targets.sql`
9. `supabase/migrations/20260821034956_finalize_terms_version.sql`

適用済みmigrationを再実行・編集しないでください。対象環境のmigration履歴を確認し、未適用分だけを上記の順でそれぞれ1回適用します。適用前にSQL、RLS、Grant、初期データをレビューし、検証環境で実DBテストを行ってください。第7migrationは既に6件以上の通知条件を持つ利用者がいると、利用者IDやメールアドレスを表示せずに失敗します。第8migrationは対応外の旧曜日・時間値が残っている環境では匿名エラーで停止します。第9migrationはdraft規約がcurrentでない環境では停止し、正式版への再同意が完了するまでactive会員を `pending_terms` として通知対象外にします。適用前に利用者ごとの件数と条件値を個人情報を出さずに確認してください。

`list_notification_rules_for_matching()` は `security invoker` のため、呼び出し元の `service_role` にも参照先テーブルの通常のSELECT権限が必要です。RLS bypassはテーブルGRANTの代わりにはなりません。第6migrationは `profiles`、`notification_rules`、`notification_rule_facilities`、`notification_rule_weekdays` の4テーブルに限ってSELECTだけを付与し、書込み権限やブラウザロールの権限は追加しません。

第2migrationの実行後はTable Editorで変更せず、SQL Editorで次を確認してください。

1. `legal_document_versions` に `terms / 2026-08-04-draft / is_current=true` が1件ある。
2. 既存の `auth.users` ごとに `profiles` があり、`membership_status=pending_terms` である。
3. 既存ユーザー向けの `terms_acceptances` は自動生成されていない。
4. 新しい開発用ユーザーでマジックリンクを開き、同意後にprofileが `active` となり、同意履歴が1件追加される。
5. 同じ規約への再同意で履歴が重複しない。

第9migrationの実行後は、`2026-08-04-draft` が非current、`2026-08-21` がcurrentとなり、過去の同意履歴が残っていることを確認します。既存active会員は `pending_terms` となり、マイページで正式版へ同意すると新しい履歴が追加されて `active` へ戻ります。

SQL Editorへ貼り付ける前に、プロジェクト名と環境を再確認してください。service role key、secret key、DBパスワードはmigration適用手順では使用しません。

### 通知条件の照合

照合CLIは、公開しないservice-role keyで通知条件取得RPCを呼び、結果の詳細をファイルへ保存せず集計値だけを表示します。

```powershell
$env:SUPABASE_URL = "https://<project-ref>.supabase.co"
$env:SUPABASE_SERVICE_ROLE_KEY = "<service-role-key>"
python scripts/match_notification_rules.py --availability run-output/availability.json
```

出力は `rules_evaluated`、`slots_evaluated`、`matched_users`、`matched_slots`、`match_candidates` だけです。利用者ID、条件ID、メールアドレス、keyは出力しません。service-role keyはローカルの環境変数またはGitHub Actions Secretだけで管理し、ファイル、ブラウザ公開設定、ログ、Artifactへ保存しないでください。

### データ更新

```bash
python scripts/scrape.py
```

鴨池県営には追加のセレクタ設定は不要です。固定パラメータとして `category_id=483`、`area_id=289` を使用し、対象日ごとに `date=YYYY-MM-DD` を付加します。

SuMIzeiと東開は共通のP-Kashikan処理を使用します。公開トップURLと日別表示 `disp_span=0` は共通で、施設設定のコード（`029` / `131`）、施設名、対象日だけを変更します。P-Kashikanに限り、`ja-JP`、`Asia/Tokyo`、Desktop ChromeのUser-Agent、`Accept-Language`、1440×1000 viewport、JavaScript有効の通常ブラウザ設定を使用し、同一実行内ではブラウザセッションを再利用します。`navigator.webdriver`を隠すなどのアクセス制限回避は行いません。

P-KashikanがHTTP 403を返した場合は、その実行内の残りのP-Kashikanアクセスを中止します。SuMIzeiまたは東開の直前の正常データはavailabilityのfallbackとして保持し、鴨池県営の取得は継続します。

### P-Kashikan診断情報

各P-Kashikanナビゲーションでは、HTML・PNGに加えて `*-diagnostics.json` を `snapshots/<facility-id>/` に保存します。診断JSONとActionsログには、実行環境、最終URL、HTTPステータス、ページタイトル、response/request headers、User-Agent、`navigator.webdriver`、Cookie名、本文中のアクセス制限関連マーカーを記録します。

Cookie値、Authorization、APIキー、token・secretを含むヘッダー値は `<redacted>` とし、Cookieは名前だけを保存します。403本文では `Access denied`、`Forbidden`、Cloudflare、Akamai、Imperva、Incapsula、Bot、Request ID、IP restriction、rate limitを確認します。

## 通知経路

本番通知は、利用者の通知条件と `run-output/availability.json` を照合し、`matching -> enqueue -> dispatch` の順で利用者別メールを配信します。重複防止はDB側の利用者・チャネル・`slot_id` 単位で行います。各ステップは独立したRepository Variableで有効化し、enqueueはservice-role credential、dispatchはdelivery worker credentialだけを受け取ります。

Phase 3.5aでは、Resend送信payloadへ内部message UUIDのcorrelation tagを加え、署名済みwebhookの `sent`、`delivery_delayed`、`delivered`、`failed`、`bounced`、`complained`、`suppressed` をDBへ正規化しました。`svix-id`で重複を排除し、到着順ではなくeventの`created_at`と固定priorityで状態を決めます。productionではmigration、webhook、sender、通知canary、duplicate replayのno-opを確認し、2026-08-19に24時間超のaggregate観察も完了しました。異常status、retry滞留、bounce、complaint、suppressionがないことを確認済みです。raw webhook payload、宛先、sender、subjectは保存・ログ出力しません。

Phase 3.5bでは、利用者単位のprivate unsubscribe token、service-role専用RPC、Cloudflare Workerの公開confirmation/RFC 8058 endpoint、body-onlyのSupabase unsubscribe Function、メールfooterと`List-Unsubscribe` headers、Account UIを実装しました。Supabase hosted GETとInvocation Logsの実測を受けて直接Supabase URLを棄却し、`unsubscribe.tenniscourtwatcher.com`のCloudflare Workerを公開入口としました。production rolloutとacceptanceは2026-08-18に完了し、本文footerは認証済みAccount UI、RFC 8058 capabilityはheadersだけに分離しています。本人opt-outは`disabled_reason = NULL`のまま保持し、bounce・complaint・suppressionの理由は上書きもブラウザからの解除もしません。必須log boundary、maintenance guard、rollout順は[Phase 3 Email Unsubscribe Runbook](docs/PHASE3_EMAIL_UNSUBSCRIBE.md)を参照してください。

Phase 0から残っていた単一通知先のlegacy管理者LINE経路はPhase 3.4.3で退役しました。これはLINE通知全体の永久廃止ではありません。Phase 4では会員とLINEユーザーを安全に紐づけるDB基盤、LINE Login開始・callback・解除のserver-side境界、My Pageの安全な状態・操作UIを本番反映し、スマホ実機の正方向acceptanceも完了しました。利用者別LINE配信の署名検証webhook、channel分離queue、worker、retry、180通guardは初期OFFで実装済みですが、本番ではまだ有効化しません。

手動実行の `dry_run=true` では、取得とArtifact生成は行いますが、リポジトリ内データの更新、commit、push、Pagesデプロイ、email/LINE enqueue・dispatchは行いません。

### Actions Variables

| Variable | 用途 |
| --- | --- |
| `ENABLE_SCHEDULED_RUNS` | `true` のときだけcron実行を許可 |
| `ENABLE_NOTIFICATION_MATCHING` | `true` のときだけ通知条件照合を実行 |
| `ENABLE_USER_EMAIL_ENQUEUE` | `true` のときだけ利用者別メール候補をqueueへ登録 |
| `ENABLE_USER_EMAIL_DISPATCH` | `true` のときだけemail delivery workerを実行 |
| `ENABLE_USER_LINE_ENQUEUE` | `true` のときだけLINE候補のshadow評価またはqueue登録を実行 |
| `ENABLE_USER_LINE_DISPATCH` | `true` のときだけLINE delivery workerを呼び出す。shadow中は実行不可 |
| `LINE_NOTIFICATION_SHADOW_MODE` | 初期値`true`。候補を評価して集計だけ返し、queueへ書き込まない |
| `LINE_NOTIFICATION_ALLOW_ALL` | 単一会員canary後に明示的に`true`へするまで全会員enqueueを拒否 |
| `ENABLE_LINE_USAGE_REPORTS` | `true` のときだけLINE月間使用量の定期確認と報告を実行 |
| `LINE_USAGE_WARNING_THRESHOLD` | LINE Pushを止める月間運用上限。初期値は`180` |
| `SUPABASE_URL` | Supabase Project URL |
| `SUPABASE_PUBLISHABLE_KEY` | ブラウザ公開用のpublishable key |
| `AUTH_CALLBACK_URL` | Supabaseに許可登録した本番callback URL |

`SUPABASE_SERVICE_ROLE_KEY` はmatching/enqueue stepだけ、`EMAIL_DELIVERY_WORKER_SECRET` と `LINE_DELIVERY_WORKER_SECRET` は各dispatch stepだけへ渡します。単一canaryのSupabase Auth UUIDはGitHub SecretとSupabase Edge Function secretの両方の`LINE_NOTIFICATION_CANARY_USER_ID`に保存し、enqueue、worker claim、送信直前再検証の3境界で同じ対象を強制します。LINE Messaging APIのtokenはSupabase Edge Function secretに留め、通常のavailability workflowへ渡しません。各通知stepは `continue-on-error: true` で、失敗してもavailability Artifact・commit・Pages公開を妨げません。

LINE使用量報告は `.github/workflows/report-line-usage.yml` が毎日12:07 JSTに
公式APIの月間上限と使用済み通数を確認します。土曜は週次メールを送り、
180通到達時は当月1回だけ警告します。`LINE_CHANNEL_ACCESS_TOKEN`、
`LINE_USAGE_REPORT_RESEND_API_KEY`、`LINE_USAGE_REPORT_TO` はGitHub Secretsへ保存し、
repositoryやlogへ値を出しません。月間使用量にはLINE Official Account Managerからの
配信も含まれます。利用者別LINE workerも同じ使用量を送信前に確認し、
180通到達後はLINEを停止します。email channelは独立して通常どおり処理されます。

## GitHub ActionsとPages

cronは `7,37 0-14,22-23 * * *` です。UTCから換算すると、JST 07:07〜23:37の30分間隔です。ただし `ENABLE_SCHEDULED_RUNS=true` になるまで定期ジョブは実行されません。

LINE使用量確認のcronは `7 3 * * *`（毎日12:07 JST）です。
`ENABLE_LINE_USAGE_REPORTS=true`になるまで定期ジョブは実行されません。

Phase 3.4.4ではnative scheduleをprimaryのまま維持し、qualifying live runが45分間生成されない場合だけSupabase Edge Functionがfallback dispatchするwatchdogを追加しています。watchdog用Cronはmigrationでは作成せず、`off -> observe 24〜48h -> dispatch` の確認後に手動作成します。設計、secret、Cron SQLは[Phase 3 Scheduler Watchdog](docs/PHASE3_SCHEDULER_WATCHDOG.md)を参照してください。

Pages画面の「最終更新」は、`availability.json` 全体が生成された `generated_at` を示します。各施設の「最終確認」は、その施設の日別データにある最新の `checked_at` を示すため、施設間や最終更新との間に時刻差が生じることがあります。画面は最終更新から60分超で「更新が遅れています」、120分超で「2時間以上更新されていません」と警告します。取得エラーは別に表示し、取得できた日と空き候補は引き続き表示します。

1. 固定済み依存関係とChromiumをセットアップ
2. pytestを実行
3. `scripts/scrape.py` で全施設を取得し、availabilityとスナップショットを更新
4. `ENABLE_NOTIFICATION_MATCHING=true` の場合だけ、実行時JSONとservice-role専用RPCで通知条件を照合
5. 各有効化フラグとdry-run gateに従って利用者別メールをenqueue/dispatch
6. LINEは独立したフラグに従ってshadow評価またはenqueue/dispatchし、初期状態ではすべてOFF
7. スナップショット、実行時availability、`index.html`、Phase 1静的画面と共通assetsを `reservation-page-snapshots` Artifactとして常時保存。match詳細は含めない
8. dry-runでなければ意味のある `data/availability.json` の変更だけをコミット
9. 別ジョブがRepository Variablesから `_site/assets/config/auth-config.js` を生成
10. Pages専用権限で `index.html`、最新JSON、認証画面、法務画面、共通assetsをデプロイ

取得ジョブだけが `contents: write`、Pagesジョブだけが `pages: write` と `id-token: write` を持ちます。dry-runではcommitとPagesジョブを実行しません。一部施設の取得失敗は日別のエラーとしてJSONへ記録し、他施設の処理を継続します。初回実行前に、GitHubリポジトリの `Settings` → `Pages` でSourceを `GitHub Actions` に設定してください。

`concurrency` は全実行共通の `tennis-availability-writer`、`cancel-in-progress=false` です。availability writerは直列化されます。Actions以外から同時にpushされてpush競合が起きた場合は、最大3回までfetch/rebaseして再試行し、競合を解消できなければ上書きせず失敗します。Artifactはcommitより先に保存されるため、内容を確認してworkflowを再実行できます。

## 今後の作業

Launch Readiness Gateは完了し、Phase 4へ進みます。

1. 退会failure-pathのhardening — 完了（2026-08-20）
2. 利用規約・プライバシーポリシー・運営者/問い合わせ先の正式化 — 完了（2026-08-21）
3. 各予約サイトの利用規約・アクセス頻度の最終確認 — 完了（取得元の利用許可取得済み）
4. GitHub Actions外部ActionのコミットSHA固定 — 完了（PR #56）
5. Monitoring Policyと監視範囲外条件のUI表示 — 完了（PR #57）
6. 鹿児島βで使用する運用指標の決定 — 完了（Launch Readiness Review）
7. Phase 4の利用者別LINE通知 — account linkの本番・スマホacceptance完了。webhook・queue・workerは初期OFFで実装・隔離環境検証済み、次は本番へ段階導入

取得元の利用許可は取得済みである。アクセス負荷とβ運用指標は
[Launch Readiness Review](docs/LAUNCH_READINESS_REVIEW.md)を参照する。

## 注意事項

- 自動予約は実装していません。
- 会員DB、規約同意履歴、RLS、規約同意RPC、会員情報表示、退会処理、Phase 2の通知条件、Phase 3の利用者別メールqueue/worker、自動enqueue/dispatch、delivery feedback、unsubscribe / re-enable、90日retention cleanupは本番反映・production acceptanceまで完了しています。Phase 4はaccount linkとスマホ実機acceptanceまで完了し、webhook・利用者別LINE配信は初期OFFで実装・ローカル検証済みです。本番migration・Function deploy・段階有効化は未完了です。
- 短い間隔でのアクセスや過剰な並列実行は避けてください。
- 予約サイトの仕様変更により取得できなくなる可能性があります。
- `availability.json` とGitHub Pagesは公開情報として扱ってください。

## ライセンス

ライセンスは未設定です。再利用・配布条件を明確にする場合は、運用開始前に追加してください。
