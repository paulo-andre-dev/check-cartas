import json
from decimal import Decimal
from pathlib import Path

import pytest
import respx
from httpx import Response

from monitor_cartas.adapters.bidcon import VITRINE_ENDPOINT, BidconAdapter
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
        active_sites=["bidcon"],
        data_dir=tmp_path / "data",
    )


@pytest.mark.asyncio
@respx.mock
async def test_collect_listing_urls_includes_imoveis_and_veiculos(tmp_path):
    settings = _settings(tmp_path)
    adapter = BidconAdapter(settings)
    payload = json.loads((FIXTURES / "bidcon_vitrine.json").read_text())
    respx.get(VITRINE_ENDPOINT).mock(return_value=Response(200, json=payload))

    urls = await adapter.collect_listing_urls()
    assert len(urls) == len(payload["cotas"])
    assert all(u.startswith("bidcon-vitrine://") for u in urls)
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_collect_quota_parses_real_fixture(tmp_path):
    settings = _settings(tmp_path)
    adapter = BidconAdapter(settings)
    payload = json.loads((FIXTURES / "bidcon_vitrine.json").read_text())
    respx.get(VITRINE_ENDPOINT).mock(return_value=Response(200, json=payload))

    urls = await adapter.collect_listing_urls()
    cota = await adapter.collect_quota(urls[0])

    first_item = payload["cotas"][0]
    assert cota.source_site == "bidcon"
    assert cota.source_id == first_item["id"]
    assert cota.administrator == first_item["adm"]
    assert cota.nominal_credit == Decimal(str(first_item["c"]))
    assert cota.advertised_entry == Decimal(str(first_item["e"]))
    assert cota.outstanding_balance is None  # Bidcon não publica saldo devedor
    assert cota.is_contemplated is True
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_validate_access_requires_origin_header(tmp_path):
    settings = _settings(tmp_path)
    adapter = BidconAdapter(settings)
    respx.get(VITRINE_ENDPOINT).mock(
        return_value=Response(403, json={"ok": False, "erro": "origem não permitida"})
    )
    result = await adapter.validate_access()
    assert result.ok is False
    await adapter.aclose()
