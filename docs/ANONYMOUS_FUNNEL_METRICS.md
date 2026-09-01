# 匿名登録ファネル

## 目的

β運用で、`LINEではじめる` の前後のどこで利用が止まっているかを概算する。個人の行動を追跡する分析機能ではない。

## 集計地点

| event_name | 数える時点 | 読み取り方 |
| --- | --- | --- |
| `login_page_view` | 未ログインでログイン画面が表示された | 登録導線へ到達したブラウザ数の概算 |
| `line_start_click` | `LINEではじめる` を押した | LINE認証を始めようとしたブラウザ数の概算 |
| `terms_prompt_view` | LINE認証後に利用規約確認が表示された | LINE認証を通過して規約確認へ到達したブラウザ数の概算 |

同じブラウザ・同じ日本時間の日・同じ地点は1回まで数える。ブラウザの保存データを削除した場合、保存機能が使えない場合、複数端末を使った場合は重複し得る。反対に、同じ端末を共有する複数人は1件になることがある。公開入口は送信元を制限するが、匿名公開機能なので機械的な送信を完全には排除できない。したがって、人数の厳密値ではなく導線間の大きな差を見る。

## プライバシー境界

アプリの集計テーブルには日付、地点名、件数、作成・更新日時だけを保存する。会員ID、LINEユーザーID、メールアドレス、IPアドレス、User-Agent、参照元URL、セッションIDまたは端末識別子は保存しない。ブラウザには、当日その地点を記録済みかを示す非個人情報の印だけを保存する。集計は最大400日で削除する。

Supabaseなど委託先が安全管理や障害対応のために生成する通常のアクセス記録は、アプリの集計テーブルとは別であり、プライバシーポリシーに従う。

## 日別確認

Supabase DashboardのSQL Editorで次を実行する。結果に利用者情報は含まれない。

```sql
select
  event_date,
  coalesce(sum(event_count) filter (
    where event_name = 'login_page_view'
  ), 0) as login_page_views,
  coalesce(sum(event_count) filter (
    where event_name = 'line_start_click'
  ), 0) as line_start_clicks,
  coalesce(sum(event_count) filter (
    where event_name = 'terms_prompt_view'
  ), 0) as terms_prompt_views
from private.anonymous_funnel_daily_counts
group by event_date
order by event_date desc;
```

## 判断の目安

- `login_page_view` に比べて `line_start_click` が少ない: 登録する価値の説明、ボタン周辺、利用規約・プライバシーへの警戒、LINE連携への抵抗を確認する。
- `line_start_click` に比べて `terms_prompt_view` が少ない: LINE認証画面、権限説明、友だち追加、認証エラーを確認する。
- `terms_prompt_view` は多いが規約同意が増えない: 規約確認画面の説明量、安心材料、同意操作を確認する。

規約同意件数は既存の同意履歴を日本時間の日別で別集計する。匿名の表示件数と会員単位の同意件数は定義が異なるため、厳密な転換率とはせず傾向として比較する。
