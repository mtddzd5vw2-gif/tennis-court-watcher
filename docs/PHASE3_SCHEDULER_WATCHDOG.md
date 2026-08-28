# Phase 3.4.4 Scheduler Reliability Watchdog

## 目的と責務

GitHub Actions の native schedule を primary scheduler として維持し、`main` 上の qualifying live run が45分間生成されていない場合だけ、Supabase Cron から Edge Function を起動して `update-availability.yml` を `workflow_dispatch(ref=main, dry_run=false)` で1回 fallback 起動する。

この watchdog が扱うのは scheduler liveness だけである。workflow の成功・失敗を回復する仕組みではない。`success`、`failure`、`cancelled`、`timed_out` など conclusion に関係なく、qualifying run の `created_at` が新しければ healthy とする。

## Liveness 判定

対象は `.github/workflows/update-availability.yml` の `main` branch run に限定する。

Liveness に数える run は次のいずれかである。

- `event=schedule`
- `event=workflow_dispatch` かつ run-name に `[manual-live]` がある
- 前回 watchdog dispatch の response から取得・保存できた workflow run ID と一致する

`manual-dry-run` と feature branch run は liveness に数えない。一方、`main` の `queued`、`in_progress`、`requested`、`waiting`、`pending` は dry-run を含め、作成から45分未満の間だけ active run としてfallbackとの競合を止める。45分以上古いactive表示は、GitHub APIに残留したghost runがwatchdogを恒久停止させないようactive判定から除外する。qualifying live runであれば古い`created_at`自体は保持されるため、snapshotは`stale`となり、通常の二重観測・claim・cooldownを経てfallback対象になる。

GitHub API は `branch=main&exclude_pull_requests=true&per_page=100` で新しい順に取得し、45分 window の末端までページングする。qualifying live runが1件もない状態、response 不正、未知status、許容2分を超える未来の `created_at`、順序不正、重複、ページ間の `total_count` 変化、最大10ページで window を覆えない状態、GET failure はすべて `unknown` として fail closed にする。GET だけは1回の短い retry/backoff を許可し、workflow dispatch POST は retry しない。

## DB と排他制御

`public.update_availability_watchdog_state` は `watchdog_name='update-availability'` の singleton である。RLS を有効にし、通常 policy は作らず、table への直接権限を `anon`、`authenticated`、`service_role` から revoke する。操作は `security definer`、`set search_path = ''` の次の RPC に限定し、execute は `service_role` だけに grant する。

- `record_update_availability_watchdog_snapshot`: normalized snapshot と観測 counter を記録する
- `claim_update_availability_fallback`: qualifying live runの存在、2分以内のsnapshot、stale、service window、lease、cooldown を単一 conditional `UPDATE` で検証し、約5分の claim を取得する
- `confirm_update_availability_fallback`: second observation token、qualifying live runの存在、2分以内のsnapshot、stale、active、service window を再検証し、成功時に初めて30分 cooldown と dispatch attempt を記録する
- `finish_update_availability_fallback`: exact claim token に対する pre-POST abort、accepted、known failure、unknown を記録して claim を解放する

Claim と cooldown は分離している。claim 後かつ confirm 前に Function が止まった場合は約5分で lease が失効し、30分 cooldown は開始しない。confirm 後は GitHub POST が timeout、5xx、response unknown でも cooldown を維持する。

`latest_live_success_at`、`latest_live_failure_at`、`consecutive_failure_count` は観測用であり、fallback 条件には使わない。

## Edge Function の判定フロー

1. POST、Origin なし、32文字以上の `SCHEDULER_WATCHDOG_SECRET` を検証する。
2. `WATCHDOG_MODE=off` なら外部アクセスなしで終了する。
3. Edge 側で `Asia/Tokyo` の `[07:20, 00:30)` を検証する。
4. GitHub snapshot #1 を完全取得して DB に記録する。unknown、fresh、active は dispatch しない。
5. `dispatch` mode かつ stale の場合だけ atomic claim を取得する。
6. GitHub snapshot #2 を独立して取得・記録する。unknown、fresh、active なら claim を解放し、dispatch しない。
7. DB confirm で second observation token と service window を再検証し、30分 cooldown を開始する。
8. GitHub workflow dispatch POST を1回だけ行い、結果を normalized state として記録する。

Function 内部 deadline は17.8秒、Cron 側 HTTP timeout は20秒である。ログと response は mode、outcome、snapshot 数、POST 数、所要時間の aggregate だけを含み、PAT、secret、raw GitHub response、PII は出力しない。

## Production rollout

1. `supabase/migrations/20260813000000_add_update_availability_watchdog.sql` を production DB に適用する。
2. `update-availability-watchdog` を `--no-verify-jwt` で deploy する。独自 Bearer secret を Function 内で検証するためである。
3. Edge Function secrets を設定し、`WATCHDOG_MODE=off` で認証、POST-only、Origin 拒否を確認する。
4. `WATCHDOG_MODE=observe` に変更し、手動 POST で snapshot と DB state を確認する。
5. 下記 Cron を手動作成し、observe のまま24～48時間観測する。GitHub dispatch POST は発生しない。
6. false stale、API unknown、active-run 判定、counter、実行時間に問題がないことを確認して `WATCHDOG_MODE=dispatch` にする。
7. 初回 fallback 後は Actions run、`dispatch_*_count`、cooldown、`last_outcome` を確認する。

