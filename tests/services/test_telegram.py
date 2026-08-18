from decimal import Decimal

import pytest

from monitor_cartas.core.statuses import OpportunityClass
from monitor_cartas.repositories.sqlite import QuotaRepository
from monitor_cartas.services.telegram import (
    MAX_MESSAGE_CHARS,
    TelegramNotifier,
    _chunk_opportunity_list,
    format_alert_message,
    format_opportunity_line,
)
from monitor_cartas.settings import MonitoringConfig, Settings
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


def test_format_opportunity_line_contains_key_fields():
    cota = make_cota(
        modality="imoveis",
        opportunity_class=OpportunityClass.GOOD,
        nominal_credit=Decimal("121209"),
        entry_percentage=Decimal("0.226"),
        current_installment=Decimal("2768"),
        administrator="Racon Consórcios",
        source_site="contemplei",
        source_id="243339",
        source_url="https://contemplei.app/carta/exemplo/",
    )
    line = format_opportunity_line(cota, 1)
    assert line.startswith("<b>1.")
    assert "Cre:" in line
    assert "Ent:" in line
    assert "Par:" in line
    assert "Adm:" in line
    assert "Racon Consórcios" in line
    assert "121.209" in line
    assert "22.6" in line
    assert 'href="https://contemplei.app/carta/exemplo/"' in line
    assert "contemplei 243339" in line


def test_format_opportunity_line_includes_entrada_value():
    cota = make_cota(advertised_entry=Decimal("24920"))
    line = format_opportunity_line(cota, 1)
    assert "24.920" in line


def test_format_opportunity_line_escapes_html_in_administrator():
    cota = make_cota(administrator="A & B <consorcios>")
    line = format_opportunity_line(cota, 1)
    assert "<consorcios>" not in line
    assert "&lt;consorcios&gt;" in line


def test_chunk_opportunity_list_single_message_when_small():
    cotas = [make_cota(source_id=str(i)) for i in range(3)]
    messages = _chunk_opportunity_list(cotas, "🏠 Imóvel")
    assert len(messages) == 1
    assert "3 itens" in messages[0]
    assert "────────────" in messages[0]


def test_chunk_opportunity_list_splits_when_too_long():
    cotas = [make_cota(source_id=str(i), administrator="Administradora " * 20) for i in range(60)]
    messages = _chunk_opportunity_list(cotas, "🚗 Veículo")
    assert len(messages) > 1
    for msg in messages:
        assert len(msg) <= MAX_MESSAGE_CHARS + 200  # cabeçalho pode passar um pouco a marca
    assert "continuação 2" in messages[1]


def test_chunk_opportunity_list_empty():
    assert _chunk_opportunity_list([], "🏠 Imóvel") == []


@pytest.mark.asyncio
async def test_send_alert_skips_chat_that_already_received_it(tmp_path, financial_config):
    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs["chat_id"])

    settings = Settings(
        financial=financial_config,
        monitoring=MonitoringConfig(),
        telegram_bot_token="test-token",
        telegram_allowed_chat_ids=["chat-1", "chat-2"],
        data_dir=tmp_path / "data",
    )
    repo = QuotaRepository(settings.db_path)
    cota = make_cota(opportunity_class=OpportunityClass.GOOD)
    repo.conn.execute(
        "INSERT INTO alerts (sent_at, kind, source_site, source_id, chat_id, message) "
        "VALUES ('2026-01-01', 'oportunidade', ?, ?, 'chat-1', 'anterior')",
        (cota.source_site, cota.source_id),
    )
    repo.conn.commit()
    notifier = TelegramNotifier(settings)
    notifier._bot = FakeBot()

    await notifier.send_alert(cota, repo)

    assert notifier._bot.sent == ["chat-2"]
    assert repo.alerted_chat_ids(cota.source_site, cota.source_id) == {"chat-1", "chat-2"}
    repo.close()
