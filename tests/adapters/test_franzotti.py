import json
from decimal import Decimal
from pathlib import Path

from monitor_cartas.adapters.franzotti import FranzottiAdapter
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
        active_sites=["franzotti"],
        data_dir=tmp_path / "data",
    )


def test_row_to_cota_parses_real_row(tmp_path):
    settings = _settings(tmp_path)
    adapter = FranzottiAdapter(settings)
    rows = json.loads((FIXTURES / "franzotti_rows.json").read_text())["rows"]

    cota = adapter._row_to_cota(rows[0])
    assert cota.source_site == "franzotti"
    assert cota.source_id == "4061"
    assert cota.administrator == "Itaú"
    assert cota.nominal_credit == Decimal("70000.00")
    assert cota.advertised_entry == Decimal("32750.00")
    assert cota.remaining_installments == 138
    assert cota.current_installment == Decimal("549.23")
    assert cota.status == QuotaStatus.AVAILABLE
    assert cota.source_url.endswith("/carta/pg2-imovel-13-4061/")
    assert cota.outstanding_balance is None


def test_row_to_cota_marks_unavailable(tmp_path):
    settings = _settings(tmp_path)
    adapter = FranzottiAdapter(settings)
    rows = json.loads((FIXTURES / "franzotti_rows.json").read_text())["rows"]

    cota = adapter._row_to_cota(rows[2])
    assert cota.status == QuotaStatus.UNAVAILABLE
