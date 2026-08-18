import json
from decimal import Decimal
from pathlib import Path

import pytest
import respx
from httpx import Response

from monitor_cartas.adapters.tramontana import API_ENDPOINT, TramontanaAdapter
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
        active_sites=["tramontana"],
        data_dir=tmp_path / "data",
    )


@pytest.mark.asyncio
@respx.mock
async def test_filters_only_available_items_any_category(tmp_path):
    settings = _settings(tmp_path)
    adapter = TramontanaAdapter(settings)
    items = json.loads((FIXTURES / "tramontana_sample.json").read_text())
    respx.get(API_ENDPOINT).mock(return_value=Response(200, json=items))

    urls = await adapter.collect_listing_urls()
    expected = [i for i in items if i["situacao-da-carta"] == "Disponível"]
    assert len(urls) == len(expected)
    categorias = {i["categoria"] for i in items if i["situacao-da-carta"] == "Disponível"}
    assert "Veículo" in categorias or "Imóvel" in categorias  # ambas passam pelo filtro
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_collect_quota_parses_installments_and_fees(tmp_path):
    settings = _settings(tmp_path)
    adapter = TramontanaAdapter(settings)
    items = json.loads((FIXTURES / "tramontana_sample.json").read_text())
    respx.get(API_ENDPOINT).mock(return_value=Response(200, json=items))

    urls = await adapter.collect_listing_urls()
    cota = await adapter.collect_quota(urls[0])

    expected = next(i for i in items if i["situacao-da-carta"] == "Disponível")
    qty_str, _, value_str = expected["parcelas"].partition("x")
    assert cota.remaining_installments == int(qty_str)
    assert cota.current_installment == Decimal(value_str.replace(".", "").replace(",", "."))
    assert cota.transfer_fee is not None
    assert cota.outstanding_balance is None
    assert cota.administrator == expected["observacoes"]

    await adapter.aclose()
