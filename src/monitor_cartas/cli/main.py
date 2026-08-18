import argparse
import asyncio
import sys
from datetime import datetime, timezone

from monitor_cartas.adapters.registry import ADAPTER_CLASSES
from monitor_cartas.logging_config import configure_logging
from monitor_cartas.repositories.sqlite import QuotaRepository
from monitor_cartas.services.pipeline import run_pipeline
from monitor_cartas.settings import load_settings


def _sites_from_arg(site_arg: str | None, settings) -> list[str]:
    if site_arg is None or site_arg == "all":
        return list(ADAPTER_CLASSES.keys()) if site_arg == "all" else settings.active_sites
    return [site_arg]


def cmd_run(args, settings) -> int:
    sites = _sites_from_arg(args.site, settings)
    if not sites:
        print("Nenhum site ativo em config.yaml (sites.active) nem informado via --site.")
        return 1
    results = asyncio.run(run_pipeline(settings, sites, trigger="cli"))
    total_processed = sum(r.processed_count for r in results)
    total_errors = sum(r.error_count for r in results)
    for r in results:
        status = "OK" if r.access.ok else f"BLOQUEADO ({r.access.block_reason})"
        print(f"{r.site}: {status} — {r.processed_count}/{r.listing_count} processados, {r.error_count} erros")
    print(f"\nTotal: {total_processed} cotas processadas, {total_errors} erros.")
    return 0 if total_errors == 0 else 1


def cmd_login(args, settings) -> int:
    site = args.site
    session_path = settings.sessions_dir / site / "storage_state.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)

    if site == "contemplei":
        print("A Contemplei não exige login para o estoque público consultado por este monitor.")
        return 0

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright não instalado nesta venv. Rode: pip install playwright && playwright install")
        return 1

    print(f"Abrindo navegador para login manual em {site}. Faça login e feche a aba quando terminar.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"https://{site}")
        input("Pressione ENTER depois de concluir o login manualmente... ")
        context.storage_state(path=str(session_path))
        browser.close()

    print(f"Sessão salva em {session_path}")
    return 0


def cmd_status(args, settings) -> int:
    repo = QuotaRepository(settings.db_path)
    last_run = repo.last_successful_run()
    print(f"Última execução bem-sucedida: {last_run.isoformat() if last_run else 'nunca'}")
    if last_run:
        age_hours = (datetime.now(timezone.utc) - last_run.replace(tzinfo=timezone.utc)).total_seconds() / 3600
        if age_hours > settings.monitoring.alert_if_last_success_older_than_hours:
            print(f"ATENÇÃO: última execução há {age_hours:.1f}h, acima do limite configurado.")
    repo.close()
    return 0


def cmd_list_errors(args, settings) -> int:
    repo = QuotaRepository(settings.db_path)
    erros = repo.list_errors()
    if not erros:
        print("Sem erros registrados.")
    for e in erros:
        print(f"{e['site']} — access_ok={e['access_ok']} block={e['block_reason']} erros={e['error_count']}")
    repo.close()
    return 0


def cmd_list_opportunities(args, settings) -> int:
    from monitor_cartas.core.statuses import OpportunityClass

    reportable = {
        OpportunityClass.GOLD,
        OpportunityClass.EXCEPTIONAL,
        OpportunityClass.VERY_GOOD,
        OpportunityClass.GOOD,
    }
    repo = QuotaRepository(settings.db_path)
    opps = [c for c in repo.list_opportunities() if c.opportunity_class in reportable]
    opps.sort(key=lambda c: c.entry_percentage)
    for c in opps:
        pct = f"{c.entry_percentage:.1%}"
        desembolso = c.known_initial_disbursement
        taxas = "com taxas desconhecidas" if c.has_unknown_fees else "taxas completas"
        print(
            f"[{c.opportunity_class}] {c.source_site}/{c.source_id} — {c.administrator} — "
            f"entrada {pct} (desembolso R$ {desembolso}, {taxas}) — {c.source_url}"
        )
    repo.close()
    return 0


def cmd_list_combinations(args, settings) -> int:
    import json

    repo = QuotaRepository(settings.db_path)
    rows = repo.conn.execute(
        "SELECT * FROM combinations ORDER BY computed_at DESC LIMIT 50"
    ).fetchall()
    for row in rows:
        data = json.loads(row["data_json"])
        print(
            f"[{row['rule_status']}] {row['administrator']} — crédito total "
            f"R$ {data['total_credit']} — entrada agregada {float(data['aggregate_entry_percentage']):.1%}"
        )
    repo.close()
    return 0


def cmd_silence(args, settings) -> int:
    repo = QuotaRepository(settings.db_path)
    ok = repo.silence(args.site, args.id, reason="cli", when=datetime.now(timezone.utc))
    print("Silenciada." if ok else "Cota não encontrada.")
    repo.close()
    return 0 if ok else 1


def cmd_reactivate(args, settings) -> int:
    repo = QuotaRepository(settings.db_path)
    ok = repo.reactivate(args.site, args.id, when=datetime.now(timezone.utc))
    print("Reativada." if ok else "Cota não encontrada.")
    repo.close()
    return 0 if ok else 1


def cmd_reprocess(args, settings) -> int:
    from monitor_cartas.core.confidence import compute_confidence
    from monitor_cartas.core.consistency import check_consistency
    from monitor_cartas.core.filters import apply_filters

    repo = QuotaRepository(settings.db_path)
    cota = repo.get_quota(args.site, args.id)
    if cota is None:
        print("Cota não encontrada.")
        repo.close()
        return 1
    cota = apply_filters(cota, settings.financial)
    cota = check_consistency(cota, settings.financial.consistency)
    cota = compute_confidence(cota)
    repo.upsert_quota(cota)
    print(f"Reprocessada: {cota.opportunity_class}, entrada {cota.entry_percentage}")
    repo.close()
    return 0


def cmd_telegram(args, settings) -> int:
    from monitor_cartas.services.telegram import build_bot_application

    repo = QuotaRepository(settings.db_path)
    app = build_bot_application(settings, repo)
    print("Bot do Telegram rodando. Ctrl+C para encerrar.")
    app.run_polling()
    repo.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="monitor_cartas.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--site", default=None, help="nome do site ou 'all'")

    p_login = sub.add_parser("login")
    p_login.add_argument("site")

    sub.add_parser("status")
    sub.add_parser("list-errors")
    sub.add_parser("list-opportunities")
    sub.add_parser("list-combinations")

    p_silence = sub.add_parser("silence")
    p_silence.add_argument("site")
    p_silence.add_argument("id")

    p_reactivate = sub.add_parser("reactivate")
    p_reactivate.add_argument("site")
    p_reactivate.add_argument("id")

    p_reprocess = sub.add_parser("reprocess")
    p_reprocess.add_argument("site")
    p_reprocess.add_argument("id")

    sub.add_parser("telegram")

    args = parser.parse_args()
    settings = load_settings()
    configure_logging(settings.logs_dir)

    handlers = {
        "run": cmd_run,
        "login": cmd_login,
        "status": cmd_status,
        "list-errors": cmd_list_errors,
        "list-opportunities": cmd_list_opportunities,
        "list-combinations": cmd_list_combinations,
        "silence": cmd_silence,
        "reactivate": cmd_reactivate,
        "reprocess": cmd_reprocess,
        "telegram": cmd_telegram,
    }
    sys.exit(handlers[args.command](args, settings))


if __name__ == "__main__":
    main()
