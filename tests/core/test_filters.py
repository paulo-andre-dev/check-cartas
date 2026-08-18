from decimal import Decimal

from monitor_cartas.core.filters import (
    apply_filters,
    classify_opportunity,
    compute_initial_disbursement,
    passes_budget,
    passes_modality_limits,
    select_credit_basis,
)
from monitor_cartas.core.statuses import CreditCalculationStatus, OpportunityClass
from tests.conftest import make_cota


def test_disbursement_sums_known_fees(financial_config):
    cota = make_cota(commission_fee=None)
    cota = compute_initial_disbursement(cota)
    assert cota.known_initial_disbursement == Decimal("31750.00")
    assert cota.has_unknown_fees is True  # commission_fee desconhecida


def test_disbursement_none_when_seller_price_unknown(financial_config):
    cota = make_cota(seller_price=None, advertised_entry=None)
    cota = compute_initial_disbursement(cota)
    assert cota.known_initial_disbursement is None
    assert cota.has_unknown_fees is True


def test_disbursement_no_unknown_when_all_fees_present():
    cota = make_cota(commission_fee=Decimal("0"), overdue_installments=Decimal("0"), other_initial_costs=Decimal("0"))
    cota = compute_initial_disbursement(cota)
    assert cota.has_unknown_fees is False


def test_select_credit_basis_falls_back_to_nominal_and_marks_provisional(financial_config):
    cota = make_cota(liquid_credit=None, updated_credit=None, nominal_credit=Decimal("350000"))
    credit = select_credit_basis(cota, financial_config)
    assert credit == Decimal("350000")
    assert cota.credit_calculation_status == CreditCalculationStatus.NOMINAL_FALLBACK


def test_select_credit_basis_prefers_liquid(financial_config):
    cota = make_cota(liquid_credit=Decimal("340000"), nominal_credit=Decimal("350000"))
    credit = select_credit_basis(cota, financial_config)
    assert credit == Decimal("340000")
    assert cota.credit_calculation_status == CreditCalculationStatus.LIQUID


def test_classify_opportunity_thresholds(financial_config):
    assert classify_opportunity(Decimal("0.0953"), financial_config) == OpportunityClass.GOLD
    assert classify_opportunity(Decimal("0.11"), financial_config) == OpportunityClass.EXCEPTIONAL
    assert classify_opportunity(Decimal("0.14"), financial_config) == OpportunityClass.VERY_GOOD
    assert classify_opportunity(Decimal("0.18"), financial_config) == OpportunityClass.GOOD
    assert classify_opportunity(Decimal("0.25"), financial_config) == OpportunityClass.GOOD
    assert classify_opportunity(Decimal("0.35"), financial_config) == OpportunityClass.NORMAL
    assert classify_opportunity(None, financial_config) == OpportunityClass.NO_PRICE


def test_apply_filters_end_to_end_matches_9_53_example(financial_config):
    # Reproduz o caso citado pelo usuário: 265.423,17 de crédito, 25.303,62 de entrada -> 9,53%
    cota = make_cota(
        liquid_credit=None,
        updated_credit=None,
        nominal_credit=Decimal("265423.17"),
        seller_price=Decimal("25303.62"),
        advertised_entry=Decimal("25303.62"),
        platform_fee=None,
        transfer_fee=None,
        commission_fee=None,
        overdue_installments=None,
        other_initial_costs=None,
    )
    cota = apply_filters(cota, financial_config)
    assert cota.known_initial_disbursement == Decimal("25303.62")
    assert cota.entry_percentage.quantize(Decimal("0.0001")) == Decimal("0.0953")
    assert cota.opportunity_class == OpportunityClass.GOLD
    assert cota.has_unknown_fees is True  # taxas de plataforma/transferência não informadas


def test_passes_budget(financial_config):
    within = make_cota(current_installment=Decimal("5000"))
    over = make_cota(current_installment=Decimal("7000"))
    unknown = make_cota(current_installment=None)
    assert passes_budget(within, financial_config) is True
    assert passes_budget(over, financial_config) is False
    assert passes_budget(unknown, financial_config) is None


def test_passes_modality_limits_imovel(financial_config):
    dentro = make_cota(
        modality="imoveis", nominal_credit=Decimal("350000"), current_installment=Decimal("5000")
    )
    credito_estoura = make_cota(
        modality="imoveis", nominal_credit=Decimal("450000"), current_installment=Decimal("5000")
    )
    parcela_estoura = make_cota(
        modality="imoveis", nominal_credit=Decimal("350000"), current_installment=Decimal("6500")
    )
    assert passes_modality_limits(dentro, financial_config) is True
    assert passes_modality_limits(credito_estoura, financial_config) is False
    assert passes_modality_limits(parcela_estoura, financial_config) is False


def test_passes_modality_limits_veiculo_has_lower_caps(financial_config):
    # 250k passa no teto de imóvel (400k) mas não no de veículo (200k)
    carro_caro = make_cota(
        modality="veiculo", nominal_credit=Decimal("250000"), current_installment=Decimal("2000")
    )
    carro_ok = make_cota(
        modality="veiculo", nominal_credit=Decimal("150000"), current_installment=Decimal("2000")
    )
    carro_parcela_estoura = make_cota(
        modality="veiculo", nominal_credit=Decimal("150000"), current_installment=Decimal("3000")
    )
    assert passes_modality_limits(carro_caro, financial_config) is False
    assert passes_modality_limits(carro_ok, financial_config) is True
    assert passes_modality_limits(carro_parcela_estoura, financial_config) is False


def test_passes_modality_limits_veiculo_has_minimum_credit(financial_config):
    # carta de carro pequena demais (abaixo de 20k) não vale a pena olhar
    carro_barato_demais = make_cota(
        modality="veiculo", nominal_credit=Decimal("15000"), current_installment=Decimal("500")
    )
    carro_no_piso = make_cota(
        modality="veiculo", nominal_credit=Decimal("20000"), current_installment=Decimal("500")
    )
    assert passes_modality_limits(carro_barato_demais, financial_config) is False
    assert passes_modality_limits(carro_no_piso, financial_config) is True


def test_passes_modality_limits_unrecognized_modality_always_blocks(financial_config):
    # Modalidade não reconhecida (nem imóvel nem veículo) nunca deve passar
    # "por baixo", mesmo com parcela baixa e crédito baixo — o risco é não
    # conseguir aplicar o teto certo, então bloqueia sempre.
    cota = make_cota(
        modality="servico", nominal_credit=Decimal("10000"), current_installment=Decimal("100")
    )
    assert passes_modality_limits(cota, financial_config) is False

    cota_sem_modalidade = make_cota(modality=None, current_installment=Decimal("100"))
    assert passes_modality_limits(cota_sem_modalidade, financial_config) is False


def test_passes_modality_limits_recognized_modality_without_configured_cap_falls_back(
    financial_config,
):
    # Se a modalidade É reconhecida mas não tem teto configurado em
    # config.yaml pra ela, cai pro teto genérico de parcela (comportamento
    # antigo, antes de existir teto por modalidade) — diferente de
    # modalidade desconhecida, que sempre bloqueia.
    financial_config.modality_limits = {}
    cota = make_cota(modality="imoveis", current_installment=Decimal("5500"))
    assert passes_modality_limits(cota, financial_config) is True
    cota_estoura = make_cota(modality="imoveis", current_installment=Decimal("6500"))
    assert passes_modality_limits(cota_estoura, financial_config) is False
