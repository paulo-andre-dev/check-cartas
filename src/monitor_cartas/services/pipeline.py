"""Orquestra: coleta -> filtros -> consistência -> confiança -> persistência -> alertas.

Os adapters não sabem nada de banco, filtro ou alerta — essa é a única
camada que decide o que persistir e quando notificar.
"""
import logging
from datetime import datetime, timezone

from monitor_cartas.adapters.registry import build_adapters
from monitor_cartas.core.combinations import find_combinations
from monitor_cartas.core.confidence import compute_confidence
from monitor_cartas.core.consistency import check_consistency
from monitor_cartas.core.filters import apply_filters, passes_modality_limits
from monitor_cartas.core.models import AdapterRunResult, CotaContemplada
from monitor_cartas.core.statuses import InconsistencyLevel, OpportunityClass, QuotaStatus
from monitor_cartas.repositories.sqlite import QuotaRepository
from monitor_cartas.services.regulations import sync_administrators_from_quotas
from monitor_cartas.services.telegram import TelegramNotifier
from monitor_cartas.settings import Settings

logger = logging.getLogger("monitor_cartas.pipeline")


async def run_pipeline(
    settings: Settings, site_names: list[str], trigger: str = "manual"
) -> list[AdapterRunResult]:
    repo = QuotaRepository(settings.db_path)
    notifier = TelegramNotifier(settings)
    now = datetime.now(timezone.utc)
    run_id = repo.start_run(trigger, now)

    adapters = build_adapters(site_names, settings)
    results: list[AdapterRunResult] = []
    success = True

    try:
        for adapter in adapters:
            result = await adapter.run()
            results.append(result)
            repo.record_adapter_result(run_id, result)

            if not result.access.ok:
                logger.warning(
                    "Adapter %s bloqueado: %s (%s)",
                    adapter.name,
                    result.access.block_reason,
                    result.access.detail,
                )
                success = False
                continue

            seen_ids = set()
            for cota in result.quotas:
                seen_ids.add(cota.source_id)
                await _process_quota(cota, repo, settings, notifier)

            previously_seen = repo.sites_seen_ids(adapter.name)
            for missing_id in previously_seen - seen_ids:
                repo.mark_missing(
                    adapter.name, missing_id, settings.monitoring.missing_runs_before_removed, now
                )

            if getattr(adapter, "aclose", None):
                await adapter.aclose()

        all_quotas = repo.list_all()
        administrators = {q.administrator for q in all_quotas if q.administrator}
        sync_administrators_from_quotas(repo, administrators)

        combos = find_combinations(all_quotas, settings.financial, repo.get_administrator_rules())
        for combo in combos:
            import json

            repo.conn.execute(
                "INSERT INTO combinations (computed_at, administrator, rule_status, data_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    now.isoformat(),
                    combo.administrator,
                    combo.rule_status.value,
                    json.dumps(combo.model_dump(mode="json"), ensure_ascii=False),
                ),
            )
        repo.conn.commit()

    finally:
        repo.finish_run(run_id, datetime.now(timezone.utc), success)
        repo.close()

    return results


async def _process_quota(
    cota: CotaContemplada, repo: QuotaRepository, settings: Settings, notifier: TelegramNotifier
) -> None:
    cota = apply_filters(cota, settings.financial)
    cota = check_consistency(cota, settings.financial.consistency)
    cota = compute_confidence(cota)

    was_silenced = repo.is_silenced(cota.source_site, cota.source_id)
    if was_silenced:
        cota.status = QuotaStatus.SILENCED

    is_new = repo.get_quota(cota.source_site, cota.source_id) is None
    stored = repo.upsert_quota(cota)

    if was_silenced:
        return

    await _maybe_alert(stored, is_new, settings, notifier, repo)


async def _maybe_alert(
    cota: CotaContemplada,
    is_new: bool,
    settings: Settings,
    notifier: TelegramNotifier,
    repo: QuotaRepository,
) -> None:
    if not is_new:
        return

    # Site pode listar cota reservada/vendida/removida misturada com as
    # disponíveis (ex.: Grupo LuME mostra linha vermelha = reservada, sem
    # coluna de status separada) — nunca alertar isso como oportunidade.
    if cota.status not in (QuotaStatus.NEW, QuotaStatus.AVAILABLE, QuotaStatus.SEEN):
        logger.info(
            "Cota %s/%s com status %s — não é oportunidade disponível, sem alerta.",
            cota.source_site,
            cota.source_id,
            cota.status,
        )
        return

    if cota.inconsistency_level == InconsistencyLevel.CRITICAL:
        logger.info(
            "Cota %s/%s com inconsistência crítica — alerta de baixa confiança, não o normal.",
            cota.source_site,
            cota.source_id,
        )
        return

    limits_ok = passes_modality_limits(cota, settings.financial)
    if limits_ok is False:
        return

    if cota.opportunity_class in (
        OpportunityClass.NO_PRICE,
        OpportunityClass.INVALID_DATA,
        OpportunityClass.NORMAL,
        None,
    ):
        return

    await notifier.send_alert(cota, repo)
