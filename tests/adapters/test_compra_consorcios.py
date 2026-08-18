import json
from decimal import Decimal
from pathlib import Path

from monitor_cartas.adapters.compra_consorcios import CompraConsorciosAdapter
from monitor_cartas.core.statuses import QuotaStatus
from monitor_cartas.settings import (
    CombinationConfig,
    ConsistencyConfig,
    FinancialConfig,
    MonitoringConfig,
    Settings,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _settings(tmp_path) -> Settings:
    financial = FinancialConfig(
        target_credit_min=Decimal("300000"),
        target_credit_max=Decimal("400000"),
        credit_basis="liquid",
        fallback_to_nominal_credit=True,
        max_entry_percentage=Decimal("0.15"),
        gold_entry_percentage=Decimal("0.10"),
        good_entry_percentage=Decimal("0.30"),
        max_monthly_payment=Decimal("6000"),
        combination=CombinationConfig(),
        consistency=ConsistencyConfig(),
    )
    return Settings(
        financial=financial,
        monitoring=MonitoringConfig(),
        active_sites=["compra_consorcios"],
        data_dir=tmp_path / "data",
    )


def test_row_to_cota_available(tmp_path):
    settings = _settings(tmp_path)
    adapter = CompraConsorciosAdapter(settings)
    rows = json.loads((FIXTURES / "compra_consorcios_rows.json").read_text())

    cota = adapter._row_to_cota(rows[0])
    assert cota.source_id == "8195"
    assert cota.nominal_credit == Decimal("305700.00")
    assert cota.advertised_entry == Decimal("145000.00")
    assert cota.current_installment == Decimal("2010.00")
    assert cota.status == QuotaStatus.AVAILABLE
    assert cota.is_contemplated is True
    assert cota.source_url.endswith("_post_id=8195")


def test_row_to_cota_reserved_is_unavailable(tmp_path):
    settings = _settings(tmp_path)
    adapter = CompraConsorciosAdapter(settings)
    rows = json.loads((FIXTURES / "compra_consorcios_rows.json").read_text())

    cota = adapter._row_to_cota(rows[1])
    assert cota.status == QuotaStatus.UNAVAILABLE
