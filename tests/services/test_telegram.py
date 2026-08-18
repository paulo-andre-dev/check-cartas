from decimal import Decimal

from monitor_cartas.core.statuses import OpportunityClass
from monitor_cartas.services.telegram import format_alert_message
from tests.conftest import make_cota


def test_format_alert_message_shows_modality_imovel():
    cota = make_cota(modality="imoveis", opportunity_class=OpportunityClass.GOOD)
    msg = format_alert_message(cota)
    assert "Modalidade: 🏠 Imóvel" in msg


def test_format_alert_message_shows_modality_veiculo():
    cota = make_cota(modality="veiculo", opportunity_class=OpportunityClass.GOOD)
    msg = format_alert_message(cota)
    assert "Modalidade: 🚗 Veículo" in msg


def test_format_alert_message_falls_back_to_raw_modality_when_unrecognized():
    cota = make_cota(modality="servico", opportunity_class=OpportunityClass.GOOD)
    msg = format_alert_message(cota)
    assert "Modalidade: servico" in msg


def test_format_alert_message_handles_missing_modality():
    cota = make_cota(modality=None, opportunity_class=OpportunityClass.GOOD)
    msg = format_alert_message(cota)
    assert "modalidade não identificada" in msg
