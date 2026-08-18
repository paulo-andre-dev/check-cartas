"""Adapter da vitrine pública de cartas contempladas da Capitalizza."""

from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

from monitor_cartas.adapters.base import SiteAdapter
from monitor_cartas.core.models import AccessResult, CotaContemplada
from monitor_cartas.core.money import parse_brl_to_decimal
from monitor_cartas.core.statuses import AdapterAccessBlockReason, QuotaStatus
from monitor_cartas.services.evidence import save_json_evidence
from monitor_cartas.settings import Settings

ADAPTER_VERSION = "1.0.0"
LISTING_URL = "https://contempladas.capitalizza.com.br/"


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_cell = False
        self.cell_parts: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("td", "th"):
            self.in_cell = True
            self.cell_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.in_cell:
            self.current_row.append(" ".join("".join(self.cell_parts).split()))
            self.in_cell = False
        elif tag == "tr" and self.current_row:
            self.rows.append(self.current_row)
            self.current_row = []


def parse_listing(html: str) -> list[dict]:
    parser = _TableParser()
    parser.feed(html)
    rows = []
    for cells in parser.rows:
        if len(cells) != 11 or not cells[0].strip().isdigit():
            continue
        rows.append(
            {
                "id": cells[0],
                "modality": cells[1],
                "credit": cells[2],
                "entry": cells[3],
                "remaining_installments": cells[4],
                "installment": cells[5],
                "outstanding_balance": cells[6],
                "common_fund": cells[7],
                "guarantee_reference": cells[8],
                "administrator": cells[9],
                "status": cells[10],
            }
        )
    return rows


class CapitalizzaAdapter(SiteAdapter):
    name = "capitalizza"
    base_url = "https://contempladas.capitalizza.com.br"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        self._cache: dict[str, dict] = {}
        self._evidence_path: str | None = None

    async def _fetch(self) -> str:
        response = await self._client.get(LISTING_URL)
        response.raise_for_status()
        return response.text

    async def validate_access(self) -> AccessResult:
        checked_at = datetime.now(timezone.utc)
        try:
            rows = parse_listing(await self._fetch())
        except httpx.HTTPStatusError as exc:
            return AccessResult(ok=False, block_reason=AdapterAccessBlockReason.HTTP_ERROR,
                                detail=f"HTTP {exc.response.status_code}", checked_at=checked_at)
        except httpx.RequestError as exc:
            return AccessResult(ok=False, block_reason=AdapterAccessBlockReason.TIMEOUT,
                                detail=str(exc), checked_at=checked_at)
        if not rows:
            return AccessResult(ok=False, block_reason=AdapterAccessBlockReason.STOCK_NOT_RENDERED,
                                detail="Tabela pública sem linhas reconhecíveis.", checked_at=checked_at)
        return AccessResult(ok=True, checked_at=checked_at)

    async def collect_listing_urls(self) -> list[str]:
        rows = parse_listing(await self._fetch())
        collected_at = datetime.now(timezone.utc)
        path, _ = save_json_evidence(
            self.settings.evidence_dir, self.name, "listagem", collected_at, {"rows": rows}
        )
        self._evidence_path = str(path)
        urls = []
        for row in rows:
            if row["status"].casefold() != "disponível".casefold():
                continue
            self._cache[row["id"]] = row
            urls.append(f"capitalizza-item://{row['id']}")
        return urls

    async def collect_quota(self, url: str) -> CotaContemplada:
        source_id = url.removeprefix("capitalizza-item://")
        row = self._cache[source_id]
        try:
            remaining = int(row["remaining_installments"])
        except (TypeError, ValueError):
            remaining = None
        return CotaContemplada(
            source_site=self.name,
            source_id=source_id,
            source_url=LISTING_URL,
            collected_at=datetime.now(timezone.utc),
            status=QuotaStatus.AVAILABLE,
            status_raw=row["status"],
            is_contemplated=True,
            modality=row["modality"],
            administrator=row["administrator"],
            nominal_credit=parse_brl_to_decimal(row["credit"]),
            advertised_entry=parse_brl_to_decimal(row["entry"]),
            seller_price=parse_brl_to_decimal(row["entry"]),
            outstanding_balance=parse_brl_to_decimal(row["outstanding_balance"]),
            remaining_installments=remaining,
            current_installment=parse_brl_to_decimal(row["installment"]),
            raw_evidence_path=self._evidence_path,
            adapter_version=ADAPTER_VERSION,
            extraction_notes=[
                "Tabela pública informa saldo devedor, mas não discrimina no snapshot "
                "a taxa de transferência e outras taxas; confirmar antes de negociar.",
                f"Fundo comum publicado: {row['common_fund'] or 'não informado'}; "
                f"referência de garantia: {row['guarantee_reference'] or 'não informada'}.",
            ],
        )

    async def aclose(self) -> None:
        await self._client.aclose()
