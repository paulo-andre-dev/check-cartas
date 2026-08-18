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
from monitor_cartas.core.models import AccessResult, AdapterRunResult, CotaContemplada
from monitor_cartas.core.statuses import (
    AdapterAccessBlockReason,
    InconsistencyLevel,
    OpportunityClass,
    QuotaStatus,
)
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
    run_status = "SUCCESS"

    try:
        for adapter in adapters:
            try:
                try:
                    result = await adapter.run()
                except Exception as exc:
                    finished_at = datetime.now(timezone.utc)
                    result = AdapterRunResult(
                        site=adapter.name,
                        started_at=finished_at,
                        finished_at=finished_at,
                        access=AccessResult(
                            ok=False,
                            block_reason=AdapterAccessBlockReason.TECHNICAL_BLOCK,
                            detail=str(exc),
                            checked_at=finished_at,
                        ),
                        error_count=1,
                        snapshot_complete=False,
                        snapshot_detail=f"Falha inesperada do adapter: {exc}",
                        errors=[str(exc)],
                    )
                    results.append(result)
                    repo.record_adapter_result(run_id, result)
                    run_status = "PARTIAL"
                    logger.exception("Adapter %s falhou inesperadamente.", adapter.name)
                    continue

                previous_count = repo.latest_complete_listing_count(adapter.name)
                if (
                    result.access.ok
                    and result.snapshot_complete
                    and previous_count
                    and result.listing_count
                    < previous_count * settings.monitoring.minimum_snapshot_ratio
                ):
                    result.snapshot_complete = False
                    result.snapshot_detail = (
                        f"Queda anormal de estoque: {result.listing_count} itens contra "
                        f"{previous_count} no último snapshot completo."
                    )

                results.append(result)
                repo.record_adapter_result(run_id, result)

                if not result.access.ok:
                    logger.warning(
                        "Adapter %s bloqueado: %s (%s)",
                        adapter.name,
                        result.access.block_reason,
                        result.access.detail,
                    )
                    run_status = "PARTIAL"
                    continue

                if result.error_count or not result.snapshot_complete:
                    run_status = "PARTIAL"
                    logger.warning(
                        "Snapshot incompleto de %s: %s",
                        adapter.name,
                        result.snapshot_detail or f"{result.error_count} erro(s)",
                    )

                seen_ids = set()
                for cota in result.quotas:
                    seen_ids.add(cota.source_id)
                    await _process_quota(cota, repo, settings, notifier)

                if result.snapshot_complete:
                    previously_seen = repo.sites_seen_ids(adapter.name)
                    for missing_id in previously_seen - seen_ids:
                        repo.mark_missing(
                            adapter.name,
                            missing_id,
                            settings.monitoring.missing_runs_before_removed,
                            now,
                        )
            finally:
                if getattr(adapter, "aclose", None):
                    try:
                        await adapter.aclose()
                    except Exception:
                        run_status = "PARTIAL"
                        logger.exception("Falha ao fechar recursos do adapter %s.", adapter.name)

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

    except Exception:
        run_status = "FAILED"
        raise
    finally:
        repo.finish_run(
            run_id,
            datetime.now(timezone.utc),
            run_status == "SUCCESS",
            status=run_status,
        )
        repo.close()

    return results


async def _process_quota(
    cota: CotaContemplada, repo: QuotaRepository, settings: Settings, notifier: TelegramNotifier
) -> None:
    policy = settings.site_policy(cota.source_site)
    cota.transaction_status = policy.transaction_status
    cota.payment_protection = policy.payment_protection
    cota = apply_filters(cota, settings.financial)
    cota = check_consistency(cota, settings.financial.consistency)
    cota = compute_confidence(cota)

    existing = repo.get_quota(cota.source_site, cota.source_id)
    delivered_to = repo.alerted_chat_ids(cota.source_site, cota.source_id)
    delivery_pending = bool(settings.telegram_allowed_chat_ids) and not set(
        settings.telegram_allowed_chat_ids
    ).issubset(delivered_to)
    became_eligible = (
        existing is None
        or not _is_alert_eligible(existing, settings)
        or delivery_pending
    )
    was_silenced = repo.is_silenced(cota.source_site, cota.source_id)
    if was_silenced:
        cota.status = QuotaStatus.SILENCED

    stored = repo.upsert_quota(cota)

    if was_silenced:
        return

    try:
        await _maybe_alert(stored, became_eligible, settings, notifier, repo)
    except Exception:
        # A cota já foi persistida e a tabela alerts não registra entregas
        # que falharam. A próxima execução detectará delivery_pending e
        # tentará novamente sem interromper a coleta dos outros sites.
        logger.exception(
            "Falha ao enviar alerta de %s/%s; entrega será tentada novamente.",
            cota.source_site,
            cota.source_id,
        )


def _is_alert_eligible(cota: CotaContemplada, settings: Settings) -> bool:
    if not settings.site_policy(cota.source_site).alert:
        return False
    if cota.status not in (QuotaStatus.NEW, QuotaStatus.AVAILABLE, QuotaStatus.SEEN):
        return False
    if cota.inconsistency_level == InconsistencyLevel.CRITICAL:
        return False
    if passes_modality_limits(cota, settings.financial) is False:
        return False
    return cota.opportunity_class not in (
        OpportunityClass.NO_PRICE,
        OpportunityClass.INVALID_DATA,
        OpportunityClass.NORMAL,
        None,
    )


async def _maybe_alert(
    cota: CotaContemplada,
    became_eligible: bool,
    settings: Settings,
    notifier: TelegramNotifier,
    repo: QuotaRepository,
) -> None:
    if not became_eligible:
        return

    if not settings.site_policy(cota.source_site).alert:
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
