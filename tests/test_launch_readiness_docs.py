from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_launch_readiness_records_source_permission_and_monitoring_scope() -> None:
    review = (ROOT / "docs/LAUNCH_READINESS_REVIEW.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "docs/DEVELOPMENT_ROADMAP.md").read_text(encoding="utf-8")

    assert "https://k2.p-kashikan.jp/kagoshima-city/index.php?op=tos" in review
    assert "https://v2.spm-cloud.com/user/kamoike-undo/contents/terms.html" in review
    assert "利用許可を取得済み" in review
    assert "ENABLE_SCHEDULED_RUNS=true" in review
    assert "1日34回" in review
    assert "1,530施設日" in review
    assert "取得元から公開空き情報の自動確認・表示・通知について利用許可を取得済み" in roadmap


def test_phase4_design_keeps_email_auth_and_same_line_provider() -> None:
    design = (ROOT / "docs/PHASE4_LINE_NOTIFICATION_DESIGN.md").read_text(
        encoding="utf-8"
    )
    specification = (ROOT / "docs/SERVICE_SPECIFICATION.md").read_text(
        encoding="utf-8"
    )

    assert "Supabase Authメールマジックリンクを会員認証の正" in design
    assert "LINE Login v2.1" in design
    assert "同じLINE provider" in design
    assert "x-line-signature" in design
    assert "1会員1LINE account、1LINE account 1会員" in specification


def test_beta_metrics_cover_quality_latency_and_privacy() -> None:
    review = (ROOT / "docs/LAUNCH_READINESS_REVIEW.md").read_text(encoding="utf-8")

    for expected in (
        "freshness",
        "取得品質",
        "通知速度",
        "重複、誤配信、別利用者情報露出",
        "利用状況",
    ):
        assert expected in review