Mode を戻すときは `dispatch -> observe -> off` の順で secrets の `WATCHDOG_MODE` を変更する。native GitHub cron と既存 workflow concurrency は変更しない。

## Production secrets

Edge Function の server-side secret:

- `SCHEDULER_WATCHDOG_SECRET`: Cron からの Bearer 認証専用。32文字以上のランダム値
- `GITHUB_ACTIONS_DISPATCH_TOKEN`: `tennis-court-watcher` repository のみに限定した fine-grained PAT。Repository permission は Actions: write
- `WATCHDOG_MODE`: 初期値 `off`、確認後 `observe`、最終的に `dispatch`
- `SUPABASE_URL`: hosted Edge Function の組み込み値
- `SUPABASE_SERVICE_ROLE_KEY`: hosted Edge Function の組み込み値。client/frontend へ公開しない

Cron から参照する Vault secret:

- `scheduler_watchdog_project_url`: `https://<PROJECT_REF>.supabase.co`
- `scheduler_watchdog_secret`: Edge Function の `SCHEDULER_WATCHDOG_SECRET` と同じ値

PAT と service-role key を Cron SQL、GitHub Actions、Pages artifact、browser bundle に入れない。

## Cron 作成 SQL

Cron job 自体は migration に含めない。Function、secret、observe mode を確認した後、production の SQL Editor で手動実行する。

まず Dashboard の Vault 画面、または次の SQL で値を登録する。プレースホルダーを実値に置き換え、SQL 履歴と画面共有の扱いに注意する。

```sql
select vault.create_secret(
  'https://<PROJECT_REF>.supabase.co',
  'scheduler_watchdog_project_url'
);

select vault.create_secret(
  '<SCHEDULER_WATCHDOG_SECRET>',
  'scheduler_watchdog_secret'
);
```

既存の同名 job があれば明示的に解除してから作成する。

```sql
select cron.unschedule(jobid)
from cron.job
where jobname = 'update-availability-watchdog';

select cron.schedule(
  'update-availability-watchdog',
  '2,12,22,32,42,52 0-15,22-23 * * *',
  $$
  select net.http_post(
    url := (
      select decrypted_secret
      from vault.decrypted_secrets
      where name = 'scheduler_watchdog_project_url'
    ) || '/functions/v1/update-availability-watchdog',
    headers := pg_catalog.jsonb_build_object(
      'content-type', 'application/json',
      'authorization', 'Bearer ' || (
        select decrypted_secret
        from vault.decrypted_secrets
        where name = 'scheduler_watchdog_secret'
      )
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 20000
  );
  $$
);
```

UTC cron は `:02/:12/:22/:32/:42/:52` の `0-15,22-23` 時である。JST 07:02、07:12 と00:32以降は Function が skip する。最初の eligible tick は07:22、最後は00:22である。

## 運用確認 SQL

```sql
select
  watchdog_name,
  last_snapshot_at,
  snapshot_outcome,
  active_run_count,
  latest_live_run_created_at,
  claim_expires_at,
  dispatch_cooldown_until,
  last_outcome,
  check_count,
  github_api_error_count,
  dispatch_attempt_count,
  dispatch_accepted_count,
  updated_at
from public.update_availability_watchdog_state
where watchdog_name = 'update-availability';

select jobid, jobname, schedule, active
from cron.job
where jobname = 'update-availability-watchdog';
```

## 2026-08-28 production recovery acceptance

GitHub native scheduleが複数枠でrunを生成しなかった際、2026-08-26のworkflow_dispatch run
`32984358881`がGitHub API上で古い`queued` statusのまま残留し、watchdogが全tickを
`active_run_present`としてfallbackを抑止していた。active判定を作成から45分未満に限定した
Edge Function version 37をdeployし、12:42 JSTの実Cronで`fresh`、`active_run_count=0`、
dispatch 0を確認した。

その後も13:07 JSTのnative runが生成されなかったため、13:12 JSTの実Cronが2回のstale
snapshot、active 0を確認し、fallback POSTを1回だけ実行した。responseはHTTP 200、
`dispatch_accepted`、workflow run ID `33141122234`であり、run本体とPages deployは成功した。
watchdog counterはattempt 171、accepted 170へ各1増加し、30分cooldownが設定された。
LINE/email候補・enqueue・dispatchは0、LINE retry・失敗・allowlist外queueは0、LINE使用量は
21/180のままだった。

## 残る運用リスク

- GitHub Actions の native schedule 自体が45分以上遅延すると fallback が起動する。これは watchdog の目的どおりだが、GitHub 側で run が同時生成された場合は second snapshot と既存 concurrency が最終的な競合抑止になる。
- GitHub APIがworkflow timeout（20分）を超えてactive statusを残す場合でも、作成から45分未満だけ競合抑止に使う。45分以上残留したghost runはfallbackを止めない。
- GitHub API 障害、rate limit、不正 response、45分 window を10ページで覆えない高頻度状態では fail closed になるため、fallback availability より重複防止を優先する。
- GitHub API version `2026-03-10` の workflow dispatch は、HTTP 200のJSON objectにpositive safe integerの `workflow_run_id` がある場合だけacceptedとして扱う。body不正、ID欠落・不正、204を含むunexpected 2xx、timeout、5xxは結果不明として30分cooldownを維持する。
- PAT の失効・権限変更は dispatch failure になる。expiry と rotation を別途運用監視する。
- pg_cron、pg_net、Vault の有効化状況と `net._http_response` は production project ごとに確認する。
