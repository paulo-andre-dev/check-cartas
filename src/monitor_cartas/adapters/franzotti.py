"""Adapter da Franzotti Contemplados (franzotticontemplados.com.br).

Sem API pública — o estoque é uma tabela HTML renderizada no cliente
(WordPress + JS), sem login. Extraímos via Playwright, lendo célula a
célula (índices confirmados inspecionando o DOM real, nunca supostos):

  0 checkbox (id interno da cota, usado na função "somar cotas" do site)
  1 segmento (ícone + texto: "Imóvel"/"Veículo")
  2 administradora
  3 valor do crédito
  4 entrada
  5 parcelas restantes
  6 valor da parcela
  7 observação (livre, geralmente vazia)
  8 próximo vencimento
  9 disponibilidade (ícone fa-check quando disponível)
  10 link "Saiba mais" com a URL pública da cota

Sem saldo devedor publicado — consistência não é aplicável. Sem taxas
discriminadas.
"""
from datetime import datetime, timezone
from decimal import Decimal

from playwright.async_api import async_playwright

from monitor_cartas.adapters.base import SiteAdapter
from monitor_cartas.core.models import AccessResult, CotaContemplada
from monitor_cartas.core.money import parse_brl_to_decimal
from monitor_cartas.core.statuses import AdapterAccessBlockReason, QuotaStatus
from monitor_cartas.services.evidence import save_json_evidence
from monitor_cartas.settings import Settings

ADAPTER_VERSION = "1.0.0"
LISTING_URLS = {
    "imoveis": "https://franzotticontemplados.com.br/cartas-contempladas-de-imoveis/",
    "veiculos": "https://franzotticontemplados.com.br/cartas-contempladas-de-veiculos/",
}

_EXTRACT_JS = """
() => {
  const trs = Array.from(document.querySelectorAll('table tbody tr'));
  return trs.map(tr => {
    const tds = Array.from(tr.children);
    const cell = i => (tds[i] ? tds[i].textContent.trim() : '');
    const checkbox = tds[0] ? tds[0].querySelector('input[name="id"]') : null;
    const link = tds[10] ? tds[10].querySelector('a') : null;
    const disponivel = tds[9] ? !!tds[9].querySelector('.fa-check') : false;
    return {
      id: checkbox ? checkbox.value : null,
      segmento: cell(1),
      administradora: cell(2),
      credito: cell(3),
      entrada: cell(4),
      parcelas: cell(5),
      valor_parcela: cell(6),
      observacao: cell(7),
      vencimento: cell(8),
      disponivel: disponivel,
      link: link ? link.href : null,
    };
  });
}
"""


class FranzottiAdapter(SiteAdapter):
    name = "franzotti"
    base_url = "https://franzotticontemplados.com.br"
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
            key = row.get("id") or f"row-{i}"
            self._cache[key] = row
            urls.append(f"franzotti-row://{key}")
        return urls

    async def collect_quota(self, url: str) -> CotaContemplada:
        key = url.removeprefix("franzotti-row://")
        return self._row_to_cota(self._cache[key])

    def _row_to_cota(self, row: dict) -> CotaContemplada:
        notes = [
            "Saldo devedor não é publicado — consistência não é aplicável.",
            "Nenhuma taxa é discriminada separadamente da entrada — tratadas "
            "como desconhecidas.",
        ]

        remaining = None
        if row.get("parcelas"):
            try:
                remaining = int(row["parcelas"])
            except ValueError:
                remaining = None

        source_id = row.get("id") or (row.get("link") or "").rstrip("/").rsplit("-", 1)[-1]

        return CotaContemplada(
            source_site=self.name,
            source_id=str(source_id),
            source_url=row.get("link") or LISTING_URLS["imoveis"],
            collected_at=datetime.now(timezone.utc),
            status=QuotaStatus.AVAILABLE if row.get("disponivel") else QuotaStatus.UNAVAILABLE,
            status_raw="disponível" if row.get("disponivel") else "indisponível",
            is_contemplated=True,
            modality=row.get("segmento"),
            administrator=row.get("administradora") or None,
            group=None,
            quota=row.get("id"),
            nominal_credit=parse_brl_to_decimal(row.get("credito")),
            advertised_entry=parse_brl_to_decimal(row.get("entrada")),
            seller_price=parse_brl_to_decimal(row.get("entrada")),
            current_installment=parse_brl_to_decimal(row.get("valor_parcela")),
            remaining_installments=remaining,
            raw_evidence_path=self._evidence_path,
            adapter_version=ADAPTER_VERSION,
            extraction_notes=notes,
        )
