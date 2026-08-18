"""Adapter das cartas públicas da VemCon, descobertas pelo sitemap oficial."""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser

import httpx

from monitor_cartas.adapters.base import SiteAdapter
from monitor_cartas.core.models import AccessResult, CotaContemplada
from monitor_cartas.core.money import parse_brl_to_decimal
from monitor_cartas.core.statuses import AdapterAccessBlockReason, QuotaStatus
from monitor_cartas.services.evidence import save_json_evidence
from monitor_cartas.settings import Settings

ADAPTER_VERSION = "1.0.0"
SITEMAP_URL = "https://vemcon.com.br/sitemap-cards.xml"
API_URL = "https://apftuqncwgobmmuqmqrq.supabase.co/rest/v1/cards_public"
# Chave publicável enviada pelo próprio front-end público da VemCon.
PUBLISHABLE_KEY = "sb_publishable__yBD1H4zu7a5RHoet2Bz-w_sOJvpVr2"
ADMIN_SELECT = (
    "administrators(id,name,slug,transfer_fee_percentage,payment_method,"
    "refund_policy,process_days)"
)


def _decimal(value) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _canonical_url(card: dict) -> str:
    administrator = card.get("administrators") or {}
    credit_thousands = round(Decimal(str(card["credit_value"])) / Decimal("1000"))
    return (
        f"https://vemcon.com.br/consorcio/{card.get('category', 'imovel')}/"
        f"{administrator.get('slug', 'administradora')}/credito-{credit_thousands}-mil-"
        f"{card['code'].lower()}"
    )


class _DetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.capture: str | None = None
        self.parts: list[str] = []
        self.pending_label: str | None = None
        self.fields: dict[str, str] = {}
        self.all_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("th", "td"):
            self.capture = tag
            self.parts = []

    def handle_data(self, data: str) -> None:
        self.all_text.append(data)
        if self.capture:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != self.capture:
            return
        value = " ".join("".join(self.parts).split())
        if tag == "th":
            self.pending_label = value
        elif tag == "td" and self.pending_label:
            self.fields[self.pending_label] = value
            self.pending_label = None
        self.capture = None


def parse_sitemap(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    return [element.text for element in root.iter() if element.tag.endswith("loc") and element.text]


def parse_detail(html: str) -> dict:
    parser = _DetailParser()
    parser.feed(html)
    text = " ".join(" ".join(parser.all_text).split())
    code_match = re.search(r"Código\s+([A-Z]{2,5}-\d+)", text, re.IGNORECASE)
    return {"code": code_match.group(1).upper() if code_match else None, **parser.fields}


class VemConAdapter(SiteAdapter):
    name = "vemcon"
    base_url = "https://vemcon.com.br"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "apikey": PUBLISHABLE_KEY,
                "Authorization": f"Bearer {PUBLISHABLE_KEY}",
                "Accept-Profile": "public",
                "Origin": "https://vemcon.com.br",
                "Referer": "https://vemcon.com.br/",
            },
        )
        self._cache: dict[str, dict] = {}
        self._url_by_code: dict[str, str] = {}

    async def _fetch(self, url: str) -> str:
        response = await self._client.get(url)
        response.raise_for_status()
        return response.text

    async def _fetch_cards(self) -> list[dict]:
        response = await self._client.get(
            API_URL,
            params={
                "select": f"*,{ADMIN_SELECT}",
                "publication_status": "eq.active",
                "order": "data_updated_at.desc",
            },
        )
        response.raise_for_status()
        return response.json()

    async def validate_access(self) -> AccessResult:
        checked_at = datetime.now(timezone.utc)
        try:
            cards = await self._fetch_cards()
        except httpx.HTTPError as exc:
            return AccessResult(ok=False, block_reason=AdapterAccessBlockReason.HTTP_ERROR,
                                detail=str(exc), checked_at=checked_at)
        if not cards:
            return AccessResult(ok=False, block_reason=AdapterAccessBlockReason.CONTENT_UNAVAILABLE,
                                detail="API pública de cartas vazia.", checked_at=checked_at)
        return AccessResult(ok=True, checked_at=checked_at)

    async def collect_listing_urls(self) -> list[str]:
        cards = await self._fetch_cards()
        sitemap_urls = parse_sitemap(await self._fetch(SITEMAP_URL))
        self._url_by_code = {
            (
                url.rstrip("/").rsplit("-", 2)[-2]
                + "-"
                + url.rstrip("/").rsplit("-", 1)[-1]
            ).upper(): url
            for url in sitemap_urls
        }
        urls = []
        for card in cards:
            code = card["code"]
            self._cache[code] = card
            urls.append(self._url_by_code.get(code, _canonical_url(card)))
        return urls

    async def collect_quota(self, url: str) -> CotaContemplada:
        source_id = (
            url.removeprefix("vemcon-card://")
            if url.startswith("vemcon-card://")
            else (
                url.rstrip("/").rsplit("-", 2)[-2]
                + "-"
                + url.rstrip("/").rsplit("-", 1)[-1]
            ).upper()
        )
        item = self._cache[source_id]
        collected_at = datetime.now(timezone.utc)
        path, _ = save_json_evidence(
            self.settings.evidence_dir, self.name, source_id, collected_at,
            {"url": url, "card": item},
        )
        administrator = item.get("administrators") or {}
        raw_status = item.get("publication_status") or item.get("status")
        source_url = self._url_by_code.get(source_id, url)
        return CotaContemplada(
            source_site=self.name,
            source_id=source_id,
            source_url=source_url,
            collected_at=collected_at,
            status=QuotaStatus.AVAILABLE,
            status_raw=raw_status,
            is_contemplated=True,
            modality=item.get("category"),
            administrator=administrator.get("name"),
            group=item.get("group"),
            quota=item.get("quota"),
            nominal_credit=_decimal(item.get("credit_value")),
            updated_credit=_decimal(item.get("credit_value")),
            advertised_entry=_decimal(item.get("asking_price")),
            seller_price=_decimal(item.get("asking_price")),
            platform_fee=_decimal(item.get("platform_fee")),
            outstanding_balance=_decimal(item.get("outstanding_balance")),
            remaining_installments=item.get("remaining_installments"),
            current_installment=_decimal(item.get("monthly_payment")),
            raw_evidence_path=str(path),
            adapter_version=ADAPTER_VERSION,
            extraction_notes=[
                "A página declara pagamento do saldo em conta garantia; contrato, "
                "provedor da custódia e condições de devolução ainda devem ser conferidos.",
                f"Taxa de transferência publicada pela administradora: "
                f"{administrator.get('transfer_fee_percentage')}% do crédito; não somada "
                "automaticamente porque o valor final deve ser confirmado.",
                f"Dados da carta atualizados em {item.get('data_updated_at') or 'data não informada'}.",
            ],
        )

    async def aclose(self) -> None:
        await self._client.aclose()
