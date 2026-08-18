"""Adapter da Bolsa do Consórcio (bolsadoconsorcio.com.br).

Tabela HTML server/cliente (DataTables), sem login. Única das fontes
raspadas por HTML que publica saldo devedor — dá pra rodar a checagem de
consistência de verdade aqui. Colunas confirmadas no DOM real:

  Administradora | Crédito | Entrada | Parc. Restantes ("73 x R$ 340.00",
  formato inconsistente entre linhas — às vezes sem "R$", às vezes X
  maiúsculo) | Próx. Parcela | Saldo Devedor ("---" quando não informado)
  | Próx. Venc. | Composição | Status | (2 links: /autor/<vendedor>/ e
  /consorcio/<slug-da-cota>/ — o segundo é o que usamos como URL pública)
"""
import re
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
    "imoveis": "https://www.bolsadoconsorcio.com.br/categoria-cota/cotas-contempladas/imoveis/",
    "veiculos": "https://www.bolsadoconsorcio.com.br/categoria-cota/cotas-contempladas/autos/",
}

_EXTRACT_JS = """
() => {
  const trs = Array.from(document.querySelectorAll('table tbody tr'));
  return trs.map(tr => {
    const tds = Array.from(tr.children);
    const cell = i => (tds[i] ? tds[i].textContent.trim() : '');
    const links = Array.from(tr.querySelectorAll('a')).map(a => a.href);
    const detailLink = links.find(h => h.includes('/consorcio/')) || null;
    return {
      administradora: cell(0),
      credito: cell(1),
      entrada: cell(2),
      parc_restantes: cell(3),
      proxima_parcela: cell(4),
      saldo_devedor: cell(5),
      vencimento: cell(6),
      composicao: cell(7),
      status: cell(8),
      link: detailLink,
    };
  });
}
"""

_PARCELAS_RE = re.compile(r"(\d+)\s*[xX]\s*(.+)")


def _parse_parcelas(raw: str | None) -> tuple[int | None, Decimal | None]:
    if not raw:
        return None, None
    match = _PARCELAS_RE.match(raw.strip())
    if not match:
        return None, None
    qty = int(match.group(1))
    value = parse_brl_to_decimal(match.group(2))
    return qty, value


class BolsaConsorcioAdapter(SiteAdapter):
    name = "bolsa_consorcio"
    base_url = "https://www.bolsadoconsorcio.com.br"
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
            key = _slug_id(row.get("link")) or f"row-{i}"
            self._cache[key] = row
            urls.append(f"bolsa-row://{key}")
        return urls

    async def collect_quota(self, url: str) -> CotaContemplada:
        key = url.removeprefix("bolsa-row://")
        return self._row_to_cota(key, self._cache[key])

    def _row_to_cota(self, cota_id: str, row: dict) -> CotaContemplada:
        remaining, installment = _parse_parcelas(row.get("parc_restantes"))

        notes = [
            "Nenhuma taxa é discriminada separadamente da entrada — tratadas "
            "como desconhecidas.",
            f"Composição publicada pela fonte: '{row.get('composicao')}' (Única = "
            "cota isolada, não combinada).",
        ]

        status_raw = (row.get("status") or "").strip().lower()
        is_available = status_raw.startswith("dispon")

        return CotaContemplada(
            source_site=self.name,
            source_id=cota_id,
            source_url=row.get("link") or LISTING_URLS["imoveis"],
            collected_at=datetime.now(timezone.utc),
            status=QuotaStatus.AVAILABLE if is_available else QuotaStatus.RESERVED,
            status_raw=row.get("status"),
            is_contemplated=True,
            modality=row.get("_modalidade_pagina"),
            administrator=row.get("administradora") or None,
            group=None,
            quota=None,
            nominal_credit=parse_brl_to_decimal(row.get("credito")),
            advertised_entry=parse_brl_to_decimal(row.get("entrada")),
            seller_price=parse_brl_to_decimal(row.get("entrada")),
            outstanding_balance=parse_brl_to_decimal(row.get("saldo_devedor")),
            current_installment=installment,
            remaining_installments=remaining,
            raw_evidence_path=self._evidence_path,
            adapter_version=ADAPTER_VERSION,
            extraction_notes=notes,
        )


def _slug_id(link: str | None) -> str | None:
    if not link:
        return None
    return link.rstrip("/").rsplit("/", 1)[-1]
