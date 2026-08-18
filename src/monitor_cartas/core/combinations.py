"""Combinação de duas ou três cotas da mesma administradora para atingir a faixa de crédito.

Importante: possuir múltiplas cotas contempladas não implica poder usar os
créditos somados no mesmo imóvel. O status da combinação só vira CONFIRMADA
quando existe uma AdministratorRule validada dizendo isso explicitamente;
caso contrário fica POTENCIAL (ou é descartada, conforme config).
"""
from decimal import Decimal
from itertools import combinations as iter_combinations

from monitor_cartas.core.models import AdministratorRule, Combination, CotaContemplada
from monitor_cartas.core.statuses import (
    CombinationRuleStatus,
    InconsistencyLevel,
    QuotaStatus,
)
from monitor_cartas.settings import FinancialConfig

# Sem isso, uma administradora com centenas de cotas no banco (comum depois
# de algumas semanas rodando) faz itertools.combinations explodir — C(474,3)
# já passa de 17 milhões. Cada grupo é cortado pro top-N mais barato (menor
# desembolso/crédito) antes de combinar; o resultado final continua limitado
# por maximum_results de qualquer forma, então cortar o candidato não perde
# as melhores combinações reais.
MAX_CANDIDATES_PER_ADMINISTRATOR = 40


def _rule_status_for(
    administrator: str, rules: dict[str, AdministratorRule], allow_unconfirmed: bool
) -> CombinationRuleStatus:
    rule = rules.get(administrator)
    if rule is None:
        return CombinationRuleStatus.POTENTIAL if allow_unconfirmed else CombinationRuleStatus.UNKNOWN

    if rule.multiple_quotas_allowed is False or rule.multiple_credits_same_property is False:
        return CombinationRuleStatus.NOT_ALLOWED

    if rule.multiple_quotas_allowed is True and rule.multiple_credits_same_property is True:
        return CombinationRuleStatus.CONFIRMED

    return CombinationRuleStatus.POTENTIAL if allow_unconfirmed else CombinationRuleStatus.UNKNOWN


def find_combinations(
    quotas: list[CotaContemplada],
    config: FinancialConfig,
    administrator_rules: dict[str, AdministratorRule] | None = None,
) -> list[Combination]:
    if not config.combination.enabled:
        return []

    administrator_rules = administrator_rules or {}

    eligible = [
        q
        for q in quotas
        if q.status in (QuotaStatus.NEW, QuotaStatus.AVAILABLE, QuotaStatus.SEEN)
        and q.inconsistency_level != InconsistencyLevel.CRITICAL
        and q.nominal_credit is not None
        and q.known_initial_disbursement is not None
        # uma cota sozinha já maior que o teto nunca cabe numa combinação
        # (somar só aumenta o total), então corta antes de combinar
        and q.nominal_credit <= config.target_credit_max
    ]

    results: list[Combination] = []
    seen_key_sets: set[frozenset[tuple[str, str]]] = set()

    by_admin: dict[str, list[CotaContemplada]] = {}
    for q in eligible:
        admin = q.administrator or "DESCONHECIDA"
        by_admin.setdefault(admin, []).append(q)

    for administrator, admin_quotas in by_admin.items():
        rule_status = _rule_status_for(
            administrator, administrator_rules, config.combination.allow_unconfirmed_rules
        )
        if rule_status in (CombinationRuleStatus.NOT_ALLOWED, CombinationRuleStatus.UNKNOWN):
            continue

        if len(admin_quotas) > MAX_CANDIDATES_PER_ADMINISTRATOR:
            admin_quotas = sorted(
                admin_quotas, key=lambda q: q.known_initial_disbursement / q.nominal_credit
            )[:MAX_CANDIDATES_PER_ADMINISTRATOR]

        for size in range(
            config.combination.minimum_quotas, config.combination.maximum_quotas + 1
        ):
            for combo in iter_combinations(admin_quotas, size):
                key_set = frozenset((q.source_site, q.source_id) for q in combo)
                if key_set in seen_key_sets:
                    continue
                seen_key_sets.add(key_set)

                total_credit = sum((q.nominal_credit for q in combo), start=Decimal(0))
                total_disbursement = sum(
                    (q.known_initial_disbursement for q in combo), start=Decimal(0)
                )

                if not config_credit_in_range(total_credit, config):
                    continue

                installments = [q.current_installment for q in combo]
                total_installment = None
                if all(i is not None for i in installments):
                    total_installment = sum(installments, start=Decimal(0))
                    if total_installment > config.max_monthly_payment:
                        continue

                if total_credit <= 0:
                    continue

                aggregate_pct = total_disbursement / total_credit

                notes = []
                if total_installment is None:
                    notes.append("Parcela total não calculada: alguma cota sem parcela informada.")
                if rule_status == CombinationRuleStatus.POTENTIAL:
                    notes.append(
                        "Regra de uso conjunto do crédito no mesmo imóvel ainda não confirmada "
                        f"para {administrator}."
                    )

                results.append(
                    Combination(
                        quota_keys=[(q.source_site, q.source_id) for q in combo],
                        administrator=administrator,
                        total_credit=total_credit,
                        total_known_disbursement=total_disbursement,
                        total_installment=total_installment,
                        aggregate_entry_percentage=aggregate_pct,
                        rule_status=rule_status,
                        notes=notes,
                    )
                )

    results.sort(key=lambda c: c.aggregate_entry_percentage)
    return results[: config.combination.maximum_results]


def config_credit_in_range(total_credit, config: FinancialConfig) -> bool:
    return config.target_credit_min <= total_credit <= config.target_credit_max
