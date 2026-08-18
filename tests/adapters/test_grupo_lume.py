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


def _rows():
    return json.loads((FIXTURES / "grupo_lume_rows.json").read_text())


def test_extract_cota_id():
    assert _extract_cota_id("https://cartascontempladas.com.br/informacao-da-carta/?cota=41060") == "41060"
    assert _extract_cota_id(None) is None


def test_row_with_no_class_is_available(tmp_path):
    settings = _settings(tmp_path)
    adapter = GrupoLumeAdapter(settings)
    row = _rows()[0]

    cota = adapter._row_to_cota("41641", row)
    assert cota.status == QuotaStatus.AVAILABLE
    assert cota.nominal_credit == Decimal("19200.00")
    assert cota.advertised_entry == Decimal("4000.00")
    assert cota.remaining_installments == 35
    assert cota.current_installment == Decimal("607.00")


def test_row_bgcinza_is_available(tmp_path):
    settings = _settings(tmp_path)
    adapter = GrupoLumeAdapter(settings)
    row = _rows()[1]

    cota = adapter._row_to_cota("41060", row)
    assert cota.status == QuotaStatus.AVAILABLE
    assert cota.administrator == "SICOOB"
    assert cota.current_installment == Decimal("355.00")
    assert cota.outstanding_balance is None


def test_row_bgvermelho_is_reserved_not_available(tmp_path):
    """Reproduz o bug relatado: linha vermelha (reservada) sem link nem
    parcela única — antes virava AVAILABLE e aparecia em /melhores."""
    settings = _settings(tmp_path)
    adapter = GrupoLumeAdapter(settings)
    row = _rows()[2]

    cota = adapter._row_to_cota("row-2", row)
    assert cota.status == QuotaStatus.RESERVED
    assert cota.nominal_credit == Decimal("44500.00")
    assert cota.advertised_entry == Decimal("13200.00")
    # plano escalonado ("37 x 10.650,00 + 10 x 460,00") não é parseado como
    # valor único — fica desconhecido em vez de virar "37,00" por engano
    assert cota.current_installment is None
    assert cota.remaining_installments is None
    assert cota.quota is None


def test_row_bgvermelho_bgescuro_is_reserved(tmp_path):
    settings = _settings(tmp_path)
    adapter = GrupoLumeAdapter(settings)
    row = _rows()[3]

    cota = adapter._row_to_cota("row-3", row)
    assert cota.status == QuotaStatus.RESERVED
    assert cota.current_installment is None


def test_row_without_link_and_without_class_falls_back_to_matching_modality_page(tmp_path):
    settings = _settings(tmp_path)
    adapter = GrupoLumeAdapter(settings)
    row = {
        "rowClass": "",
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
    assert cota.status == QuotaStatus.AVAILABLE
