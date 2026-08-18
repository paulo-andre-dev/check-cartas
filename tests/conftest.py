from datetime import datetime, timezone
from decimal import Decimal

import pytest

from monitor_cartas.core.modality import MODALITY_IMOVEL, MODALITY_VEICULO
from monitor_cartas.core.models import CotaContemplada
from monitor_cartas.settings import (
    CombinationConfig,
    ConsistencyConfig,
    FinancialConfig,
    ModalityLimits,
)


def make_cota(**overrides) -> CotaContemplada:
    defaults = dict(
        source_site="contemplei",
        source_id="123",
        source_url="https://contemplei.app/carta/exemplo/",
        collected_at=datetime.now(timezone.utc),
        administrator="Caixa Consórcios",
        group="5805",
        nominal_credit=Decimal("350000.00"),
        advertised_entry=Decimal("30000.00"),
        seller_price=Decimal("30000.00"),
        platform_fee=Decimal("500.00"),
        transfer_fee=Decimal("1250.00"),
        outstanding_balance=Decimal("400000.00"),
        remaining_installments=100,
        current_installment=Decimal("4000.00"),
    )
    defaults.update(overrides)
    return CotaContemplada(**defaults)


@pytest.fixture
def financial_config() -> FinancialConfig:
    return FinancialConfig(
        target_credit_min=Decimal("300000.00"),
        target_credit_max=Decimal("400000.00"),
        credit_basis="liquid",
        fallback_to_nominal_credit=True,
        max_entry_percentage=Decimal("0.15"),
        gold_entry_percentage=Decimal("0.10"),
        good_entry_percentage=Decimal("0.30"),
        max_monthly_payment=Decimal("6000.00"),
        combination=CombinationConfig(),
        consistency=ConsistencyConfig(
            warning_difference_percentage=Decimal("0.15"),
            critical_difference_percentage=Decimal("0.35"),
        ),
        modality_limits={
            MODALITY_IMOVEL: ModalityLimits(
                max_credit=Decimal("400000.00"), max_monthly_payment=Decimal("6000.00")
            ),
            MODALITY_VEICULO: ModalityLimits(
                max_credit=Decimal("200000.00"), max_monthly_payment=Decimal("2500.00")
            ),
        },
    )
