from datetime import datetime, timezone

import pytest

from monitor_cartas.core.models import AccessResult, AdapterRunResult
from monitor_cartas.repositories.sqlite import QuotaRepository
from monitor_cartas.services import pipeline
from monitor_cartas.settings import MonitoringConfig, Settings


class _CrashingAdapter:
    name = "crashing"

    def __init__(self):
        self.closed = False

    async def run(self):
        raise RuntimeError("quebra controlada")

    async def aclose(self):
        self.closed = True


class _HealthyAdapter:
    name = "healthy"

    def __init__(self):
        self.closed = False

    async def run(self):
        now = datetime.now(timezone.utc)
        return AdapterRunResult(
            site=self.name,
            started_at=now,
            finished_at=now,
            access=AccessResult(ok=True, checked_at=now),
        )

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_pipeline_isolates_adapter_crash_and_marks_run_partial(
    tmp_path, financial_config, monkeypatch
):
    bad = _CrashingAdapter()
    good = _HealthyAdapter()
    monkeypatch.setattr(pipeline, "build_adapters", lambda *_: [bad, good])
    settings = Settings(
        financial=financial_config,
        monitoring=MonitoringConfig(),
        active_sites=[bad.name, good.name],
        data_dir=tmp_path / "data",
    )

    results = await pipeline.run_pipeline(settings, settings.active_sites, trigger="test")

    assert [result.site for result in results] == [bad.name, good.name]
    assert results[0].access.ok is False
    assert results[1].access.ok is True
    assert bad.closed and good.closed

    repo = QuotaRepository(settings.db_path)
    row = repo.conn.execute("SELECT status, success FROM scraper_runs").fetchone()
    assert row["status"] == "PARTIAL"
    assert row["success"] == 0
    repo.close()
