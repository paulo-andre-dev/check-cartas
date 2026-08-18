import sqlite3
from datetime import datetime
from pathlib import Path

from monitor_cartas.core.models import AdapterRunResult, AdministratorRule, CotaContemplada
from monitor_cartas.core.statuses import QuotaStatus
from monitor_cartas.repositories.migrations import apply_schema

TRACKED_FIELDS = [
    "advertised_entry",
    "known_initial_disbursement",
    "current_installment",
    "outstanding_balance",
    "remaining_installments",
    "nominal_credit",
    "status",
]


class QuotaRepository:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        apply_schema(self.conn)

    def close(self) -> None:
        self.conn.close()

    # --- cotas -----------------------------------------------------------

    def get_quota(self, site: str, source_id: str) -> CotaContemplada | None:
        row = self.conn.execute(
            "SELECT data_json FROM quotas WHERE source_site=? AND source_id=?",
            (site, source_id),
        ).fetchone()
        if row is None:
            return None
        return CotaContemplada.model_validate_json(row["data_json"])

    def upsert_quota(self, cota: CotaContemplada) -> CotaContemplada:
        existing = self.get_quota(cota.source_site, cota.source_id)
        now = cota.collected_at

        if existing is None:
            cota.first_seen_at = now
            # NEW é um estado transitório de "primeira vez que vejo isso
            # disponível": só sobrepõe quando o adapter não relatou um status
            # definitivo (RESERVED/SOLD/REMOVED/UNAVAILABLE), que prevalece.
            if cota.status in (QuotaStatus.UNKNOWN, QuotaStatus.AVAILABLE):
                cota.status = QuotaStatus.NEW
        else:
            cota.first_seen_at = existing.first_seen_at
            if cota.status == QuotaStatus.UNKNOWN:
                cota.status = QuotaStatus.SEEN
            elif existing.status == QuotaStatus.NEW and cota.status == QuotaStatus.AVAILABLE:
                cota.status = QuotaStatus.SEEN
            self._record_status_change(cota, existing.status, cota.status, now)
            self._record_field_changes(cota, existing, now)

        cota.last_seen_at = now

        self.conn.execute(
            """
            INSERT INTO quotas (
                source_site, source_id, fingerprint, source_url, status, status_raw,
                modality, administrator, administrator_cnpj, "group", quota,
                data_json, first_seen_at, last_seen_at, missing_runs, silenced
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                COALESCE((SELECT silenced FROM quotas WHERE source_site=? AND source_id=?), 0))
            ON CONFLICT(source_site, source_id) DO UPDATE SET
                fingerprint=excluded.fingerprint,
                source_url=excluded.source_url,
                status=excluded.status,
                status_raw=excluded.status_raw,
                modality=excluded.modality,
                administrator=excluded.administrator,
                administrator_cnpj=excluded.administrator_cnpj,
                "group"=excluded."group",
                quota=excluded.quota,
                data_json=excluded.data_json,
                last_seen_at=excluded.last_seen_at,
                missing_runs=0
            """,
            (
                cota.source_site,
                cota.source_id,
                _fingerprint_of(cota),
                cota.source_url,
                cota.status.value,
                cota.status_raw,
                cota.modality,
                cota.administrator,
                cota.administrator_cnpj,
                cota.group,
                cota.quota,
                cota.model_dump_json(),
                cota.first_seen_at.isoformat(),
                cota.last_seen_at.isoformat(),
                cota.source_site,
                cota.source_id,
            ),
        )

        self.conn.execute(
            "INSERT INTO quota_observations (source_site, source_id, observed_at, data_json) "
            "VALUES (?, ?, ?, ?)",
            (cota.source_site, cota.source_id, now.isoformat(), cota.model_dump_json()),
        )
        self.conn.commit()
        return cota

    def _record_status_change(
        self, cota: CotaContemplada, old_status: QuotaStatus, new_status: QuotaStatus, when: datetime
    ) -> None:
        if old_status == new_status:
            return
        self.conn.execute(
            "INSERT INTO quota_status_history (source_site, source_id, changed_at, old_status, new_status) "
            "VALUES (?, ?, ?, ?, ?)",
            (cota.source_site, cota.source_id, when.isoformat(), old_status.value, new_status.value),
        )

    def _record_field_changes(
        self, cota: CotaContemplada, existing: CotaContemplada, when: datetime
    ) -> None:
        for field in TRACKED_FIELDS:
            old_value = getattr(existing, field)
            new_value = getattr(cota, field)
            if old_value != new_value:
                self.conn.execute(
                    "INSERT INTO quota_price_history "
                    "(source_site, source_id, changed_at, field, old_value, new_value) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        cota.source_site,
                        cota.source_id,
                        when.isoformat(),
                        field,
                        str(old_value) if old_value is not None else None,
                        str(new_value) if new_value is not None else None,
                    ),
                )

    def mark_missing(self, site: str, source_id: str, threshold: int, when: datetime) -> None:
        row = self.conn.execute(
            "SELECT missing_runs, status, data_json FROM quotas WHERE source_site=? AND source_id=?",
            (site, source_id),
        ).fetchone()
        if row is None:
            return
        missing_runs = row["missing_runs"] + 1
        new_status = row["status"]
        data_json = row["data_json"]

        if missing_runs >= threshold and row["status"] != QuotaStatus.REMOVED.value:
            new_status = QuotaStatus.REMOVED.value
            self.conn.execute(
                "INSERT INTO quota_status_history (source_site, source_id, changed_at, old_status, new_status) "
                "VALUES (?, ?, ?, ?, ?)",
                (site, source_id, when.isoformat(), row["status"], new_status),
            )
            cota = CotaContemplada.model_validate_json(data_json)
            cota.status = QuotaStatus.REMOVED
            data_json = cota.model_dump_json()

        self.conn.execute(
            "UPDATE quotas SET missing_runs=?, status=?, data_json=? WHERE source_site=? AND source_id=?",
            (missing_runs, new_status, data_json, site, source_id),
        )
        self.conn.commit()

    def sites_seen_ids(self, site: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT source_id FROM quotas WHERE source_site=? AND status != ?",
            (site, QuotaStatus.REMOVED.value),
        ).fetchall()
        return {r["source_id"] for r in rows}

    def list_opportunities(
        self, max_entry_percentage=None, exclude_silenced: bool = True
    ) -> list[CotaContemplada]:
        rows = self.conn.execute("SELECT data_json, silenced FROM quotas").fetchall()
        result = []
        for row in rows:
            if exclude_silenced and row["silenced"]:
                continue
            cota = CotaContemplada.model_validate_json(row["data_json"])
            if max_entry_percentage is not None and (
                cota.entry_percentage is None or cota.entry_percentage > max_entry_percentage
            ):
                continue
            result.append(cota)
        return result

    def list_all(self) -> list[CotaContemplada]:
        rows = self.conn.execute("SELECT data_json FROM quotas").fetchall()
        return [CotaContemplada.model_validate_json(r["data_json"]) for r in rows]

    # --- silenciamento ----------------------------------------------------

    def _set_status_everywhere(self, site: str, source_id: str, status: QuotaStatus) -> int:
        row = self.conn.execute(
            "SELECT data_json FROM quotas WHERE source_site=? AND source_id=?", (site, source_id)
        ).fetchone()
        if row is None:
            return 0
        cota = CotaContemplada.model_validate_json(row["data_json"])
        cota.status = status
        cur = self.conn.execute(
            "UPDATE quotas SET silenced=?, status=?, data_json=? WHERE source_site=? AND source_id=?",
            (
                1 if status == QuotaStatus.SILENCED else 0,
                status.value,
                cota.model_dump_json(),
                site,
                source_id,
            ),
        )
        return cur.rowcount

    def silence(self, site: str, source_id: str, reason: str | None, when: datetime) -> bool:
        if self._set_status_everywhere(site, source_id, QuotaStatus.SILENCED) == 0:
            return False
        self.conn.execute(
            "INSERT INTO silenced_quotas (source_site, source_id, silenced_at, reason) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(source_site, source_id) DO UPDATE SET "
            "silenced_at=excluded.silenced_at, reactivated_at=NULL, reason=excluded.reason",
            (site, source_id, when.isoformat(), reason),
        )
        self.conn.commit()
        return True

    def reactivate(self, site: str, source_id: str, when: datetime) -> bool:
        if self._set_status_everywhere(site, source_id, QuotaStatus.SEEN) == 0:
            return False
        self.conn.execute(
            "UPDATE silenced_quotas SET reactivated_at=? WHERE source_site=? AND source_id=?",
            (when.isoformat(), site, source_id),
        )
        self.conn.commit()
        return True

    def is_silenced(self, site: str, source_id: str) -> bool:
        row = self.conn.execute(
            "SELECT silenced FROM quotas WHERE source_site=? AND source_id=?", (site, source_id)
        ).fetchone()
        return bool(row and row["silenced"])

    def list_silenced(self) -> list[CotaContemplada]:
        rows = self.conn.execute("SELECT data_json FROM quotas WHERE silenced=1").fetchall()
        return [CotaContemplada.model_validate_json(r["data_json"]) for r in rows]

    # --- administradoras ----------------------------------------------------

    def upsert_administrator_rule(self, rule: AdministratorRule) -> None:
        self.conn.execute(
            "INSERT INTO administrator_rules (administrator, data_json, validation_status, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(administrator) DO UPDATE SET "
            "data_json=excluded.data_json, validation_status=excluded.validation_status, "
            "updated_at=excluded.updated_at",
            (
                rule.administrator,
                rule.model_dump_json(),
                rule.validation_status.value,
                datetime.now().isoformat(),
            ),
        )
        self.conn.commit()

    def get_administrator_rules(self) -> dict[str, AdministratorRule]:
        rows = self.conn.execute("SELECT data_json FROM administrator_rules").fetchall()
        return {
            (r := AdministratorRule.model_validate_json(row["data_json"])).administrator: r
            for row in rows
        }

    # --- runs / auditoria ----------------------------------------------------

    def start_run(self, trigger: str, when: datetime) -> int:
        cur = self.conn.execute(
            "INSERT INTO scraper_runs (started_at, trigger, success) VALUES (?, ?, 0)",
            (when.isoformat(), trigger),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, when: datetime, success: bool) -> None:
        self.conn.execute(
            "UPDATE scraper_runs SET finished_at=?, success=? WHERE id=?",
            (when.isoformat(), int(success), run_id),
        )
        self.conn.commit()

    def last_successful_run(self) -> datetime | None:
        row = self.conn.execute(
            "SELECT finished_at FROM scraper_runs WHERE success=1 ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        if row is None or row["finished_at"] is None:
            return None
        return datetime.fromisoformat(row["finished_at"])

    def record_adapter_result(self, run_id: int, result: AdapterRunResult) -> None:
        import json

        self.conn.execute(
            "INSERT INTO adapter_results (run_id, site, started_at, finished_at, access_ok, "
            "block_reason, listing_count, processed_count, error_count, errors_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                result.site,
                result.started_at.isoformat(),
                result.finished_at.isoformat(),
                int(result.access.ok),
                result.access.block_reason.value if result.access.block_reason else None,
                result.listing_count,
                result.processed_count,
                result.error_count,
                json.dumps(result.errors, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    def list_errors(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM adapter_results WHERE error_count > 0 OR access_ok = 0 "
            "ORDER BY finished_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def record_evidence(
        self,
        site: str,
        source_id: str,
        collected_at: datetime,
        adapter_version: str | None,
        http_status: int | None,
        evidence_path: str,
        content_hash: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO raw_evidence (source_site, source_id, collected_at, adapter_version, "
            "http_status, evidence_path, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                site,
                source_id,
                collected_at.isoformat(),
                adapter_version,
                http_status,
                evidence_path,
                content_hash,
            ),
        )
        self.conn.commit()


def _fingerprint_of(cota: CotaContemplada) -> str | None:
    from monitor_cartas.core.dedupe import fingerprint

    return fingerprint(cota)
