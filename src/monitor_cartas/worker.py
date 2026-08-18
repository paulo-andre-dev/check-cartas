"""Entrypoint pra rodar em plataforma tipo Railway: processo único e
contínuo, sem depender de cron do sistema operacional.

Sobe duas coisas em paralelo:
  - o bot do Telegram (comandos /status, /silenciar etc.), sempre ativo;
  - um ciclo que roda o pipeline de coleta a cada CYCLE_INTERVAL_SECONDS
    (padrão 24h), do mesmo jeito que os outros bots do autor nesta conta
    Railway usam CYCLE_INTERVAL_SECONDS pra pausar entre execuções.

Uso local: python -m monitor_cartas.worker
"""
import asyncio
import logging

from monitor_cartas.logging_config import configure_logging
from monitor_cartas.repositories.sqlite import QuotaRepository
from monitor_cartas.services.pipeline import run_pipeline
from monitor_cartas.settings import load_settings

logger = logging.getLogger("monitor_cartas.worker")


async def _collection_loop() -> None:
    settings = load_settings()
    interval = settings.monitoring.cycle_interval_seconds

    while True:
        sites = settings.active_sites
        if not sites:
            logger.warning("Nenhum site ativo em sites.active/SITES_ACTIVE — ciclo pulado.")
        else:
            try:
                results = await run_pipeline(settings, sites, trigger="worker")
                for r in results:
                    logger.info(
                        "%s: access_ok=%s processados=%s/%s erros=%s",
                        r.site,
                        r.access.ok,
                        r.processed_count,
                        r.listing_count,
                        r.error_count,
                    )
            except Exception:
                logger.exception("Ciclo de coleta falhou — tenta de novo no próximo ciclo.")

        logger.info("Ciclo concluído. Próxima coleta em %ss.", interval)
        await asyncio.sleep(interval)


async def _telegram_loop() -> None:
    settings = load_settings()
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN não configurado — bot de comandos não vai subir.")
        return

    from monitor_cartas.services.telegram import build_bot_application

    repo = QuotaRepository(settings.db_path)
    app = build_bot_application(settings, repo)

    async with app:
        # Application.initialize() (chamado no __aenter__) não dispara
        # post_init sozinho — isso só acontece dentro de run_polling()/
        # run_webhook(), que a gente não usa aqui (ciclo manual pra rodar
        # em paralelo com a coleta). Sem essa linha, o menu de comandos
        # (set_my_commands) nunca é registrado no Telegram.
        if app.post_init:
            await app.post_init(app)
        await app.start()
        await app.updater.start_polling()
        try:
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()
            repo.close()


async def main() -> None:
    settings = load_settings()
    configure_logging(settings.logs_dir)
    logger.info(
        "Worker iniciado. Sites ativos: %s. Ciclo: %ss.",
        settings.active_sites,
        settings.monitoring.cycle_interval_seconds,
    )

    await asyncio.gather(_collection_loop(), _telegram_loop())


if __name__ == "__main__":
    asyncio.run(main())
