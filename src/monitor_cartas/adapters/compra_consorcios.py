"""Adapter da Compra Consórcios (compraconsorcios.com.br).

Tabela HTML (plugin JetEngine do WordPress) renderizada no cliente, sem
login, cada linha já com link direto pro post da cota. Colunas
confirmadas no DOM real:

  Código | Segmento | Administradora | Valor do Crédito | Valor de Entrada
  | Contemplado | Valor Mensal | % pago | Status | Visualizar (link)

Sem saldo devedor nem taxas discriminadas publicadas.
"""
from datetime import datetime, timezone

from playwright.async_api import async_playwright

from monitor_cartas.adapters.base import SiteAdapter
from monitor_cartas.core.models import AccessResult, CotaContemplada
from monitor_cartas.core.money import parse_brl_to_decimal
from monitor_cartas.core.statuses import AdapterAccessBlockReason, QuotaStatus
from monitor_cartas.services.evidence import save_json_evidence
from monitor_cartas.settings import Settings

ADAPTER_VERSION = "1.0.0"
LISTING_URL = "https://www.compraconsorcios.com.br/cotas-disponiveis/"

_EXTRACT_JS = """
() => {
  const trs = Array.from(document.querySelectorAll('table.jet-dynamic-table tbody tr'));
  return trs.map(tr => {
    const tds = Array.from(tr.children);
    const cell = i => (tds[i] ? tds[i].textContent.trim() : '');
    const linkEl = tr.querySelector('a');
    return {
      codigo: cell(0),
      segmento: cell(1),
      administradora: cell(2),
      credito: cell(3),
      entrada: cell(4),
      contemplado: cell(5),
      valor_mensal: cell(6),
      pct_pago: cell(7),
      status: cell(8),
      link: linkEl ? linkEl.href : null,
    };
  }).filter(r => r.codigo && r.codigo !== 'Código');
}
"""


class CompraConsorciosAdapter(SiteAdapter):
    name = "compra_consorcios"
    base_url = "https://www.compraconsorcios.com.br"
    requires_authentication = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache: dict[str, dict] = {}
        self._evidence_path: str | None = None

    async def validate_access(self) -> AccessResult:
        checked_at = datetime.now(timezone.utc)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page()
                resp = await page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=25000)
                status_ok = resp is not None and resp.status < 400
                await browser.close()
        except Exception as exc:
            return AccessResult(
                ok=False,
                block_reason=AdapterAccessBlockReason.TIMEOUT,
                detail=str(exc)[:300],
                checked_at=checked_at,
            )
        if not status_ok:
            return AccessResult(
                ok=False,
                block_reason=AdapterAccessBlockReason.HTTP_ERROR,
                detail=f"HTTP {resp.status if resp else '?'}",
                checked_at=checked_at,
            )
        return AccessResult(ok=True, checked_at=checked_at)

    async def collect_listing_urls(self) -> list[str]:
        collected_at = datetime.now(timezone.utc)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.goto(LISTING_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1500)
            rows = await page.evaluate(_EXTRACT_JS)
            await browser.close()

        path, _hash = save_json_evidence(
            self.settings.evidence_dir, self.name, "listagem", collected_at, {"rows": rows}
        )
        self._evidence_path = str(path)

        urls = []
        for row in rows:
            self._cache[row["codigo"]] = row
            urls.append(f"compra-row://{row['codigo']}")
        return urls

    async def collect_quota(self, url: str) -> CotaContemplada:
        key = url.removeprefix("compra-row://")
        return self._row_to_cota(self._cache[key])

    def _row_to_cota(self, row: dict) -> CotaContemplada:
        notes = [
            "Saldo devedor não é publicado — consistência não é aplicável.",
            "Nenhuma taxa é discriminada separadamente da entrada — tratadas "
            "como desconhecidas.",
            f"'% pago' publicado pela fonte: {row.get('pct_pago')} — não usado no "
            "cálculo, mantido só como referência.",
        ]

        status_raw = (row.get("status") or "").strip().lower()
        is_available = status_raw.startswith("dispon")

        return CotaContemplada(
            source_site=self.name,
            source_id=row["codigo"],
            source_url=row.get("link") or LISTING_URL,
            collected_at=datetime.now(timezone.utc),
            status=QuotaStatus.AVAILABLE if is_available else QuotaStatus.UNAVAILABLE,
            status_raw=row.get("status"),
            is_contemplated=(row.get("contemplado") or "").strip().lower().startswith("contempl"),
            modality=row.get("segmento"),
            administrator=row.get("administradora") or None,
            group=None,
            quota=row.get("codigo"),
            nominal_credit=parse_brl_to_decimal(row.get("credito")),
            advertised_entry=parse_brl_to_decimal(row.get("entrada")),
            seller_price=parse_brl_to_decimal(row.get("entrada")),
            current_installment=parse_brl_to_decimal(row.get("valor_mensal")),
            raw_evidence_path=self._evidence_path,
            adapter_version=ADAPTER_VERSION,
            extraction_notes=notes,
        )
