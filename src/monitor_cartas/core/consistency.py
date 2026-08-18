"""Checagem de consistência aritmética entre parcelas e saldo devedor.

Nunca declara fraude: existem reajustes, seguros e fundo de reserva que
explicam divergências legítimas. Só sinaliza quando a divergência excede
a tolerância configurada.
"""
from decimal import Decimal

from monitor_cartas.core.models import CotaContemplada
from monitor_cartas.core.statuses import InconsistencyLevel
from monitor_cartas.settings import ConsistencyConfig


def check_consistency(
    cota: CotaContemplada, config: ConsistencyConfig
) -> CotaContemplada:
    if (
        cota.remaining_installments is None
        or cota.current_installment is None
        or cota.outstanding_balance is None
        or cota.outstanding_balance == 0
    ):
        cota.inconsistency_level = InconsistencyLevel.NOT_APPLICABLE
        cota.data_inconsistency = None
        cota.inconsistency_reason = "Dados insuficientes para checagem de consistência."
        return cota

    total_linear = Decimal(cota.remaining_installments) * cota.current_installment
    absolute_diff = abs(total_linear - cota.outstanding_balance)
    percentage_diff = absolute_diff / abs(cota.outstanding_balance)

    if percentage_diff <= config.warning_difference_percentage:
        cota.inconsistency_level = InconsistencyLevel.CONSISTENT
        cota.data_inconsistency = False
        cota.inconsistency_reason = None
    elif percentage_diff <= config.critical_difference_percentage:
        cota.inconsistency_level = InconsistencyLevel.REVIEW
        cota.data_inconsistency = True
        cota.inconsistency_reason = (
            f"Parcelas restantes × parcela atual diverge {percentage_diff:.1%} "
            "do saldo devedor anunciado. Pode ser reajuste, seguro ou fundo "
            "de reserva — revisar antes de decidir."
        )
    else:
        cota.inconsistency_level = InconsistencyLevel.CRITICAL
        cota.data_inconsistency = True
        cota.inconsistency_reason = (
            f"Divergência de {percentage_diff:.1%} entre parcelas×saldo e o saldo "
            "devedor anunciado, acima da tolerância crítica. Exigir extrato oficial "
            "antes de qualquer decisão."
        )

    return cota
