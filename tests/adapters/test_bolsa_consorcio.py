import json
from decimal import Decimal
from pathlib import Path

from monitor_cartas.adapters.bolsa_consorcio import BolsaConsorcioAdapter, _parse_parcelas, _slug_id
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
        active_sites=["bolsa_consorcio"],
        data_dir=tmp_path / "data",
    )


def test_parse_parcelas_handles_both_formats():
    assert _parse_parcelas("73 x R$ 340.00") == (73, Decimal("340.00"))
    assert _parse_parcelas("167 X 433") == (167, Decimal("433"))
    assert _parse_parcelas(None) == (None, None)


def test_slug_id():
    assert _slug_id("https://x.com/consorcio/minha-cota-123/") == "minha-cota-123"


def test_row_with_missing_saldo_devedor(tmp_path):
    settings = _settings(tmp_path)
    adapter = BolsaConsorcioAdapter(settings)
    rows = json.loads((FIXTURES / "bolsa_consorcio_rows.json").read_text())

    cota = adapter._row_to_cota("cota-1", rows[0])
    assert cota.outstanding_balance is None  # "---" vira None, nunca zero
    assert cota.nominal_credit == Decimal("23000.00")
    assert cota.remaining_installments == 73
    assert cota.status == QuotaStatus.AVAILABLE


def test_row_with_saldo_devedor_and_reserved_status(tmp_path):
    settings = _settings(tmp_path)
    adapter = BolsaConsorcioAdapter(settings)
    rows = json.loads((FIXTURES / "bolsa_consorcio_rows.json").read_text())

    cota = adapter._row_to_cota("cota-2", rows[1])
    assert cota.outstanding_balance == Decimal("72311.00")
    assert cota.status == QuotaStatus.RESERVED
