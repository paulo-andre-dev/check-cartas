"""Identidade e deduplicação de cotas.

Chave primária: source_site + source_id (identifica o anúncio).
Fingerprint secundário: detecta a MESMA cota anunciada em plataformas
diferentes, para não duplicar alertas quando dois sites vendem a mesma cota.
"""
import hashlib

from monitor_cartas.core.models import CotaContemplada


def primary_key(cota: CotaContemplada) -> tuple[str, str]:
    return (cota.source_site, cota.source_id)


def fingerprint(cota: CotaContemplada) -> str | None:
    """Fingerprint determinístico quando administradora+grupo+cota são conhecidos.

    Sem grupo/cota publicados, cai para um fingerprint probabilístico baseado
    em crédito/entrada/parcela/saldo/prazo — menos confiável, mas ainda útil
    para agrupar prováveis duplicatas entre sites.
    """
    if cota.administrator and cota.group and cota.quota and cota.nominal_credit is not None:
        raw = f"{cota.administrator.lower().strip()}|{cota.group}|{cota.quota}|{cota.nominal_credit}"
        return "det:" + hashlib.sha256(raw.encode()).hexdigest()[:24]

    if cota.administrator and cota.nominal_credit is not None:
        parts = [
            cota.administrator.lower().strip(),
            str(cota.nominal_credit),
            str(cota.advertised_entry),
            str(cota.current_installment),
            str(cota.outstanding_balance),
            str(cota.remaining_installments),
        ]
        raw = "|".join(parts)
        return "prob:" + hashlib.sha256(raw.encode()).hexdigest()[:24]

    return None
