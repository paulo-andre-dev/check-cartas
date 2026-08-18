"""Adapter da Tramontana Consórcios (cartas.tramontanaconsorcios.com.br).

O front-end consome uma API pública genérica (plataforma "themedeploy",
usada por mais de um site do setor):

  GET https://api.themedeploy.com/api/consorcios-investimentos

Um único request devolve todo o estoque (veículos + imóveis, ~266 itens),
já com status por item ("Disponível"/"Reservada") — só os disponíveis
entram no pipeline. "tax-trans" é a única taxa publicada separadamente
(as demais ficam desconhecidas). O campo "observacoes" mistura nome da
administradora com anotações livres (ex.: "HS Consórcios, tem 91.000,00
pagos") — usado como veio, sem tentar separar por heurística, para não
inventar uma estrutura que a fonte não garante.

Não achei um link de card individual clicável na home renderizada; o
campo "permalink" da API existe mas o path testado (/contemplados/<slug>)
devolveu 404, então o link do alerta aponta para a listagem pública e o
permalink cru fica registrado nas extraction_notes para conferência manual.
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
API_ENDPOINT = "https://api.themedeploy.com/api/consorcios-investimentos"
PUBLIC_LISTING_URL = "https://cartas.tramontanaconsorcios.com.br/"


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


def _parse_parcelas(raw: str | None) -> tuple[int | None, Decimal | None]:
    if not raw or "x" not in raw:
        return None, None
    qty_part, _, value_part = raw.partition("x")
    try:
        qty = int(qty_part.strip())
    except ValueError:
        qty = None
    value = parse_brl_to_decimal(value_part)
    return qty, value


class TramontanaAdapter(SiteAdapter):
    name = "tramontana"
    base_url = "https://cartas.tramontanaconsorcios.com.br"
    requires_authentication = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=25.0, headers={"Accept": "application/json"})
        self._cache: dict[str, dict] = {}
        self._evidence_path: str | None = None

    @retry_on_rate_limit
    async def _fetch_all(self) -> list[dict]:
        resp = await self._client.get(API_ENDPOINT)
        resp.raise_for_status()
        return resp.json()

    async def validate_access(self) -> AccessResult:
        checked_at = datetime.now(timezone.utc)
        try:
            await self._fetch_all()
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
        items = await self._fetch_all()
        collected_at = datetime.now(timezone.utc)

        path, _hash = save_json_evidence(
            self.settings.evidence_dir, self.name, "consorcios-investimentos", collected_at, {"data": items}
        )
        self._evidence_path = str(path)

        urls = []
        for item in items:
            if item.get("situacao-da-carta") != "Disponível":
                continue
            key = str(item["id"])
            self._cache[key] = item
            urls.append(f"tramontana-item://{key}")
        return urls

    async def collect_quota(self, url: str) -> CotaContemplada:
        item_id = url.removeprefix("tramontana-item://")
        item = self._cache[item_id]
        return self._to_cota(item)

    def _to_cota(self, item: dict) -> CotaContemplada:
        remaining, installment = _parse_parcelas(item.get("parcelas"))

        notes = [
            "Campo 'observacoes' da fonte mistura nome da administradora com "
            "anotações livres (ex.: valor já pago) — usado cru, sem heurística de "
            "separação.",
            "Saldo devedor não é publicado — consistência não é aplicável.",
            "Taxas de plataforma/comissão não são discriminadas (só 'tax-trans' é "
            "conhecida) — tratadas como desconhecidas.",
        ]
        permalink = item.get("permalink")
        if permalink:
            notes.append(
                f"permalink bruto da API: '{permalink}' — path testado não resolveu "
                "(404); link do alerta aponta para a listagem geral."
            )

        return CotaContemplada(
            source_site=self.name,
            source_id=str(item["id"]),
            source_url=PUBLIC_LISTING_URL,
            collected_at=datetime.now(timezone.utc),
            status=QuotaStatus.AVAILABLE,
            status_raw=item.get("situacao-da-carta"),
            is_contemplated=True,
            modality=item.get("categoria"),
            administrator=item.get("observacoes"),
            group=None,
            quota=None,
            nominal_credit=parse_brl_to_decimal(item.get("valor-do-credito")),
            advertised_entry=parse_brl_to_decimal(item.get("entrada")),
            seller_price=parse_brl_to_decimal(item.get("entrada")),
            transfer_fee=parse_brl_to_decimal(item.get("tax-trans")),
            current_installment=installment,
            remaining_installments=remaining,
            raw_evidence_path=self._evidence_path,
            adapter_version=ADAPTER_VERSION,
            extraction_notes=notes,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
