"""Runtime configuration. All thresholds live here so they can be tuned without
touching detector code."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class AnomalyThresholds:
    robust_z: float = 3.5  # modified z-score cutoff for amount outliers
    min_history: int = 8  # fewer samples than this -> use the client's overall spending distribution
    min_outlier_multiple: float = 2.0  # also require amount >= 2x the median (materiality)
    recurring_min_months: int = 3  # same payee, similar amount, this many months -> recurring, not anomalous
    large_vs_income_ratio: float = 0.5  # single outflow > 50% of monthly income
    reporting_threshold: float = 500_000_000  # PPATK cash/transfer reporting line (IDR)
    structuring_band: float = 0.10  # within 10% below the reporting threshold
    structuring_window_days: int = 3
    structuring_min_count: int = 2
    velocity_per_day: int = 6  # outflows in a single day
    spending_spike_ratio: float = 1.4  # last month vs trailing average
    negative_cashflow_months: int = 3
    concentration_limit: float = 0.30  # single equity/crypto position share of portfolio
    drawdown_alert: float = -0.15  # 30d price change
    emergency_fund_months: float = 3.0
    crm_stale_contact_days: int = 180


@dataclass
class RiskPolicy:
    """Max share of risky assets (equity + crypto) per risk profile."""

    max_risky_share: dict[str, float] = field(
        default_factory=lambda: {"conservative": 0.30, "moderate": 0.60, "aggressive": 0.90}
    )
    max_crypto_share: dict[str, float] = field(
        default_factory=lambda: {"conservative": 0.0, "moderate": 0.05, "aggressive": 0.15}
    )


@dataclass
class Settings:
    data_dir: Path = PROJECT_ROOT / "data"
    output_dir: Path = PROJECT_ROOT / "output"
    log_dir: Path = PROJECT_ROOT / "logs"
    memory_db: Path = PROJECT_ROOT / "output" / "memory.sqlite3"
    as_of: str | None = None  # ISO date; defaults to latest transaction date
    tool_max_retries: int = 2
    tool_backoff_seconds: float = 0.05
    circuit_failure_threshold: int = 3
    crm_failure_mode: str = "none"  # none | flaky | down | slow
    market_failure_mode: str = "none"
    use_llm: bool = False
    llm_model: str = "claude-opus-5-5"
    thresholds: AnomalyThresholds = field(default_factory=AnomalyThresholds)
    policy: RiskPolicy = field(default_factory=RiskPolicy)
