from abc import ABC, abstractmethod
from datetime import datetime, timezone

from monitor_cartas.core.models import AccessResult, AdapterRunResult, CotaContemplada


class SiteAdapter(ABC):
    name: str
    base_url: str
    requires_authentication: bool = False

    @abstractmethod
    async def validate_access(self) -> AccessResult:
        ...

    @abstractmethod
    async def collect_listing_urls(self) -> list[str]:
        ...

    @abstractmethod
    async def collect_quota(self, url: str) -> CotaContemplada:
        ...

    async def run(self) -> AdapterRunResult:
        started_at = datetime.now(timezone.utc)
        access = await self.validate_access()

        if not access.ok:
            finished_at = datetime.now(timezone.utc)
            return AdapterRunResult(
                site=self.name,
                started_at=started_at,
                finished_at=finished_at,
                access=access,
            )

        errors: list[str] = []
        quotas: list[CotaContemplada] = []

        try:
            urls = await self.collect_listing_urls()
        except Exception as exc:
            errors.append(f"Falha ao coletar lista de anúncios: {exc}")
            urls = []

        for url in urls:
            try:
                quotas.append(await self.collect_quota(url))
            except Exception as exc:
                errors.append(f"Falha ao processar {url}: {exc}")

        finished_at = datetime.now(timezone.utc)
        return AdapterRunResult(
            site=self.name,
            started_at=started_at,
            finished_at=finished_at,
            access=access,
            listing_count=len(urls),
            processed_count=len(quotas),
            error_count=len(errors),
            snapshot_complete=not errors,
            snapshot_detail=("; ".join(errors[:3]) if errors else None),
            quotas=quotas,
            errors=errors,
        )
