"""Alertas e bot de comandos do Telegram.

Envio de alerta (send_alert) e o worker de comandos (/silenciar etc.) são
processos separados por design: o scraper roda 1x/dia via cron, o bot de
comandos precisa ficar de pé continuamente para responder a qualquer hora.

Nunca logar o token. Comandos só são aceitos de chat_ids em
TELEGRAM_ALLOWED_CHAT_IDS.
"""
import html
import logging
from datetime import datetime, timezone

from monitor_cartas.core.filters import passes_modality_limits
from monitor_cartas.core.modality import MODALITY_IMOVEL, MODALITY_VEICULO, normalize_modality
from monitor_cartas.core.models import CotaContemplada
from monitor_cartas.core.money import format_brl, format_percentage
from monitor_cartas.core.statuses import OpportunityClass
from monitor_cartas.repositories.sqlite import QuotaRepository
from monitor_cartas.settings import Settings

logger = logging.getLogger("monitor_cartas.telegram")

OPPORTUNITY_LABELS = {
    OpportunityClass.GOLD: "🥇 OURO",
    OpportunityClass.EXCEPTIONAL: "⭐ EXCEPCIONAL",
    OpportunityClass.VERY_GOOD: "MUITO BOA",
    OpportunityClass.GOOD: "BOA",
    OpportunityClass.NORMAL: "NORMAL",
    OpportunityClass.NO_PRICE: "SEM PREÇO",
    OpportunityClass.INVALID_DATA: "DADO INVÁLIDO",
}

MODALITY_LABELS = {
    MODALITY_IMOVEL: "🏠 Imóvel",
    MODALITY_VEICULO: "🚗 Veículo",
}

REPORTABLE_CLASSES = {
    OpportunityClass.GOLD,
    OpportunityClass.EXCEPTIONAL,
    OpportunityClass.VERY_GOOD,
    OpportunityClass.GOOD,
}


def format_alert_message(cota: CotaContemplada) -> str:
    classe = OPPORTUNITY_LABELS.get(cota.opportunity_class, "—")
    modalidade = MODALITY_LABELS.get(
        normalize_modality(cota.modality), cota.modality or "modalidade não identificada"
    )
    linhas = [
        f"<b>{classe}</b> — {cota.source_site}",
        f"Modalidade: {modalidade}",
        f"Administradora: {cota.administrator or 'não informada'}",
        f"Crédito: {format_brl(cota.nominal_credit)}",
        f"Entrada anunciada: {format_brl(cota.advertised_entry)}",
        f"Desembolso estimado: {format_brl(cota.known_initial_disbursement)}"
        + (" (taxas desconhecidas — pode subir)" if cota.has_unknown_fees else ""),
        f"Entrada %: {format_percentage(cota.entry_percentage)}",
        f"Parcela: {format_brl(cota.current_installment)} em {cota.remaining_installments or '?'}x",
        f"Confiabilidade: {cota.confidence_level.value if cota.confidence_level else 'não avaliada'}",
        f"Link: {cota.source_url}",
        f"ID: <code>{cota.source_site} {cota.source_id}</code>",
    ]
    if cota.inconsistency_level and cota.inconsistency_level.value not in (
        "CONSISTENTE",
        "NAO_APLICAVEL",
    ):
        linhas.insert(1, f"⚠️ {cota.inconsistency_reason}")
    return "\n".join(linhas)


def format_opportunity_line(cota: CotaContemplada, index: int) -> str:
    """Um item numerado de duas linhas, pra listas — o alerta individual
    detalhado continua sendo format_alert_message."""
    classe = OPPORTUNITY_LABELS.get(cota.opportunity_class, "—")
    admin = html.escape(cota.administrator or "administradora não informada")
    parcela = (
        f"{format_brl(cota.current_installment)}/mês"
        if cota.current_installment is not None
        else "parcela não informada"
    )
    pct = format_percentage(cota.entry_percentage)
    credito = format_brl(cota.nominal_credit)
    entrada = format_brl(cota.advertised_entry)
    link = html.escape(cota.source_url, quote=True)
    return (
        f"{index}. {classe} <b>{credito}</b> — Entrada {entrada} ({pct})\n"
        f'    {parcela} · {admin} · <a href="{link}">abrir</a> · '
        f"<code>{cota.source_site} {cota.source_id}</code>"
    )


