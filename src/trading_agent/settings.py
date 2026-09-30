"""Single source of configuration truth.

Precedence (highest first): environment variables (prefix TA_, nested with __),
then `config/settings.yaml`, then the defaults below. Secrets are ONLY read from the environment.
Every knob is documented in config/settings.example.yaml - keep the two in sync.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from trading_agent.core.fees import DEFAULT_FEE_SCHEDULES, FeeSchedule

REPO_ROOT = Path(__file__).resolve().parents[2]


class ExchangeSettings(BaseModel):
    id: str = "kraken"
    quote: str = "USD"
    discovery_exchanges: list[str] = Field(
        default_factory=lambda: ["kraken", "binance", "coinbase", "gateio"],
        description="Exchanges scanned for new listings (public endpoints, no keys needed)",
    )
    fees: dict[str, FeeSchedule] = Field(default_factory=lambda: dict(DEFAULT_FEE_SCHEDULES))
    request_timeout_s: float = 20.0


class UniverseSettings(BaseModel):
    core_symbols: list[str] = Field(
        default_factory=lambda: ["kraken:BTC/USD", "kraken:ETH/USD", "kraken:SOL/USD"],
        description="Instrument keys '<exchange>:<BASE>/<QUOTE>'; quote must be a USD stablecoin",
    )
    timeframe: str = "5m"
    candle_history: int = 300
    new_listing_max_age_days: int = 14
    max_new_listings_tracked: int = 15

    @field_validator("core_symbols")
    @classmethod
    def _instrument_keys(cls, v: list[str]) -> list[str]:
        for key in v:
            if ":" not in key or "/" not in key:
                raise ValueError(f"core symbol must look like 'kraken:BTC/USD', got {key!r}")
        if not v:
            raise ValueError("core_symbols must not be empty")
        return v


class RiskSettings(BaseModel):
    initial_capital_quote: float = 1_000.0
    max_position_pct_of_sleeve: float = 0.25
    max_open_positions_per_sleeve: int = 4
    min_edge_to_cost_multiple: float = 2.0
    max_daily_loss_pct: float = 3.0
    max_drawdown_pct: float = 12.0
    max_daily_fees_pct_of_equity: float = 0.5
    max_trades_per_day_per_sleeve: int = 12
    max_position_age_hours: int = 72


class EvolutionSettings(BaseModel):
    enabled: bool = True
    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    web_search_max_uses: int = 8
    max_proposals_per_day: int = 1
    challenger_allocation_pct: float = 15.0
    min_backtest_bars: int = 200
    min_backtest_trades: int = 5
    max_backtest_bars: int = 3000
    required_improvement_pct: float = 0.5
    require_positive_return: bool = True
    max_backtest_drawdown_pct: float = 15.0
    max_backtest_fees_pct: float = 5.0
    promotion_min_days: int = 5
    min_live_trades_for_promotion: int = 5
    promote_margin_pct: float = 1.0
    retire_loss_pct: float = 5.0
    retire_margin_pct: float = 3.0
    subprocess_timeout_s: int = 120
    subprocess_memory_mb: int = 512
    git_commit: bool = True


class ReportSettings(BaseModel):
    hour: int = 22
    minute: int = 0
    timezone: str = "Europe/London"
    max_bullets: int = 10


class ScheduleSettings(BaseModel):
    tick_minutes: int = 5
    discovery_minutes: int = 30
    evolution_hour_utc: int = 3
    backfill_on_start: bool = True


class ObservabilitySettings(BaseModel):
    metrics_port: int = 9100
    log_level: str = "INFO"
    json_logs: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TA_",
        env_nested_delimiter="__",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
        yaml_file=None,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Priority: explicit kwargs > environment > .env > settings.yaml > defaults."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    mode: Literal["paper"] = Field(
        default="paper",
        description="Only 'paper' exists. Live trading is intentionally not implemented (see SAFETY.md).",
    )
    repo_root: Path = Field(
        default=REPO_ROOT, description="Git checkout root; the evolution loop commits here"
    )
    data_dir: Path = REPO_ROOT / "data"
    generated_strategies_dir: Path = (
        REPO_ROOT / "src" / "trading_agent" / "strategies" / "generated"
    )
    exchange: ExchangeSettings = ExchangeSettings()
    universe: UniverseSettings = UniverseSettings()
    risk: RiskSettings = RiskSettings()
    evolution: EvolutionSettings = EvolutionSettings()
    report: ReportSettings = ReportSettings()
    schedule: ScheduleSettings = ScheduleSettings()
    observability: ObservabilitySettings = ObservabilitySettings()

    # Secrets: environment only, never in YAML.
    telegram_bot_token: str | None = Field(default=None, validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, validation_alias="TELEGRAM_CHAT_ID")
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    @field_validator("repo_root", "data_dir", "generated_strategies_dir", mode="before")
    @classmethod
    def _expand(cls, v: Any) -> Path:
        return Path(str(v)).expanduser().resolve()

    def fee_schedule(self) -> FeeSchedule:
        try:
            return self.exchange.fees[self.exchange.id]
        except KeyError as e:
            raise KeyError(f"no fee schedule configured for exchange '{self.exchange.id}'") from e

    # --- derived paths (one place to know the on-disk layout) ---
    @property
    def events_dir(self) -> Path:
        return self.data_dir / "events"

    @property
    def market_dir(self) -> Path:
        return self.data_dir / "market"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def research_dir(self) -> Path:
        return self.data_dir / "research"

    def ensure_dirs(self) -> None:
        for d in (
            self.events_dir,
            self.market_dir,
            self.state_dir,
            self.logs_dir,
            self.research_dir,
            self.generated_strategies_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def load_settings(config_path: Path | None = None) -> Settings:
    """Build Settings: defaults <- config/settings.yaml (or --config / $TA_CONFIG) <- .env <- environment."""
    path = Path(config_path or os.environ.get("TA_CONFIG", REPO_ROOT / "config" / "settings.yaml"))
    if path.exists():
        with path.open() as fh:
            if not isinstance(yaml.safe_load(fh) or {}, dict):
                raise ValueError(f"{path} must contain a mapping at top level")

    class _FileSettings(Settings):
        model_config = SettingsConfigDict(
            **{**Settings.model_config, "yaml_file": str(path) if path.exists() else None}
        )

    return _FileSettings()
