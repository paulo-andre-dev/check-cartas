import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass
class CombinationConfig:
    enabled: bool = True
    minimum_quotas: int = 2
    maximum_quotas: int = 3
    maximum_results: int = 20
    require_same_administrator: bool = True
    allow_unconfirmed_rules: bool = True


@dataclass
class ConsistencyConfig:
    warning_difference_percentage: Decimal = Decimal("0.15")
    critical_difference_percentage: Decimal = Decimal("0.35")


@dataclass
class ModalityLimits:
    """Teto de crédito e de parcela específico por modalidade (imóvel/veículo).

    Diferente de target_credit_min/max (que só serve pra achar combinações),
    isto aqui É um filtro de exclusão: cota acima do teto da sua modalidade
    não entra na lista de oportunidades.
    """

    min_credit: Decimal | None = None
    max_credit: Decimal | None = None
    max_monthly_payment: Decimal | None = None


@dataclass
class FinancialConfig:
    target_credit_min: Decimal
    target_credit_max: Decimal
    credit_basis: str
    fallback_to_nominal_credit: bool
    max_entry_percentage: Decimal
    gold_entry_percentage: Decimal
    good_entry_percentage: Decimal
    max_monthly_payment: Decimal
    combination: CombinationConfig
    consistency: ConsistencyConfig
    modality_limits: dict[str, ModalityLimits] = field(default_factory=dict)


@dataclass
class MonitoringConfig:
    run_time: str = "08:00"
    timezone: str = "America/Recife"
    alert_if_last_success_older_than_hours: int = 30
    missing_runs_before_removed: int = 3
    cycle_interval_seconds: int = 86400
    minimum_snapshot_ratio: Decimal = Decimal("0.50")


@dataclass
class SitePolicy:
    collect: bool = True
    alert: bool = True
    transaction_status: str = "conditional"
    payment_protection: str = "unknown"


@dataclass
class Settings:
    financial: FinancialConfig
    monitoring: MonitoringConfig
    active_sites: list[str] = field(default_factory=list)
    site_policies: dict[str, SitePolicy] = field(default_factory=dict)
    telegram_bot_token: str | None = None
    telegram_allowed_chat_ids: list[str] = field(default_factory=list)
    data_dir: Path = PROJECT_ROOT / "data"
    logs_dir: Path = PROJECT_ROOT / "logs"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.monitoring.timezone)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cotas.db"

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    def site_policy(self, site: str) -> SitePolicy:
        return self.site_policies.get(site, SitePolicy())


def _parse_modality_limits(raw: dict) -> dict[str, ModalityLimits]:
    from monitor_cartas.core.modality import MODALITY_IMOVEL, MODALITY_VEICULO

    keys = {"imovel": MODALITY_IMOVEL, "veiculo": MODALITY_VEICULO}
    limits: dict[str, ModalityLimits] = {}
    for yaml_key, modality_key in keys.items():
        entry = raw.get(yaml_key)
        if not entry:
            continue
        limits[modality_key] = ModalityLimits(
            min_credit=Decimal(str(entry["min_credit"])) if "min_credit" in entry else None,
            max_credit=Decimal(str(entry["max_credit"])) if "max_credit" in entry else None,
            max_monthly_payment=(
                Decimal(str(entry["max_monthly_payment"]))
                if "max_monthly_payment" in entry
                else None
            ),
        )
    return limits


def _env_decimal(key: str) -> Decimal | None:
    value = os.environ.get(key)
    return Decimal(value) if value else None


def _apply_env_overrides(financial: FinancialConfig) -> None:
    """Sobrepõe valores do config.yaml com variáveis de ambiente, quando
    presentes — pensado pra rodar em plataformas tipo Railway, onde você
    ajusta os parâmetros direto no dashboard sem precisar redeployar.
    """
    from monitor_cartas.core.modality import MODALITY_IMOVEL, MODALITY_VEICULO

    for env_prefix, modality_key in (("IMOVEL", MODALITY_IMOVEL), ("VEICULO", MODALITY_VEICULO)):
        min_credit = _env_decimal(f"{env_prefix}_MIN_CREDIT")
        max_credit = _env_decimal(f"{env_prefix}_MAX_CREDIT")
        max_parcela = _env_decimal(f"{env_prefix}_MAX_MONTHLY_PAYMENT")
        if min_credit is None and max_credit is None and max_parcela is None:
            continue
        existing = financial.modality_limits.get(modality_key, ModalityLimits())
        financial.modality_limits[modality_key] = ModalityLimits(
            min_credit=min_credit if min_credit is not None else existing.min_credit,
            max_credit=max_credit if max_credit is not None else existing.max_credit,
            max_monthly_payment=(
                max_parcela if max_parcela is not None else existing.max_monthly_payment
            ),
        )

    for attr, env_key in (
        ("max_entry_percentage", "MAX_ENTRY_PERCENTAGE"),
        ("gold_entry_percentage", "GOLD_ENTRY_PERCENTAGE"),
        ("good_entry_percentage", "GOOD_ENTRY_PERCENTAGE"),
        ("max_monthly_payment", "MAX_MONTHLY_PAYMENT"),
    ):
        value = _env_decimal(env_key)
        if value is not None:
            setattr(financial, attr, value)


