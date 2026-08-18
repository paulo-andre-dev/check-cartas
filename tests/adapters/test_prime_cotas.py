import json
from decimal import Decimal
from pathlib import Path

import pytest
import respx
from httpx import Response

from monitor_cartas.adapters.prime_cotas import REST_ENDPOINT, PrimeCotasAdapter
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
        active_sites=["prime_cotas"],
        data_dir=tmp_path / "data",
    )


@pytest.mark.asyncio
@respx.mock
async def test_collect_quota_parses_real_fixture(tmp_path):
    settings = _settings(tmp_path)
    adapter = PrimeCotasAdapter(settings)
    cards = json.loads((FIXTURES / "prime_cotas_cards.json").read_text())
    respx.get(REST_ENDPOINT).mock(return_value=Response(200, json=cards))

    urls = await adapter.collect_listing_urls()
    assert len(urls) == len(cards)

    cota = await adapter.collect_quota(urls[0])
    first = cards[0]
    assert cota.source_site == "prime_cotas"
    assert cota.source_id == first["id"]
    assert cota.quota == first["numero_cota"]
    assert cota.administrator == first["administradora"]
    assert cota.nominal_credit == Decimal(
        first["valor_credito"].replace("R$", "").replace("\xa0", "").replace(".", "").replace(",", ".").strip()
    )
    assert cota.remaining_installments == int(first["opcoes_parcelamento"][0]["parcelas"])
    assert cota.outstanding_balance is None
    assert cota.is_contemplated is True

    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_validate_access_ok(tmp_path):
    settings = _settings(tmp_path)
    adapter = PrimeCotasAdapter(settings)
    respx.get(REST_ENDPOINT).mock(return_value=Response(200, json=[]))
    result = await adapter.validate_access()
    assert result.ok is True
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_validate_access_detects_auth_error(tmp_path):
    settings = _settings(tmp_path)
    adapter = PrimeCotasAdapter(settings)
    respx.get(REST_ENDPOINT).mock(
        return_value=Response(401, json={"message": "No API key found in request"})
    )
    result = await adapter.validate_access()
    assert result.ok is False
    await adapter.aclose()
