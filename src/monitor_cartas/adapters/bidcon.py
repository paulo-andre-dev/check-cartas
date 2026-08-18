"""Adapter da Bidcon (bidcon.com.br).

A Bidcon expõe toda a vitrine pública num único endpoint JSON (descoberto
inspecionando o tráfego de rede da home, igual fizemos com a Contemplei):

  GET https://app.bidcon.com.br/api/vitrine

Exige apenas cabeçalhos Origin/Referer do próprio site (comportamento
normal de CORS de front-end, não é autenticação) — sem isso a API responde
403 "origem não permitida". Não há paginação: uma chamada devolve as ~2500
cotas ativas (imóveis e veículos) de uma vez.

Limitações reais da fonte, documentadas nas extraction_notes de cada cota
(nunca inventadas): não publica saldo devedor (sem checagem de consistência
possível), não discrimina taxas de plataforma/transferência separadamente,
e o front-end é uma SPA sem URL estável por anúncio — o clique no card não
navega para uma página própria, então o link do alerta aponta para a
seção pública da vitrine, não para o anúncio individual.
"""
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from monitor_cartas.adapters.base import SiteAdapter
from monitor_cartas.core.models import AccessResult, CotaContemplada
from monitor_cartas.core.statuses import AdapterAccessBlockReason, QuotaStatus
from monitor_cartas.services.evidence import save_json_evidence
from monitor_cartas.settings import Settings

ADAPTER_VERSION = "1.0.0"
VITRINE_ENDPOINT = "https://app.bidcon.com.br/api/vitrine"
PUBLIC_LISTING_URL = "https://www.bidcon.com.br/#cotas"


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


class BidconAdapter(SiteAdapter):
    name = "bidcon"
    base_url = "https://www.bidcon.com.br"
    requires_authentication = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=25.0,
            headers={
                "Accept": "application/json",
                "Origin": "https://www.bidcon.com.br",
                "Referer": "https://www.bidcon.com.br/",
            },
        )
        self._cache: dict[str, dict] = {}
        self._evidence_path: str | None = None

    @retry_on_rate_limit
    async def _fetch_vitrine(self) -> dict:
        resp = await self._client.get(VITRINE_ENDPOINT)
        resp.raise_for_status()
        return resp.json()

    async def validate_access(self) -> AccessResult:
        checked_at = datetime.now(timezone.utc)
        try:
            body = await self._fetch_vitrine()
        except httpx.HTTPStatusError as exc:
            return AccessResult(
                ok=False,
                block_reason=AdapterAccessBlockReason.HTTP_ERROR,
                detail=f"HTTP {exc.response.status_code}",
                checked_at=checked_at,
            )
        except httpx.RequestError as exc:
            return AccessResult(
                ok=False,
                block_reason=AdapterAccessBlockReason.TIMEOUT,
                detail=str(exc),
                checked_at=checked_at,
            )

        if not body.get("ok"):
            return AccessResult(
                ok=False,
                block_reason=AdapterAccessBlockReason.CONTENT_UNAVAILABLE,
                detail=str(body.get("erro")),
                checked_at=checked_at,
            )

        return AccessResult(ok=True, checked_at=checked_at)

    async def collect_listing_urls(self) -> list[str]:
        body = await self._fetch_vitrine()
        collected_at = datetime.now(timezone.utc)

        path, _hash = save_json_evidence(
            self.settings.evidence_dir, self.name, "vitrine", collected_at, body
        )
        self._evidence_path = str(path)

        urls = []
        for item in body.get("cotas", []):
            self._cache[item["id"]] = item
            urls.append(f"bidcon-vitrine://{item['id']}")
        return urls

    async def collect_quota(self, url: str) -> CotaContemplada:
        item_id = url.removeprefix("bidcon-vitrine://")
        item = self._cache[item_id]
        return self._to_cota(item)

    def _to_cota(self, item: dict) -> CotaContemplada:
        notes = [
            "Bidcon não publica saldo devedor — checagem de consistência aritmética "
            "não é aplicável para este site.",
            "Taxas de plataforma/transferência não são discriminadas separadamente; "
            "'entrada' (e) é tratada como desembolso já embutindo o que a Bidcon cobra, "
            "mas isso não está confirmado — tratado como taxas desconhecidas.",
            "Front-end é uma SPA sem URL estável por anúncio; o link aponta para a "
            "vitrine pública, não para o card específico.",
        ]

        return CotaContemplada(
            source_site=self.name,
            source_id=item["id"],
            source_url=PUBLIC_LISTING_URL,
            collected_at=datetime.now(timezone.utc),
            status=QuotaStatus.AVAILABLE,
            status_raw="vitrine pública (assume-se contemplada)",
            is_contemplated=True,
            modality=item.get("t"),
            administrator=item.get("adm"),
            group=None,
            quota=str(item["n"]) if item.get("n") is not None else None,
            nominal_credit=Decimal(str(item["c"])) if item.get("c") is not None else None,
            advertised_entry=Decimal(str(item["e"])) if item.get("e") is not None else None,
            seller_price=Decimal(str(item["e"])) if item.get("e") is not None else None,
            current_installment=Decimal(str(item["p"])) if item.get("p") is not None else None,
            remaining_installments=item.get("x"),
            raw_evidence_path=self._evidence_path,
            adapter_version=ADAPTER_VERSION,
            extraction_notes=notes,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
