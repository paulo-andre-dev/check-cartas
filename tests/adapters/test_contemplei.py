import json
from decimal import Decimal
from pathlib import Path

import pytest
import respx
from httpx import Response

from monitor_cartas.adapters.contemplei import LIST_ENDPOINT, ContempleiAdapter
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
        active_sites=["contemplei"],
        data_dir=tmp_path / "data",
    )


@pytest.mark.asyncio
@respx.mock
async def test_collect_listing_urls_paginates(tmp_path):
    settings = _settings(tmp_path)
    adapter = ContempleiAdapter(settings)

    page1_items = [{"id": f"id-{i}"} for i in range(100)]
    page2_items = [{"id": "id-100"}, {"id": "id-101"}]
    moveis_items = [{"id": "moveis-0"}]

    respx.get(LIST_ENDPOINT, params={"segmento": "imoveis", "page": "1", "pageSize": "100"}).mock(
        return_value=Response(200, json={"data": page1_items, "meta": {"success": True, "total": 102}})
    )
    respx.get(LIST_ENDPOINT, params={"segmento": "imoveis", "page": "2", "pageSize": "100"}).mock(
        return_value=Response(200, json={"data": page2_items, "meta": {"success": True, "total": 102}})
    )
    respx.get(LIST_ENDPOINT, params={"segmento": "moveis", "page": "1", "pageSize": "100"}).mock(
        return_value=Response(200, json={"data": moveis_items, "meta": {"success": True, "total": 1}})
    )

    urls = await adapter.collect_listing_urls()
    assert len(urls) == 103
    assert urls[0] == f"{LIST_ENDPOINT}/id-0"
    assert urls[101] == f"{LIST_ENDPOINT}/id-101"
    assert urls[-1] == f"{LIST_ENDPOINT}/moveis-0"
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_collect_quota_parses_real_fixture(tmp_path):
    settings = _settings(tmp_path)
    adapter = ContempleiAdapter(settings)

    payload = json.loads((FIXTURES / "contemplei_detalhe_imovel.json").read_text())
    detail_url = f"{LIST_ENDPOINT}/851da047-078e-4c28-b725-0e8d4f5fe69f"
    respx.get(detail_url).mock(return_value=Response(200, json=payload))

    cota = await adapter.collect_quota(detail_url)

    assert cota.source_site == "contemplei"
    assert cota.source_id == "324351"
    assert cota.source_url == (
        "https://contemplei.app/carta/carta-contemplada-imoveis-caixa-consorcios-251-mil-324351/"
    )
    assert cota.administrator == "Caixa Consórcios"
    assert cota.group == "5805"
    assert cota.nominal_credit == Decimal("251663.00")
    assert cota.advertised_entry == Decimal("103183.52")
    assert cota.transfer_fee == Decimal("1250.00")
    assert cota.outstanding_balance == Decimal("307905.00")
    assert cota.remaining_installments == 195
    assert cota.is_contemplated is True
    assert cota.has_unknown_fees is None  # calculado depois pelo filters.py, não pelo adapter
    assert cota.raw_evidence_path is not None
    assert Path(cota.raw_evidence_path).exists()

    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_validate_access_detects_http_error(tmp_path):
    settings = _settings(tmp_path)
    adapter = ContempleiAdapter(settings)
    respx.get(LIST_ENDPOINT, params={"segmento": "imoveis", "pageSize": "1"}).mock(
        return_value=Response(503)
    )
    result = await adapter.validate_access()
    assert result.ok is False
    await adapter.aclose()
