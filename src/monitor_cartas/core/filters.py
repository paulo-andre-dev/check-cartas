"""Regras de desembolso, crédito, classificação e orçamento.

Esta é a única camada que decide "quanto custa entrar" e "vale a pena".
Os adapters nunca calculam isso — só entregam os campos brutos publicados.
"""
from decimal import Decimal

from monitor_cartas.core.models import CotaContemplada
from monitor_cartas.core.statuses import CreditCalculationStatus, OpportunityClass
from monitor_cartas.settings import FinancialConfig


def compute_initial_disbursement(cota: CotaContemplada) -> CotaContemplada:
    """Soma os custos iniciais conhecidos; nunca trata campo ausente como zero."""
    known_fields = [
        cota.seller_price if cota.seller_price is not None else cota.advertised_entry,
        cota.platform_fee,
        cota.commission_fee,
        cota.transfer_fee,
        cota.overdue_installments,
        cota.other_initial_costs,
    ]

    base = known_fields[0]
    fee_fields = known_fields[1:]

    if base is None:
        cota.known_initial_disbursement = None
        cota.has_unknown_fees = True
        return cota

    total = base
    any_unknown_fee = False
    for fee in fee_fields:
        if fee is None:
            any_unknown_fee = True
            continue
        total += fee

    cota.known_initial_disbursement = total
    cota.has_unknown_fees = any_unknown_fee
    return cota


def select_credit_basis(cota: CotaContemplada, config: FinancialConfig) -> Decimal | None:
    """Escolhe o crédito usado no cálculo, marcando quando é provisório."""
    if config.credit_basis == "liquid" and cota.liquid_credit is not None:
        cota.credit_calculation_status = CreditCalculationStatus.LIQUID
        return cota.liquid_credit

    if config.credit_basis in ("liquid", "updated") and cota.updated_credit is not None:
        cota.credit_calculation_status = CreditCalculationStatus.UPDATED
        return cota.updated_credit

    if config.credit_basis == "nominal" and cota.nominal_credit is not None:
        cota.credit_calculation_status = CreditCalculationStatus.NOMINAL_FALLBACK
        return cota.nominal_credit

    if config.fallback_to_nominal_credit and cota.nominal_credit is not None:
        cota.credit_calculation_status = CreditCalculationStatus.NOMINAL_FALLBACK
        return cota.nominal_credit

    cota.credit_calculation_status = CreditCalculationStatus.UNAVAILABLE
    return None


def compute_entry_percentage_and_leverage(
    cota: CotaContemplada, config: FinancialConfig
) -> CotaContemplada:
    credit = select_credit_basis(cota, config)
    disbursement = cota.confirmed_initial_disbursement or cota.known_initial_disbursement

    if credit is None or disbursement is None or credit <= 0:
        cota.entry_percentage = None
        cota.leverage = None
        cota.opportunity_class = OpportunityClass.NO_PRICE
        return cota

    if disbursement < 0:
        cota.opportunity_class = OpportunityClass.INVALID_DATA
        return cota

    cota.entry_percentage = disbursement / credit
    cota.leverage = credit / disbursement if disbursement > 0 else None
    cota.opportunity_class = classify_opportunity(cota.entry_percentage, config)
    return cota


def classify_opportunity(
    percentage: Decimal | None, config: FinancialConfig
) -> OpportunityClass:
    if percentage is None:
        return OpportunityClass.NO_PRICE
    if percentage < 0:
        return OpportunityClass.INVALID_DATA
    if percentage <= config.gold_entry_percentage:
        return OpportunityClass.GOLD
    if percentage <= Decimal("0.12"):
        return OpportunityClass.EXCEPTIONAL
    if percentage <= config.max_entry_percentage:
        return OpportunityClass.VERY_GOOD
    if percentage <= config.good_entry_percentage:
        return OpportunityClass.GOOD
    return OpportunityClass.NORMAL


def passes_budget(cota: CotaContemplada, config: FinancialConfig) -> bool | None:
    """True = cabe no orçamento, False = estourou, None = parcela desconhecida."""
    if cota.current_installment is None:
        return None
    return cota.current_installment <= config.max_monthly_payment


def passes_modality_limits(cota: CotaContemplada, config: FinancialConfig) -> bool | None:
    """Teto de crédito e de parcela por modalidade (imóvel/veículo).

    None = não dá pra avaliar (falta dado); True/False = passou ou não.

    Modalidade não reconhecida (nem imóvel nem veículo) sempre bloqueia
    (False) — nunca passa "por baixo do radar" só respeitando o teto
    genérico de parcela sem checar crédito. Se a modalidade não tem teto
    configurado em config.yaml, cai pro teto genérico (comportamento
    antigo do projeto, antes de existir teto por modalidade).
    """
    from monitor_cartas.core.modality import normalize_modality

    modality = normalize_modality(cota.modality)
    if modality is None:
        return False

    limits = config.modality_limits.get(modality)
    if limits is None:
        return passes_budget(cota, config)

    credit = cota.liquid_credit or cota.updated_credit or cota.nominal_credit
    if limits.max_credit is not None and credit is not None and credit > limits.max_credit:
        return False

    if (
        limits.max_monthly_payment is not None
        and cota.current_installment is not None
        and cota.current_installment > limits.max_monthly_payment
    ):
        return False

    if credit is None and cota.current_installment is None:
        return None

    return True


def is_within_credit_range(cota: CotaContemplada, config: FinancialConfig) -> bool | None:
    credit = cota.liquid_credit or cota.updated_credit or cota.nominal_credit
    if credit is None:
        return None
    return config.target_credit_min <= credit <= config.target_credit_max


def apply_filters(cota: CotaContemplada, config: FinancialConfig) -> CotaContemplada:
    cota = compute_initial_disbursement(cota)
    cota = compute_entry_percentage_and_leverage(cota, config)
    return cota
