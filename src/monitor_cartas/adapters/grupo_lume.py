"""Adapter do Grupo LuME (cartascontempladas.com.br).

Tabela HTML renderizada no cliente, sem login. Colunas confirmadas
inspecionando o DOM real:

  0 checkbox "juntar cotas"   5 entrada
  1 (vazio)                   6 parcelas restantes
  2 segmento                  7 valor da parcela (sem prefixo "R$")
  3 administradora            8 próximo vencimento
  4 valor do crédito          9/10 link "Ver" / "Quero saber mais" ->
                                 informacao-da-carta/?cota=<id>

A própria listagem já filtra por disponibilidade (não há coluna de status
com valores distintos) — tratamos toda linha retornada como disponível,
registrado nas extraction_notes. Sem saldo devedor publicado.
"""
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

from monitor_cartas.adapters.base import SiteAdapter
from monitor_cartas.core.models import AccessResult, CotaContemplada
from monitor_cartas.core.money import parse_brl_to_decimal
from monitor_cartas.core.statuses import AdapterAccessBlockReason, QuotaStatus
from monitor_cartas.services.evidence import save_json_evidence
from monitor_cartas.settings import Settings

ADAPTER_VERSION = "1.0.0"
LISTING_URLS = {
    "imoveis": "https://cartascontempladas.com.br/consorcios-contemplados-de-imoveis/",
    "veiculos": "https://cartascontempladas.com.br/cartas-contempladas-de-veiculos/",
}

_EXTRACT_JS = """
() => {
  const trs = Array.from(document.querySelectorAll('table tbody tr'));
  return trs.map(tr => {
    const tds = Array.from(tr.children);
    const cell = i => (tds[i] ? tds[i].textContent.trim() : '');
    const linkEl = tds[9] ? tds[9].querySelector('a') : null;
    return {
      segmento: cell(2),
      administradora: cell(3),
      credito: cell(4),
      entrada: cell(5),
      parcelas: cell(6),
      valor_parcela: cell(7),
      vencimento: cell(8),
      link: linkEl ? linkEl.href : null,
    };
  });
}
"""


class GrupoLumeAdapter(SiteAdapter):
    name = "grupo_lume"
    base_url = "https://cartascontempladas.com.br"
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
                resp = await page.goto(
                    LISTING_URLS["imoveis"], wait_until="domcontentloaded", timeout=25000
                )
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
        all_rows: list[dict] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch()
            for modalidade, url in LISTING_URLS.items():
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(1500)
                rows = await page.evaluate(_EXTRACT_JS)
                for row in rows:
                    row["_modalidade_pagina"] = modalidade
                all_rows.extend(rows)
                await page.close()
            await browser.close()

        path, _hash = save_json_evidence(
            self.settings.evidence_dir, self.name, "listagem", collected_at, {"rows": all_rows}
        )
        self._evidence_path = str(path)

        urls = []
        for i, row in enumerate(all_rows):
            cota_id = _extract_cota_id(row.get("link")) or f"row-{i}"
            self._cache[cota_id] = row
            urls.append(f"lume-row://{cota_id}")
        return urls

    async def collect_quota(self, url: str) -> CotaContemplada:
        key = url.removeprefix("lume-row://")
        return self._row_to_cota(key, self._cache[key])

    def _row_to_cota(self, cota_id: str, row: dict) -> CotaContemplada:
        is_synthetic_id = cota_id.startswith("row-")

        notes = [
            "Saldo devedor não é publicado — consistência não é aplicável.",
            "Nenhuma taxa é discriminada separadamente da entrada — tratadas "
            "como desconhecidas.",
            "A listagem pública já mostra só cotas disponíveis (sem coluna de "
            "status com valores distintos) — status assumido AVAILABLE.",
        ]
        if is_synthetic_id:
            notes.append(
                "Não foi possível extrair o link/id individual desta linha — "
                "source_id sintético e link aponta para a listagem geral, não "
                "para o anúncio específico."
            )

        remaining = None
        if row.get("parcelas"):
            try:
                remaining = int(row["parcelas"])
            except ValueError:
                remaining = None

        fallback_listing = LISTING_URLS.get(
            row.get("_modalidade_pagina"), LISTING_URLS["imoveis"]
        )

        return CotaContemplada(
            source_site=self.name,
            source_id=str(cota_id),
            source_url=row.get("link") or fallback_listing,
            collected_at=datetime.now(timezone.utc),
            status=QuotaStatus.AVAILABLE,
            status_raw="listado como disponível",
            is_contemplated=True,
            modality=row.get("segmento"),
            administrator=row.get("administradora") or None,
            group=None,
            quota=None if is_synthetic_id else cota_id,
            nominal_credit=parse_brl_to_decimal(row.get("credito")),
            advertised_entry=parse_brl_to_decimal(row.get("entrada")),
            seller_price=parse_brl_to_decimal(row.get("entrada")),
            current_installment=parse_brl_to_decimal(row.get("valor_parcela")),
            remaining_installments=remaining,
            raw_evidence_path=self._evidence_path,
            adapter_version=ADAPTER_VERSION,
            extraction_notes=notes,
        )


def _extract_cota_id(link: str | None) -> str | None:
    if not link:
        return None
    query = parse_qs(urlparse(link).query)
    values = query.get("cota")
    return values[0] if values else None
