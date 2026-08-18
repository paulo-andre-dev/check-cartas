from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from monitor_cartas.core.statuses import (
    AdapterAccessBlockReason,
    CombinationRuleStatus,
    ConfidenceLevel,
    CreditCalculationStatus,
    InconsistencyLevel,
    OpportunityClass,
    QuotaStatus,
    RegulationValidationStatus,
)


class CotaContemplada(BaseModel):
    """Representação normalizada de um anúncio de cota contemplada.

    Campos financeiros calculados pelo pipeline (desembolso, percentuais,
    inconsistência, confiabilidade) começam None: o adapter só entrega o
    que o site publica; quem calcula é core/filters.py e core/consistency.py.
    """

    model_config = ConfigDict(frozen=False)

    # Identidade e origem
    source_site: str
    source_id: str
    source_url: str
    collected_at: datetime
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    transaction_status: str | None = None
    payment_protection: str | None = None

    # Status
    status: QuotaStatus = QuotaStatus.UNKNOWN
    status_raw: str | None = None
    is_contemplated: bool | None = None
    modality: str | None = None

    # Administradora / grupo / cota
    administrator: str | None = None
    administrator_cnpj: str | None = None
    group: str | None = None
    quota: str | None = None

    # Crédito
    nominal_credit: Decimal | None = None
    updated_credit: Decimal | None = None
    used_credit: Decimal | None = None
    liquid_credit: Decimal | None = None

    # Desembolso inicial
    advertised_entry: Decimal | None = None
    seller_price: Decimal | None = None
    platform_fee: Decimal | None = None
    commission_fee: Decimal | None = None
    transfer_fee: Decimal | None = None
    overdue_installments: Decimal | None = None
    other_initial_costs: Decimal | None = None
    known_initial_disbursement: Decimal | None = None
    confirmed_initial_disbursement: Decimal | None = None
    has_unknown_fees: bool | None = None

    # Indicadores financeiros
    entry_percentage: Decimal | None = None
    leverage: Decimal | None = None
    credit_calculation_status: CreditCalculationStatus | None = None
    opportunity_class: OpportunityClass | None = None

    # Saldo e parcelas
    outstanding_balance: Decimal | None = None
    remaining_installments: int | None = None
    current_installment: Decimal | None = None
    installment_type: str | None = None
    adjustment_index: str | None = None
    next_due_date: datetime | None = None

    # Consistência e confiabilidade
    data_inconsistency: bool | None = None
    inconsistency_level: InconsistencyLevel | None = None
    inconsistency_reason: str | None = None
    confidence_level: ConfidenceLevel | None = None

    # Regras
    combination_rule_status: CombinationRuleStatus = CombinationRuleStatus.UNKNOWN
    transfer_rule_status: RegulationValidationStatus | None = None
    regulation_source_url: str | None = None
    regulation_checked_at: datetime | None = None

    # Auditoria
    raw_evidence_path: str | None = None
    adapter_version: str | None = None
    extraction_notes: list[str] = []


class AccessResult(BaseModel):
    ok: bool
    block_reason: AdapterAccessBlockReason | None = None
    detail: str | None = None
    checked_at: datetime


class AdapterRunResult(BaseModel):
    site: str
    started_at: datetime
    finished_at: datetime
    access: AccessResult
    listing_count: int = 0
    processed_count: int = 0
    error_count: int = 0
    snapshot_complete: bool = True
    snapshot_detail: str | None = None
    quotas: list[CotaContemplada] = []
    errors: list[str] = []


class AdministratorRule(BaseModel):
    administrator: str
    cnpj: str | None = None
    bcb_authorization_status: str | None = None
    official_source_url: str | None = None
    source_type: str | None = None
    source_accessed_at: datetime | None = None
    transfer_allowed: bool | None = None
    credit_analysis_required: bool | None = None
    guarantor_may_be_required: bool | None = None
    real_estate_guarantee_rules: str | None = None
    multiple_quotas_allowed: bool | None = None
    multiple_credits_same_property: bool | None = None
    additional_fees: str | None = None
    refund_if_transfer_denied: str | None = None
    notes: str | None = None
    validation_status: RegulationValidationStatus = (
        RegulationValidationStatus.PENDING_MANUAL_VALIDATION
    )


class Combination(BaseModel):
    quota_keys: list[tuple[str, str]]
    administrator: str
    total_credit: Decimal
    total_known_disbursement: Decimal
    total_installment: Decimal | None
    aggregate_entry_percentage: Decimal
    rule_status: CombinationRuleStatus
    notes: list[str] = []
