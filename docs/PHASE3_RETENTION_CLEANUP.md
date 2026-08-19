# Phase 3.5c Email Notification Retention Cleanup

## 1. 目的

利用者別メール通知の内部履歴を無期限に保持せず、初期保持期間を90日とする。対象は
`notification_messages`、`notification_message_items`、
`notification_provider_events`、`notification_delivery_items`である。

cleanupは利用者向け機能や送信経路を変更しない。メールアドレス、利用者ID、message ID、
provider ID、slot IDをログや戻り値へ出さず、aggregate countだけを返す。

## 2. 削除契約

`public.cleanup_email_notification_history(batch_size integer default 1000)`を
service-role専用の`security definer` RPCとして追加する。1回のbatch上限は1000件である。

messageは次をすべて満たす場合だけ削除する。

- `created_at`が実行時刻から90 daysより前。
- `updated_at`も90 daysより前。
- statusが`accepted`、`delivered`、`failed_permanent`、`bounced`、
  `complained`、`suppressed`、`cancelled`のいずれか。
- 90 days以内に到着した`notification_provider_events.created_at`が存在しない。

`pending`、`processing`、`retry_wait`は古さに関係なく削除しない。message削除時は既存FKの
`ON DELETE CASCADE`によりmessage itemとprovider eventを同一transaction内で削除する。

`notification_delivery_items`は重複通知防止のauthorityであるため最後に扱う。次をすべて満たす
場合だけ削除する。

- `created_at`が90 daysより前。
- `available_date`がAsia/Tokyo基準の現在日より前。
- `notification_message_items`から参照されていない。

これにより、現在または将来の空き枠、送信中message、最近のprovider feedback、参照中のdedupe
recordをcleanupで失わない。

## 3. concurrencyと権限

RPCはtransaction advisory lockをtry取得し、別cleanupが動作中なら`outcome=busy`とaggregate
zero countを返す。候補取得には`FOR UPDATE SKIP LOCKED`を使用する。

`PUBLIC`、`anon`、`authenticated`からEXECUTEをrevokeし、`service_role`だけへgrantする。
pg_cronはproduction SQL Editorから作成したjob ownerで実行する。

戻り値は次のaggregateだけである。

- `outcome`
- `deleted_message_count`
- `deleted_message_item_count`
- `deleted_provider_event_count`
- `deleted_delivery_item_count`

## 4. Production rollout

2026-08-19にproduction rolloutを完了した。migration適用後のcandidate countはmessage、
delivery itemとも0で、manual cleanupも全削除件数0で成功した。
`email-notification-retention-cleanup` cronを03:17 JSTの日次実行として作成し、
2026-08-19の初回実行成功と対象table件数・candidate countに異常がないことを確認した。

rolloutは次の順序で行う。

1. localで`python -m pytest`と`supabase test db`を成功させる。
2. productionへmigrationを適用する。
3. cron作成前に下記preflight SQLで対象件数が0であることを再確認する。
4. RPCを手動で1回実行し、aggregateがzeroであることを確認する。
5. cronを作成する。
6. 翌日、cron実行履歴と対象table件数をaggregateで確認する。

### Preflight SQL

```sql
with bounds as (
  select
    pg_catalog.statement_timestamp() - interval '90 days' as cutoff,
    (
      pg_catalog.statement_timestamp() at time zone 'Asia/Tokyo'
    )::date as today
)
select
  (
    select pg_catalog.count(*)
    from public.notification_messages as message
    cross join bounds
    where message.created_at < bounds.cutoff
      and message.updated_at < bounds.cutoff
      and message.status in (
        'accepted',
        'delivered',
        'failed_permanent',
        'bounced',
        'complained',
        'suppressed',
        'cancelled'
      )
      and not exists (
        select 1
        from public.notification_provider_events as provider_event
        where provider_event.message_id = message.id
          and provider_event.created_at >= bounds.cutoff
      )
  ) as message_candidate_count,
  (
    select pg_catalog.count(*)
    from public.notification_delivery_items as delivery_item
    cross join bounds
    where delivery_item.created_at < bounds.cutoff
      and delivery_item.available_date < bounds.today
      and not exists (
        select 1
        from public.notification_message_items as message_item
        where message_item.delivery_item_id = delivery_item.id
      )
  ) as orphan_delivery_candidate_count;
```

### Manual execution

```sql
select public.cleanup_email_notification_history(1000);
```

## 5. Cron

cron job itself is not part of the migration。productionでmigrationとmanual zero-deleteを確認してから
手動作成する。

通常のavailability取得・watchdog windowを避け、毎日03:17 JSTに1回実行する。pg_cronはUTCなので
scheduleは`17 18 * * *`である。

```sql
select cron.unschedule(jobid)
from cron.job
where jobname = 'email-notification-retention-cleanup';

select cron.schedule(
  'email-notification-retention-cleanup',
  '17 18 * * *',
  $$
  select public.cleanup_email_notification_history(1000);
  $$
);
```

確認:

```sql
select jobid, jobname, schedule, active
from cron.job
where jobname = 'email-notification-retention-cleanup';
```

## 6. 異常時

cleanupはnotification senderとは独立しているため、異常時にメール送信feature flagを変更しない。
まずcron jobをunscheduleまたはinactive化し、対象件数とfunction errorを確認する。

cleanup済みの90日超履歴を復元する運用は前提としない。そのため、retention条件・cascade・dedupe
条件に変更が必要な場合は、既存migrationを編集せずforward migrationで修正する。
