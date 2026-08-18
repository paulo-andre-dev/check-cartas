"""Cadastro de administradoras e suas regras de transferência/combinação de cotas.

Este cadastro é versionado no banco, não repesquisado a cada execução.
Quando uma administradora nova aparece num anúncio, criamos uma entrada
PENDING_MANUAL_VALIDATION — a validação real do regulamento é manual,
feita fora do scraper, e depois persistida com upsert_administrator_rule.
"""
from monitor_cartas.core.models import AdministratorRule
from monitor_cartas.repositories.sqlite import QuotaRepository


def ensure_administrator_registered(repo: QuotaRepository, administrator: str) -> None:
    existing = repo.get_administrator_rules()
    if administrator in existing:
        return
    repo.upsert_administrator_rule(AdministratorRule(administrator=administrator))


def sync_administrators_from_quotas(repo: QuotaRepository, administrators: set[str]) -> None:
    for administrator in administrators:
        if administrator:
            ensure_administrator_registered(repo, administrator)
