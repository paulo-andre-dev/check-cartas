"""Adapter da Contemplei (contemplei.app).

A Contemplei expõe uma API JSON pública para o marketplace de compra
(descoberta inspecionando o tráfego de rede da página /comprar/):

  GET https://contemplei.app/v1/anuncios/publico?segmento=imoveis&page=N&pageSize=100
  GET https://contemplei.app/v1/anuncios/publico/{id}

Não exige autenticação nem Playwright para o estoque público — por isso
usamos httpx direto, conforme a preferência do projeto por endpoint JSON
estável sobre navegador headless.

URL pública do anúncio individual: https://contemplei.app/carta/{seoSlug}/
(confirmada inspecionando os links reais da página, já que o padrão
/comprar/{seoSlug} não existe).
"""
import asyncio
from datetime import datetime, timezone

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from monitor_cartas.adapters.base import SiteAdapter
from monitor_cartas.core.models import AccessResult, CotaContemplada
from monitor_cartas.core.money import cents_to_decimal
from monitor_cartas.core.statuses import AdapterAccessBlockReason, QuotaStatus
from monitor_cartas.services.evidence import save_json_evidence
from monitor_cartas.settings import Settings

ADAPTER_VERSION = "1.0.0"
LIST_ENDPOINT = "https://contemplei.app/v1/anuncios/publico"
PAGE_SIZE = 100
# A Contemplei só tem dois segmentos no marketplace público: "imoveis" e
# "moveis" (este último é o nome real da API para veículos — confirmado
# inspecionando os dois únicos valores existentes, não é suposição).
SEGMENTOS = ["imoveis", "moveis"]


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


retry_on_rate_limit = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    stop=stop_after_attempt(4),
    reraise=True,
)


class ContempleiAdapter(SiteAdapter):
    name = "contemplei"
    base_url = "https://contemplei.app"
    requires_authentication = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=20.0, headers={"Accept": "application/json"})

    async def validate_access(self) -> AccessResult:
        checked_at = datetime.now(timezone.utc)
        try:
            resp = await self._client.get(LIST_ENDPOINT, params={"segmento": "imoveis", "pageSize": 1})
        except httpx.RequestError as exc:
            return AccessResult(
                ok=False,
                block_reason=AdapterAccessBlockReason.TIMEOUT,
                detail=str(exc),
                checked_at=checked_at,
            )

        if resp.status_code == 429:
            return AccessResult(
                ok=False,
                block_reason=AdapterAccessBlockReason.HTTP_ERROR,
                detail="HTTP 429 (rate limit)",
                checked_at=checked_at,
            )
        if resp.status_code >= 400:
            return AccessResult(
                ok=False,
                block_reason=AdapterAccessBlockReason.HTTP_ERROR,
                detail=f"HTTP {resp.status_code}",
                checked_at=checked_at,
            )

        body = resp.json()
        if not body.get("meta", {}).get("success"):
            return AccessResult(
                ok=False,
                block_reason=AdapterAccessBlockReason.CONTENT_UNAVAILABLE,
                detail="Resposta sem meta.success=true",
                checked_at=checked_at,
            )

        return AccessResult(ok=True, checked_at=checked_at)

    @retry_on_rate_limit
    async def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        await asyncio.sleep(0.4)  # rate limiting simples por domínio
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp

    async def collect_listing_urls(self) -> list[str]:
        urls: list[str] = []
        for segmento in SEGMENTOS:
            page = 1
            while True:
                resp = await self._get(
                    LIST_ENDPOINT,
                    params={"segmento": segmento, "page": page, "pageSize": PAGE_SIZE},
                )
                body = resp.json()
                items = body.get("data", [])
                for item in items:
                    urls.append(f"{LIST_ENDPOINT}/{item['id']}")

                total = body.get("meta", {}).get("total", len(items))
                if page * PAGE_SIZE >= total or not items:
                    break
                page += 1

        return urls

    async def collect_quota(self, url: str) -> CotaContemplada:
        resp = await self._get(url)
        payload = resp.json()
        item = payload["data"]

        collected_at = datetime.now(timezone.utc)
        evidence_path, _hash = save_json_evidence(
            self.settings.evidence_dir, self.name, item["codigo"], collected_at, payload
        )

        return self._to_cota(item, str(evidence_path), collected_at)

    def _to_cota(self, item: dict, evidence_path: str, collected_at: datetime) -> CotaContemplada:
        calculos = item.get("calculos", {})
        notes = [
            "seller_price assumido igual ao campo 'entrada' da API pública; a Contemplei "
            "não distingue explicitamente valor pago ao vendedor de outras taxas embutidas.",
            "commission_fee não é exposta separadamente pela API pública — tratada como "
            "custo desconhecido (has_unknown_fees).",
            "API não expõe crédito líquido separado do nominal; percentual calculado é "
            "provisório com base no crédito nominal (creditoCents).",
        ]

        is_contemplated = item.get("situacao") == "Contemplada"

        other_costs = cents_to_decimal(item.get("taxaAnaliseCents"))

        return CotaContemplada(
            source_site=self.name,
            source_id=item["codigo"],
            source_url=f"https://contemplei.app/carta/{item['seoSlug']}/",
            collected_at=collected_at,
            status=QuotaStatus.AVAILABLE if is_contemplated else QuotaStatus.UNAVAILABLE,
            status_raw=item.get("situacao"),
            is_contemplated=is_contemplated,
            modality=item.get("segmento"),
            administrator=item.get("administradoraNome"),
            group=item.get("grupo"),
            quota=None,
            nominal_credit=cents_to_decimal(item.get("creditoCents")),
            advertised_entry=cents_to_decimal(item.get("entradaCents")),
            seller_price=cents_to_decimal(item.get("entradaCents")),
            platform_fee=cents_to_decimal(calculos.get("taxaPlataformaCents")),
            commission_fee=None,
            transfer_fee=cents_to_decimal(item.get("taxaTransferenciaCents")),
            overdue_installments=None,
            other_initial_costs=other_costs,
            outstanding_balance=cents_to_decimal(item.get("saldoDevedorCents")),
            remaining_installments=item.get("prazoRestanteMeses"),
            current_installment=cents_to_decimal(calculos.get("parcelaComSeguroCents"))
            or cents_to_decimal(item.get("parcelaSemSeguroCents")),
            raw_evidence_path=evidence_path,
            adapter_version=ADAPTER_VERSION,
            extraction_notes=notes,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
