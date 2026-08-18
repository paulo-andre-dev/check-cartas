from decimal import Decimal

from monitor_cartas.core.consistency import check_consistency
from monitor_cartas.core.statuses import InconsistencyLevel
from tests.conftest import make_cota


def test_consistent_within_tolerance(financial_config):
    # 100 parcelas x 4000 = 400000, saldo 400000 -> divergência 0%
    cota = make_cota(remaining_installments=100, current_installment=Decimal("4000"), outstanding_balance=Decimal("400000"))
    cota = check_consistency(cota, financial_config.consistency)
    assert cota.inconsistency_level == InconsistencyLevel.CONSISTENT
    assert cota.data_inconsistency is False


def test_review_band(financial_config):
    # 100 x 4000 = 400000 vs saldo 340000 -> divergência ~17.6%, entre 15% e 35%
    cota = make_cota(remaining_installments=100, current_installment=Decimal("4000"), outstanding_balance=Decimal("340000"))
    cota = check_consistency(cota, financial_config.consistency)
    assert cota.inconsistency_level == InconsistencyLevel.REVIEW
    assert cota.data_inconsistency is True


def test_critical_band(financial_config):
    # 100 x 4000 = 400000 vs saldo 200000 -> divergência 100%
    cota = make_cota(remaining_installments=100, current_installment=Decimal("4000"), outstanding_balance=Decimal("200000"))
    cota = check_consistency(cota, financial_config.consistency)
    assert cota.inconsistency_level == InconsistencyLevel.CRITICAL
    assert "extrato oficial" in cota.inconsistency_reason


def test_not_applicable_when_missing_data(financial_config):
    cota = make_cota(current_installment=None)
    cota = check_consistency(cota, financial_config.consistency)
    assert cota.inconsistency_level == InconsistencyLevel.NOT_APPLICABLE
    assert cota.data_inconsistency is None
