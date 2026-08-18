"""Alertas e bot de comandos do Telegram.

Envio de alerta (send_alert) e o worker de comandos (/silenciar etc.) são
processos separados por design: o scraper roda 1x/dia via cron, o bot de
comandos precisa ficar de pé continuamente para responder a qualquer hora.

Nunca logar o token. Comandos só são aceitos de chat_ids em
TELEGRAM_ALLOWED_CHAT_IDS.
"""
import logging
from datetime import datetime, timezone

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


def format_alert_message(cota: CotaContemplada) -> str:
    classe = OPPORTUNITY_LABELS.get(cota.opportunity_class, "—")
    linhas = [
        f"<b>{classe}</b> — {cota.source_site}",
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


def build_bot_application(settings: Settings, repo: QuotaRepository):
    """Monta a Application do python-telegram-bot com os comandos do projeto."""
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não configurado em .env")

    app = Application.builder().token(settings.telegram_bot_token).build()

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

    async def cmd_novas(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guarded(update):
            return
        from monitor_cartas.core.statuses import QuotaStatus

        novas = [c for c in repo.list_opportunities() if c.status == QuotaStatus.NEW]
        if not novas:
            await update.message.reply_text("Nenhuma cota nova.")
            return
        for cota in novas[:10]:
            await update.message.reply_text(format_alert_message(cota), parse_mode="HTML")

    async def cmd_melhores(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guarded(update):
            return
        ranked = sorted(
            (c for c in repo.list_opportunities() if c.entry_percentage is not None),
            key=lambda c: c.entry_percentage,
        )
        if not ranked:
            await update.message.reply_text("Nenhuma oportunidade com preço calculado.")
            return
        for cota in ranked[:5]:
            await update.message.reply_text(format_alert_message(cota), parse_mode="HTML")

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
