"""Global configuration. Every module reads these; none hard-codes them.

The defaults are the **baseline**, not "the right parameters". Every variant
studied is measured against them; without a stable baseline, the question
"how much did that one change move the result" has no answer.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date

from backtest.core.types import ContractSpec, Right, require

#: Trading days per year, for annualising volatility only — never for pricing
#: (see TimeConventions).
TRADING_DAYS_PER_YEAR = 252

#: Calendar days per year, for pricing and theta decay. Options decay over
#: weekends too.
CALENDAR_DAYS_PER_YEAR = 365.0


@dataclass(frozen=True, slots=True)
class TimeConventions:
    """Two time conventions with different jobs; forcing them into one makes
    one of them wrong.

    - Time to expiry and theta, for pricing: **calendar days / 365**. An option
      decays on non-trading days as well.
    - Annualising volatility: **trading days / 252**. Volatility is only
      realised on trading days.
    """

    pricing_basis: str = "calendar/365"
    vol_annualization_basis: str = "trading/252"

    def year_fraction(self, start: date, end: date) -> float:
        """Time to expiry in years, on the pricing convention."""
        return max((end - start).days, 0) / CALENDAR_DAYS_PER_YEAR

    def describe(self) -> dict[str, str]:
        return {
            "pricing": self.pricing_basis,
            "vol_annualization": self.vol_annualization_basis,
        }


@dataclass(frozen=True, slots=True)
class CostConfig:
    """Cost assumptions. Shared by both engines; these must not diverge."""

    #: Where the fill sits between mid and the adverse side: 0 = mid,
    #: 1 = fully adverse (pay the ask, receive the bid).
    adverse_fraction: float = 1.0
    commission_per_contract: float = 0.65
    min_commission_per_order: float = 1.00
    regulatory_fee_per_contract: float = 0.05
    exercise_fee: float = 0.0
    slippage_per_contract: float = 0.0
    #: Maximum relative bid/ask spread; wider than this counts as untradable.
    max_relative_spread: float = 0.20

    def __post_init__(self) -> None:
        require(
            0.0 <= self.adverse_fraction <= 1.0,
            f"adverse_fraction must lie between 0 and 1, got {self.adverse_fraction}",
        )
        require(self.max_relative_spread > 0, "max_relative_spread must be positive")
        for name in (
            "commission_per_contract",
            "min_commission_per_order",
            "regulatory_fee_per_contract",
            "exercise_fee",
            "slippage_per_contract",
        ):
            require(getattr(self, name) >= 0, f"{name} must not be negative")


@dataclass(frozen=True, slots=True)
class SyntheticChainConfig:
    """Parameters of the synthetic chain.

    Deliberately crude: no volatility skew, no term structure. A crude model
    that says so is safer than a model that looks refined and is still wrong —
    the latter invites belief in the backtest's conclusions.
    """

    #: How far above and below spot the strikes reach, as a fraction.
    strike_range: float = 0.20
    #: Bid/ask spread as a fraction of the theoretical price.
    spread_pct: float = 0.02
    #: Absolute floor on the bid/ask spread.
    spread_floor: float = 0.05
    #: Which expiry cadences to generate.
    include_monthly: bool = True
    include_weekly: bool = True
    #: Weekly expiries only exist after this date (they were rolled out
    #: gradually).
    weekly_start: date = date(2010, 6, 4)
    max_dte: int = 120

    def __post_init__(self) -> None:
        require(0 < self.strike_range < 1, "strike_range must lie between 0 and 1")
        require(self.spread_pct > 0 and self.spread_floor > 0,
                "the spread parameters must be positive")
        require(self.max_dte > 0, "max_dte must be positive")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """The full configuration of one run. Kept with the report so the result
    can be re-executed."""

    symbol: str = "QQQ"
    start: date = date(2015, 1, 1)
    end: date = date(2024, 12, 31)
    #: Notional capital. The trade-table engine applies no capital constraint;
    #: this only converts P&L into fractions and checks whether concurrent
    #: positions would have been affordable.
    notional_capital: float = 100_000.0
    #: Constant fallback when no risk-free rate series is available. Every
    #: fallback is recorded in the event stream and flagged in the report.
    fallback_risk_free_rate: float = 0.02
    #: Execution lag in trading days. The intent forms at the close of T and is
    #: executed at the close of T + lag.
    execution_lag_days: int = 1
    #: Minimum remaining life (trading days) for a contract to be selected;
    #: must not be below the execution lag.
    min_days_to_expiry: int = 5
    random_seed: int = 20260917

    contract: ContractSpec = field(default_factory=lambda: ContractSpec(symbol="QQQ"))
    costs: CostConfig = field(default_factory=CostConfig)
    synthetic: SyntheticChainConfig = field(default_factory=SyntheticChainConfig)
    time: TimeConventions = field(default_factory=TimeConventions)

    def __post_init__(self) -> None:
        require(self.start < self.end, "the start date must precede the end date")
        require(self.notional_capital > 0, "notional capital must be positive")
        require(self.execution_lag_days >= 1, "the execution lag is at least one day")
        require(
            self.contract.symbol == self.symbol,
            f"the contract spec's symbol {self.contract.symbol} does not match "
            f"the backtest symbol {self.symbol}",
        )
        # A one-day lag means an exit decided on T is executed on T+1, and the
        # contract has to still be tradable then. Below lag+1 a position is
        # silently dragged into expiry settlement while the report looks fine.
        require(
            self.min_days_to_expiry >= self.execution_lag_days + 1,
            f"min_days_to_expiry must be at least {self.execution_lag_days + 1} "
            f"(execution lag {self.execution_lag_days} plus one), "
            f"got {self.min_days_to_expiry}",
        )

    def describe(self) -> dict[str, object]:
        """The whole configuration in printable form, kept with the report."""
        return dataclasses.asdict(self)
