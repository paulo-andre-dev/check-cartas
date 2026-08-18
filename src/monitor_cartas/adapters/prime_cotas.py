"""Adapter da Prime Cotas (primecotascontempladas.com.br).

O front-end é um site estático que lê direto de um projeto Supabase
público via REST (`consortium_cards`), usando a chave "anon" do Supabase —
essa chave é pública por desenho do Supabase (protegida por Row Level
Security no servidor, não é um segredo), embutida no bundle JS que
qualquer visitante do site já recebe. Capturada inspecionando a requisição
real feita pela página (não inventada).

  GET https://fnzyktedpoxyorrelqzv.supabase.co/rest/v1/consortium_cards

Inventário pequeno (21 cotas de imóvel na inspeção), sem paginação
necessária. Sem saldo devedor nem discriminação de taxas — mesma limitação
documentada nas extraction_notes de cada cota. Front-end também é SPA de
página única (âncora #cartas), sem URL estável por cota.
"""
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from monitor_cartas.adapters.base import SiteAdapter
from monitor_cartas.core.models import AccessResult, CotaContemplada
from monitor_cartas.core.money import parse_brl_to_decimal
from monitor_cartas.core.statuses import AdapterAccessBlockReason, QuotaStatus
from monitor_cartas.services.evidence import save_json_evidence
from monitor_cartas.settings import Settings

ADAPTER_VERSION = "1.0.0"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZuenlrdGVk"
    "cG94eW9ycmVscXp2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI2MzQ1OTYsImV4cCI6MjA4ODIxMDU5Nn0"
    ".jIrbUgeILvT98yZu3U4be_iratGKtyf8F7susp5wyxM"
)
REST_ENDPOINT = "https://fnzyktedpoxyorrelqzv.supabase.co/rest/v1/consortium_cards"
PUBLIC_LISTING_URL = "https://www.primecotascontempladas.com.br/#cartas"


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


class PrimeCotasAdapter(SiteAdapter):
    name = "prime_cotas"
    base_url = "https://www.primecotascontempladas.com.br"
    requires_authentication = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=25.0,
            headers={
                "Accept": "application/json",
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            },
        )
        self._cache: dict[str, dict] = {}
        self._evidence_path: str | None = None

    @retry_on_rate_limit
    async def _fetch_cards(self) -> list[dict]:
        resp = await self._client.get(
            REST_ENDPOINT,
            params={
                "select": "*",
                "is_published": "eq.true",
                "tipo_carta": "eq.CONTEMPLADA",
                "order": "created_at.desc",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def validate_access(self) -> AccessResult:
        checked_at = datetime.now(timezone.utc)
        try:
            await self._fetch_cards()
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
        return AccessResult(ok=True, checked_at=checked_at)

    async def collect_listing_urls(self) -> list[str]:
        cards = await self._fetch_cards()
        collected_at = datetime.now(timezone.utc)

        path, _hash = save_json_evidence(
            self.settings.evidence_dir, self.name, "consortium_cards", collected_at, {"data": cards}
        )
        self._evidence_path = str(path)

        urls = []
        for card in cards:
            self._cache[card["id"]] = card
            urls.append(f"primecotas-card://{card['id']}")
        return urls

    async def collect_quota(self, url: str) -> CotaContemplada:
        card_id = url.removeprefix("primecotas-card://")
        card = self._cache[card_id]
        return self._to_cota(card)

    def _to_cota(self, card: dict) -> CotaContemplada:
        opcoes = card.get("opcoes_parcelamento") or []
        primeira_opcao = opcoes[0] if opcoes else {}

        notes = [
            "Prime Cotas não publica saldo devedor — checagem de consistência não é "
            "aplicável para este site.",
            "Nenhuma taxa (plataforma/transferência/comissão) é discriminada "
            "separadamente da 'entrada' — tratadas como desconhecidas.",
            "Front-end é site de página única (âncora #cartas); sem URL estável por "
            "cota individual.",
        ]
        if len(opcoes) > 1:
            notes.append(
                f"Cota tem {len(opcoes)} opções de parcelamento publicadas; usada a "
                "primeira retornada pela API."
            )

        remaining = None
        if primeira_opcao.get("parcelas") is not None:
            try:
                remaining = int(primeira_opcao["parcelas"])
            except (TypeError, ValueError):
                remaining = None

        return CotaContemplada(
            source_site=self.name,
            source_id=card["id"],
            source_url=PUBLIC_LISTING_URL,
            collected_at=datetime.now(timezone.utc),
            status=QuotaStatus.AVAILABLE,
            status_raw=card.get("tipo_carta"),
            is_contemplated=card.get("tipo_carta") == "CONTEMPLADA",
            modality=card.get("category"),
            administrator=card.get("administradora"),
            group=None,
            quota=card.get("numero_cota"),
            nominal_credit=parse_brl_to_decimal(card.get("valor_credito")),
            advertised_entry=parse_brl_to_decimal(card.get("entrada")),
            seller_price=parse_brl_to_decimal(card.get("entrada")),
            current_installment=parse_brl_to_decimal(primeira_opcao.get("valorParcela")),
            remaining_installments=remaining,
            raw_evidence_path=self._evidence_path,
            adapter_version=ADAPTER_VERSION,
            extraction_notes=notes,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