MAX_MESSAGE_CHARS = 3500  # margem de segurança sob o limite de 4096 do Telegram


def _chunk_opportunity_list(cotas: list[CotaContemplada], title: str) -> list[str]:
    """Divide a lista em uma ou mais mensagens numeradas, cada uma com seu
    próprio cabeçalho, respeitando o limite de tamanho do Telegram."""
    header_base = html.escape(title)
    messages: list[str] = []
    current_lines: list[str] = []
    current_len = 0
    part = 1

    def header(part_num: int) -> str:
        suffix = f" ({len(cotas)})" if part_num == 1 else f" (cont. {part_num})"
        return f"<b>{header_base}</b>{suffix}"

    for i, cota in enumerate(cotas, start=1):
        line = format_opportunity_line(cota, i)
        projected = current_len + len(line) + 2
        if current_lines and projected + len(header(part)) > MAX_MESSAGE_CHARS:
            messages.append(header(part) + "\n\n" + "\n\n".join(current_lines))
            part += 1
            current_lines = []
            current_len = 0
        current_lines.append(line)
        current_len += len(line) + 2

    if current_lines:
        messages.append(header(part) + "\n\n" + "\n\n".join(current_lines))

    return messages


class TelegramNotifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._bot = None
        if settings.telegram_bot_token:
            from telegram import Bot

            self._bot = Bot(token=settings.telegram_bot_token)

    @property
    def enabled(self) -> bool:
        return self._bot is not None and bool(self.settings.telegram_allowed_chat_ids)

    async def send_alert(self, cota: CotaContemplada, repo: QuotaRepository | None = None) -> None:
        message = format_alert_message(cota)
        if not self.enabled:
            logger.warning("Telegram não configurado — alerta não enviado: %s %s", cota.source_site, cota.source_id)
            return

        for chat_id in self.settings.telegram_allowed_chat_ids:
            await self._bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
            if repo is not None:
                repo.conn.execute(
                    "INSERT INTO alerts (sent_at, kind, source_site, source_id, chat_id, message) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        "oportunidade",
                        cota.source_site,
                        cota.source_id,
                        chat_id,
                        message,
                    ),
                )
                repo.conn.commit()


def _allowed(update, settings: Settings) -> bool:
    chat_id = str(update.effective_chat.id)
    return chat_id in settings.telegram_allowed_chat_ids


# Só os dois comandos do dia a dia aparecem sugeridos ao digitar "/" no
# Telegram. Os outros (/status, /detalhes, /silenciar, /reativar,
# /silenciadas, /erros) continuam funcionando normalmente se digitados —
# só não poluem o menu.
BOT_COMMANDS = [
    ("novas", "Cotas novas"),
    ("melhores", "Melhores oportunidades"),
]