def load_settings(config_path: Path | None = None) -> Settings:
    _load_env_file(PROJECT_ROOT / ".env")

    config_path = config_path or PROJECT_ROOT / "config.yaml"
    raw = yaml.safe_load(config_path.read_text())

    fin = raw["financial"]
    combo = fin.get("combination", {})
    cons = fin.get("consistency", {})

    financial = FinancialConfig(
        target_credit_min=Decimal(str(fin["target_credit_min"])),
        target_credit_max=Decimal(str(fin["target_credit_max"])),
        credit_basis=fin.get("credit_basis", "liquid"),
        fallback_to_nominal_credit=fin.get("fallback_to_nominal_credit", True),
        max_entry_percentage=Decimal(str(fin["max_entry_percentage"])),
        gold_entry_percentage=Decimal(str(fin["gold_entry_percentage"])),
        good_entry_percentage=Decimal(str(fin.get("good_entry_percentage", "0.30"))),
        max_monthly_payment=Decimal(str(fin["max_monthly_payment"])),
        combination=CombinationConfig(
            enabled=combo.get("enabled", True),
            minimum_quotas=combo.get("minimum_quotas", 2),
            maximum_quotas=combo.get("maximum_quotas", 3),
            maximum_results=combo.get("maximum_results", 20),
            require_same_administrator=combo.get("require_same_administrator", True),
            allow_unconfirmed_rules=combo.get("allow_unconfirmed_rules", True),
        ),
        consistency=ConsistencyConfig(
            warning_difference_percentage=Decimal(
                str(cons.get("warning_difference_percentage", "0.15"))
            ),
            critical_difference_percentage=Decimal(
                str(cons.get("critical_difference_percentage", "0.35"))
            ),
        ),
        modality_limits=_parse_modality_limits(fin.get("modalities", {})),
    )
    _apply_env_overrides(financial)

    mon = raw.get("monitoring", {})
    monitoring = MonitoringConfig(
        run_time=mon.get("run_time", "08:00"),
        timezone=mon.get("timezone", "America/Recife"),
        alert_if_last_success_older_than_hours=mon.get(
            "alert_if_last_success_older_than_hours", 30
        ),
        missing_runs_before_removed=mon.get("missing_runs_before_removed", 3),
        cycle_interval_seconds=int(
            os.environ.get("CYCLE_INTERVAL_SECONDS", mon.get("cycle_interval_seconds", 86400))
        ),
        minimum_snapshot_ratio=Decimal(str(mon.get("minimum_snapshot_ratio", "0.50"))),
    )

    sites_raw = raw.get("sites", {})
    policies = {
        name: SitePolicy(
            collect=bool(policy.get("collect", True)),
            alert=bool(policy.get("alert", True)),
            transaction_status=policy.get("transaction_status", "conditional"),
            payment_protection=policy.get("payment_protection", "unknown"),
        )
        for name, policy in sites_raw.get("policies", {}).items()
    }
    sites_env = os.environ.get("SITES_ACTIVE")
    requested_sites = (
        [s.strip() for s in sites_env.split(",") if s.strip()]
        if sites_env
        else sites_raw.get("active", [name for name, policy in policies.items() if policy.collect])
    )
    sites = [
        name
        for name in requested_sites
        if policies.get(name, SitePolicy()).collect
    ]

    # TELEGRAM_CHAT_ID (singular) é o nome usado nos outros bots do autor —
    # aceito como alias de TELEGRAM_ALLOWED_CHAT_IDS pra poder reaproveitar
    # a mesma variável sem duplicar configuração.
    chat_ids_raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS") or os.environ.get(
        "TELEGRAM_CHAT_ID", ""
    )
    chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]

    data_dir = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else PROJECT_ROOT / "data"

    return Settings(
        financial=financial,
        monitoring=monitoring,
        active_sites=sites,
        site_policies=policies,
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        telegram_allowed_chat_ids=chat_ids,
        data_dir=data_dir,
    )
