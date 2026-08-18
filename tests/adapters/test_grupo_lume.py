import json
from decimal import Decimal
from pathlib import Path

from monitor_cartas.adapters.grupo_lume import GrupoLumeAdapter, _extract_cota_id
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
        active_sites=["grupo_lume"],
        data_dir=tmp_path / "data",
    )


def test_extract_cota_id():
    assert _extract_cota_id("https://cartascontempladas.com.br/informacao-da-carta/?cota=41060") == "41060"
    assert _extract_cota_id(None) is None


def test_row_to_cota_parses_real_row(tmp_path):
    settings = _settings(tmp_path)
    adapter = GrupoLumeAdapter(settings)
    rows = json.loads((FIXTURES / "grupo_lume_rows.json").read_text())

    cota = adapter._row_to_cota("41060", rows[0])
    assert cota.source_site == "grupo_lume"
    assert cota.source_id == "41060"
    assert cota.administrator == "SICOOB"
    assert cota.nominal_credit == Decimal("46900.00")
    assert cota.advertised_entry == Decimal("14900.00")
    assert cota.remaining_installments == 141
    assert cota.current_installment == Decimal("355.00")
    assert cota.status == QuotaStatus.AVAILABLE
    assert cota.outstanding_balance is None


def test_row_without_link_falls_back_to_matching_modality_page(tmp_path):
    settings = _settings(tmp_path)
    adapter = GrupoLumeAdapter(settings)
    row = {
        "segmento": "Veículos",
        "administradora": "ITAU",
        "credito": "R$ 19.200,00",
        "entrada": "R$ 4.000,00",
        "parcelas": "35",
        "valor_parcela": "607,00",
        "vencimento": "15/09/2026",
        "link": None,
        "_modalidade_pagina": "veiculos",
    }

    cota = adapter._row_to_cota("row-7", row)
    assert cota.source_url == "https://cartascontempladas.com.br/cartas-contempladas-de-veiculos/"
    assert cota.quota is None
