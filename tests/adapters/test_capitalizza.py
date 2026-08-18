from decimal import Decimal
from pathlib import Path

import pytest
import respx
from httpx import Response

from monitor_cartas.adapters.capitalizza import LISTING_URL, CapitalizzaAdapter, parse_listing
from monitor_cartas.settings import CombinationConfig, ConsistencyConfig, FinancialConfig, MonitoringConfig, Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _settings(tmp_path):
    return Settings(
        financial=FinancialConfig(
            target_credit_min=Decimal("300000"), target_credit_max=Decimal("400000"),
            credit_basis="liquid", fallback_to_nominal_credit=True,
            max_entry_percentage=Decimal(".15"), gold_entry_percentage=Decimal(".10"),
            good_entry_percentage=Decimal(".30"), max_monthly_payment=Decimal("6000"),
            combination=CombinationConfig(), consistency=ConsistencyConfig(),
        ),
        monitoring=MonitoringConfig(), active_sites=["capitalizza"], data_dir=tmp_path / "data",
    )


def test_parse_listing_reads_only_structured_rows():
    rows = parse_listing((FIXTURES / "capitalizza_sample.html").read_text())
    assert len(rows) == 2
    assert rows[0]["id"] == "578"
    assert rows[0]["outstanding_balance"] == "R$ 352.905,84"


@pytest.mark.asyncio
@respx.mock
async def test_adapter_filters_reserved_and_maps_financial_fields(tmp_path):
    html = (FIXTURES / "capitalizza_sample.html").read_text()
    respx.get(LISTING_URL).mock(return_value=Response(200, text=html))
    adapter = CapitalizzaAdapter(_settings(tmp_path))
    urls = await adapter.collect_listing_urls()
    assert urls == ["capitalizza-item://578"]
    cota = await adapter.collect_quota(urls[0])
    assert cota.nominal_credit == Decimal("292088.02")
    assert cota.advertised_entry == Decimal("131500.00")
    assert cota.outstanding_balance == Decimal("352905.84")
    assert cota.current_installment == Decimal("2400.72")
    assert cota.remaining_installments == 147
    await adapter.aclose()