def build_bot_application(settings: Settings, repo: QuotaRepository):
    """Monta a Application do python-telegram-bot com os comandos do projeto."""
    from telegram import BotCommand, Update
    from telegram.ext import Application, CommandHandler, ContextTypes

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado em .env")

    async def _register_commands(app: Application) -> None:
        await app.bot.set_my_commands(
            [BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS]
        )

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_register_commands)
        .build()
    )

    async def guarded(update: Update) -> bool:
        if not _allowed(update, settings):
            await update.message.reply_text("Não autorizado.")
            return False
        return True

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guarded(update):
            return
        last_run = repo.last_successful_run()
        text = (
            f"Última execução bem-sucedida: {last_run.isoformat() if last_run else 'nunca'}"
        )
        await update.message.reply_text(text)

    async def _reply_grouped_by_modality(update: Update, cotas: list[CotaContemplada]) -> None:
        imoveis = [c for c in cotas if normalize_modality(c.modality) == MODALITY_IMOVEL]
        veiculos = [c for c in cotas if normalize_modality(c.modality) == MODALITY_VEICULO]
        outros = [
            c
            for c in cotas
            if normalize_modality(c.modality) not in (MODALITY_IMOVEL, MODALITY_VEICULO)
        ]

        for titulo, grupo in (
            ("🏠 Imóvel", imoveis),
            ("🚗 Veículo", veiculos),
            ("❓ Modalidade não identificada", outros),
        ):
            if not grupo:
                continue
            for chunk in _chunk_opportunity_list(grupo, titulo):
                await update.message.reply_text(
                    chunk, parse_mode="HTML", disable_web_page_preview=True
                )

    async def cmd_novas(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guarded(update):
            return
        from monitor_cartas.core.statuses import QuotaStatus

        novas = [
            c
            for c in repo.list_opportunities()
            if c.status == QuotaStatus.NEW
            and c.opportunity_class in REPORTABLE_CLASSES
            and passes_modality_limits(c, settings.financial) is not False
        ]
        if not novas:
            await update.message.reply_text("Nenhuma cota nova dentro dos tetos configurados.")
            return
        await _reply_grouped_by_modality(update, novas)

    async def cmd_melhores(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guarded(update):
            return
        limit = 20
        if context.args:
            try:
                limit = max(1, min(int(context.args[0]), 50))
            except ValueError:
                await update.message.reply_text("Uso: /melhores [quantidade] (padrão 20, máx 50)")
                return

        from monitor_cartas.core.statuses import QuotaStatus

        available_statuses = (QuotaStatus.NEW, QuotaStatus.AVAILABLE, QuotaStatus.SEEN)
        ranked = sorted(
            (
                c
                for c in repo.list_opportunities()
                if c.entry_percentage is not None
                and c.status in available_statuses
                and c.opportunity_class in REPORTABLE_CLASSES
                and passes_modality_limits(c, settings.financial) is not False
            ),
            key=lambda c: c.entry_percentage,
        )
        if not ranked:
            await update.message.reply_text(
                "Nenhuma oportunidade com preço calculado dentro dos tetos configurados."
            )
            return
        await _reply_grouped_by_modality(update, ranked[:limit])

    async def cmd_detalhes(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guarded(update):
            return
        if len(context.args) < 2:
            await update.message.reply_text("Uso: /detalhes <site> <id>")
            return
        site, source_id = context.args[0], context.args[1]
        cota = repo.get_quota(site, source_id)
        if cota is None:
            await update.message.reply_text("Cota não encontrada.")
            return
        await update.message.reply_text(format_alert_message(cota), parse_mode="HTML")

    async def cmd_silenciar(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guarded(update):
            return
        if len(context.args) < 2:
            await update.message.reply_text("Uso: /silenciar <site> <id>")
            return
        site, source_id = context.args[0], context.args[1]
        ok = repo.silence(site, source_id, reason="manual via telegram", when=datetime.now(timezone.utc))
        await update.message.reply_text("Silenciada." if ok else "Cota não encontrada.")

    async def cmd_reativar(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guarded(update):
            return
        if len(context.args) < 2:
            await update.message.reply_text("Uso: /reativar <site> <id>")
            return
        site, source_id = context.args[0], context.args[1]
        ok = repo.reactivate(site, source_id, when=datetime.now(timezone.utc))
        await update.message.reply_text("Reativada." if ok else "Cota não encontrada.")

    async def cmd_silenciadas(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guarded(update):
            return
        silenciadas = repo.list_silenced()
        if not silenciadas:
            await update.message.reply_text("Nenhuma cota silenciada.")
            return
        linhas = [f"{c.source_site} {c.source_id}" for c in silenciadas]
        await update.message.reply_text("\n".join(linhas))

    async def cmd_erros(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guarded(update):
            return
        erros = repo.list_errors()
        if not erros:
            await update.message.reply_text("Sem erros registrados.")
            return
        linhas = [f"{e['site']}: {e['error_count']} erro(s)" for e in erros]
        await update.message.reply_text("\n".join(linhas))

    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("novas", cmd_novas))
    app.add_handler(CommandHandler("melhores", cmd_melhores))
    app.add_handler(CommandHandler("detalhes", cmd_detalhes))
    app.add_handler(CommandHandler("silenciar", cmd_silenciar))
    app.add_handler(CommandHandler("reativar", cmd_reativar))
    app.add_handler(CommandHandler("silenciadas", cmd_silenciadas))
    app.add_handler(CommandHandler("erros", cmd_erros))

    return app
