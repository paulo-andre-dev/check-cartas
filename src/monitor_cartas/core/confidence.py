from monitor_cartas.core.models import CotaContemplada
from monitor_cartas.core.statuses import ConfidenceLevel, CreditCalculationStatus, InconsistencyLevel


def compute_confidence(cota: CotaContemplada) -> CotaContemplada:
    if cota.inconsistency_level == InconsistencyLevel.CRITICAL:
        cota.confidence_level = ConfidenceLevel.LOW
        return cota

    if cota.has_unknown_fees or cota.inconsistency_level == InconsistencyLevel.REVIEW:
        cota.confidence_level = ConfidenceLevel.MEDIUM
        return cota

    if cota.credit_calculation_status == CreditCalculationStatus.NOMINAL_FALLBACK:
        cota.confidence_level = ConfidenceLevel.MEDIUM
        return cota

    if cota.credit_calculation_status == CreditCalculationStatus.UNAVAILABLE:
        cota.confidence_level = ConfidenceLevel.LOW
        return cota

    cota.confidence_level = ConfidenceLevel.HIGH
    return cota
