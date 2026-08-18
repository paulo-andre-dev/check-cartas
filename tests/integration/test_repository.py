from datetime import datetime, timezone
from decimal import Decimal

from monitor_cartas.core.statuses import QuotaStatus
from monitor_cartas.repositories.sqlite import QuotaRepository
from tests.conftest import make_cota


def test_second_upsert_does_not_duplicate(tmp_path):
    repo = QuotaRepository(tmp_path / "cotas.db")
    cota = make_cota()

    repo.upsert_quota(cota)
    repo.upsert_quota(make_cota())  # segunda "execução" vendo o mesmo anúncio

    rows = repo.conn.execute("SELECT COUNT(*) c FROM quotas").fetchone()
    assert rows["c"] == 1
    repo.close()


def test_first_upsert_marks_new_then_seen(tmp_path):
    repo = QuotaRepository(tmp_path / "cotas.db")
    first = repo.upsert_quota(make_cota())
    assert first.status == QuotaStatus.NEW

    second = repo.upsert_quota(make_cota())
    assert second.status == QuotaStatus.SEEN
    repo.close()


def test_price_change_creates_history(tmp_path):
    repo = QuotaRepository(tmp_path / "cotas.db")
    repo.upsert_quota(make_cota(advertised_entry=Decimal("30000")))
    repo.upsert_quota(make_cota(advertised_entry=Decimal("28000")))

    history = repo.conn.execute(
        "SELECT * FROM quota_price_history WHERE field='advertised_entry'"
    ).fetchall()
    assert len(history) == 1
    assert history[0]["old_value"] == "30000"
    assert history[0]["new_value"] == "28000"
    repo.close()


def test_silence_persists_and_blocks_reconsideration(tmp_path):
    repo = QuotaRepository(tmp_path / "cotas.db")
    repo.upsert_quota(make_cota())

    ok = repo.silence("contemplei", "123", "não faz mais sentido", datetime.now(timezone.utc))
    assert ok is True
    assert repo.is_silenced("contemplei", "123") is True

    # scraper "encontra" a cota de novo — continua silenciada até reativação manual
    assert repo.is_silenced("contemplei", "123") is True

    reactivated = repo.reactivate("contemplei", "123", datetime.now(timezone.utc))
    assert reactivated is True
    assert repo.is_silenced("contemplei", "123") is False
    repo.close()


def test_mark_missing_removes_after_threshold(tmp_path):
    repo = QuotaRepository(tmp_path / "cotas.db")
    repo.upsert_quota(make_cota())

    now = datetime.now(timezone.utc)
    repo.mark_missing("contemplei", "123", threshold=3, when=now)
    repo.mark_missing("contemplei", "123", threshold=3, when=now)
    assert repo.get_quota("contemplei", "123").status != QuotaStatus.REMOVED

    repo.mark_missing("contemplei", "123", threshold=3, when=now)
    assert repo.get_quota("contemplei", "123").status == QuotaStatus.REMOVED
    repo.close()


def test_mark_missing_resets_when_seen_again(tmp_path):
    repo = QuotaRepository(tmp_path / "cotas.db")
    repo.upsert_quota(make_cota())
    now = datetime.now(timezone.utc)
    repo.mark_missing("contemplei", "123", threshold=3, when=now)

    repo.upsert_quota(make_cota())  # visto de novo
    row = repo.conn.execute(
        "SELECT missing_runs FROM quotas WHERE source_site='contemplei' AND source_id='123'"
    ).fetchone()
    assert row["missing_runs"] == 0
    repo.close()
