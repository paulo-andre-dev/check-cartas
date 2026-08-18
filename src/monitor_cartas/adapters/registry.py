from monitor_cartas.adapters.base import SiteAdapter
from monitor_cartas.adapters.bidcon import BidconAdapter
from monitor_cartas.adapters.bolsa_consorcio import BolsaConsorcioAdapter
from monitor_cartas.adapters.compra_consorcios import CompraConsorciosAdapter
from monitor_cartas.adapters.capitalizza import CapitalizzaAdapter
from monitor_cartas.adapters.contemplei import ContempleiAdapter
from monitor_cartas.adapters.franzotti import FranzottiAdapter
from monitor_cartas.adapters.grupo_lume import GrupoLumeAdapter
from monitor_cartas.adapters.prime_cotas import PrimeCotasAdapter
from monitor_cartas.adapters.tramontana import TramontanaAdapter
from monitor_cartas.adapters.vemcon import VemConAdapter
from monitor_cartas.settings import Settings

ADAPTER_CLASSES: dict[str, type[SiteAdapter]] = {
    "contemplei": ContempleiAdapter,
    "bidcon": BidconAdapter,
    "prime_cotas": PrimeCotasAdapter,
    "tramontana": TramontanaAdapter,
    "franzotti": FranzottiAdapter,
    "grupo_lume": GrupoLumeAdapter,
    "compra_consorcios": CompraConsorciosAdapter,
    "bolsa_consorcio": BolsaConsorcioAdapter,
    "vemcon": VemConAdapter,
    "capitalizza": CapitalizzaAdapter,
}


def build_adapters(site_names: list[str], settings: Settings) -> list[SiteAdapter]:
    adapters = []
    for name in site_names:
        cls = ADAPTER_CLASSES.get(name)
        if cls is None:
            raise ValueError(
                f"Site '{name}' não tem adapter implementado ainda. "
                f"Disponíveis: {sorted(ADAPTER_CLASSES)}"
            )
        adapters.append(cls(settings))
    return adapters
