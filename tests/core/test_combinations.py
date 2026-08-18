from decimal import Decimal

from monitor_cartas.core.combinations import find_combinations
from monitor_cartas.core.models import AdministratorRule
from monitor_cartas.core.statuses import CombinationRuleStatus, QuotaStatus
from tests.conftest import make_cota


def _eligible(**overrides):
    defaults = dict(
        status=QuotaStatus.AVAILABLE,
        nominal_credit=Decimal("180000"),
        known_initial_disbursement=Decimal("15000"),
        current_installment=Decimal("2500"),
    )
    defaults.update(overrides)
    return make_cota(**defaults)


def test_combination_within_range_marked_potential_by_default(financial_config):
    a = _eligible(source_id="1", administrator="Caixa Consórcios")
    b = _eligible(source_id="2", administrator="Caixa Consórcios")
    combos = find_combinations([a, b], financial_config)
    assert len(combos) == 1
    assert combos[0].total_credit == Decimal("360000")
    assert combos[0].rule_status == CombinationRuleStatus.POTENTIAL


def test_different_administrators_not_combined(financial_config):
    a = _eligible(source_id="1", administrator="Caixa Consórcios")
    b = _eligible(source_id="2", administrator="Itaú Consórcios")
    combos = find_combinations([a, b], financial_config)
    assert combos == []


def test_combination_outside_credit_range_excluded(financial_config):
    a = _eligible(source_id="1", administrator="Caixa Consórcios", nominal_credit=Decimal("50000"))
    b = _eligible(source_id="2", administrator="Caixa Consórcios", nominal_credit=Decimal("60000"))
    combos = find_combinations([a, b], financial_config)
    assert combos == []


def test_combination_over_budget_excluded(financial_config):
    a = _eligible(source_id="1", administrator="Caixa Consórcios", current_installment=Decimal("4000"))
    b = _eligible(source_id="2", administrator="Caixa Consórcios", current_installment=Decimal("4000"))
    combos = find_combinations([a, b], financial_config)
    assert combos == []


def test_combination_confirmed_when_administrator_rule_allows(financial_config):
    a = _eligible(source_id="1", administrator="Caixa Consórcios")
    b = _eligible(source_id="2", administrator="Caixa Consórcios")
    rules = {
        "Caixa Consórcios": AdministratorRule(
            administrator="Caixa Consórcios",
            multiple_quotas_allowed=True,
            multiple_credits_same_property=True,
        )
    }
    combos = find_combinations([a, b], financial_config, rules)
    assert combos[0].rule_status == CombinationRuleStatus.CONFIRMED


def test_combination_not_allowed_when_administrator_rule_forbids(financial_config):
    a = _eligible(source_id="1", administrator="Caixa Consórcios")
    b = _eligible(source_id="2", administrator="Caixa Consórcios")
    rules = {
        "Caixa Consórcios": AdministratorRule(
            administrator="Caixa Consórcios", multiple_credits_same_property=False
        )
    }
    combos = find_combinations([a, b], financial_config, rules)
    assert combos == []


def test_large_administrator_group_does_not_hang(financial_config):
    import time

    # 300 cotas da mesma administradora sem o corte de candidatos faria
    # C(300,3) ~ 4.4 milhões de combinações — precisa terminar rápido.
    quotas = [
        _eligible(source_id=str(i), administrator="Porto Seguro", nominal_credit=Decimal("50000"))
        for i in range(300)
    ]
    start = time.monotonic()
    combos = find_combinations(quotas, financial_config)
    elapsed = time.monotonic() - start
    assert elapsed < 5
    assert isinstance(combos, list)


def test_quota_individually_above_target_max_is_excluded(financial_config):
    a = _eligible(source_id="1", administrator="Caixa Consórcios", nominal_credit=Decimal("500000"))
    b = _eligible(source_id="2", administrator="Caixa Consórcios")
    combos = find_combinations([a, b], financial_config)
    assert combos == []
