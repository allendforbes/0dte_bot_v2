# bot_0dte/risk/risk_config.py
"""
Centralized Risk Configuration

All risk-related parameters in one place. Can be overridden via:
    - Environment variables (0DTE_EXPOSURE_PCT, 0DTE_STOP_PCT, etc.)
    - Direct instantiation with custom values
    - CLI args (via from_args() class method)

CHANGELOG:
    - NEW FILE: Centralizes EXPOSURE_PCT, STOP_PCT, MAX_LOSS_PCT from scattered locations
"""

from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskConfig:
    """
    Immutable risk configuration for 0DTE trading.
    
    Attributes:
        exposure_pct: Max % of equity to risk per trade (default 2%)
        stop_pct: Stop loss as % of option premium (default 50%)
        max_loss_pct: Trail logic max loss threshold (default 50%)
        max_daily_loss_pct: Circuit breaker - max daily drawdown (default 5%)
        max_concurrent_trades: Max simultaneous positions (default 1)
        min_equity: Minimum equity to allow trading (default $1000)
    """
    
    # Core sizing parameters
    exposure_pct: float = 0.02          # 2% of equity per trade
    stop_pct: float = 0.50              # 50% stop loss on premium
    max_loss_pct: float = 0.50          # Trail logic max loss
    
    # Daily limits
    max_daily_loss_pct: float = 0.05    # 5% daily circuit breaker
    max_concurrent_trades: int = 1       # Single trade at a time
    
    # Minimums
    min_equity: float = 1000.0          # $1k minimum to trade
    
    @classmethod
    def from_env(cls, prefix: str = "0DTE_") -> RiskConfig:
        """
        Load config from environment variables.
        
        Env vars checked (with defaults):
            0DTE_EXPOSURE_PCT=0.02
            0DTE_STOP_PCT=0.50
            0DTE_MAX_LOSS_PCT=0.50
            0DTE_MAX_DAILY_LOSS_PCT=0.05
            0DTE_MAX_CONCURRENT_TRADES=1
            0DTE_MIN_EQUITY=1000
        """
        def _get(key: str, default: float) -> float:
            val = os.getenv(f"{prefix}{key}")
            return float(val) if val else default
        
        def _get_int(key: str, default: int) -> int:
            val = os.getenv(f"{prefix}{key}")
            return int(val) if val else default
        
        return cls(
            exposure_pct=_get("EXPOSURE_PCT", 0.02),
            stop_pct=_get("STOP_PCT", 0.50),
            max_loss_pct=_get("MAX_LOSS_PCT", 0.50),
            max_daily_loss_pct=_get("MAX_DAILY_LOSS_PCT", 0.05),
            max_concurrent_trades=_get_int("MAX_CONCURRENT_TRADES", 1),
            min_equity=_get("MIN_EQUITY", 1000.0),
        )
    
    @classmethod
    def from_args(cls, args) -> RiskConfig:
        """
        Build config from argparse namespace.
        Falls back to env vars, then defaults.
        
        Expected args attributes (all optional):
            args.exposure_pct
            args.stop_pct
            args.max_loss_pct
        """
        base = cls.from_env()
        
        return cls(
            exposure_pct=getattr(args, 'exposure_pct', None) or base.exposure_pct,
            stop_pct=getattr(args, 'stop_pct', None) or base.stop_pct,
            max_loss_pct=getattr(args, 'max_loss_pct', None) or base.max_loss_pct,
            max_daily_loss_pct=getattr(args, 'max_daily_loss_pct', None) or base.max_daily_loss_pct,
            max_concurrent_trades=getattr(args, 'max_concurrent_trades', None) or base.max_concurrent_trades,
            min_equity=getattr(args, 'min_equity', None) or base.min_equity,
        )
    
    # =========================================================================
    # Backward compatibility aliases (UPPERCASE)
    # =========================================================================
    @property
    def EXPOSURE_PCT(self) -> float:
        """Legacy alias for exposure_pct."""
        return self.exposure_pct
    
    @property
    def STOP_PCT(self) -> float:
        """Legacy alias for stop_pct."""
        return self.stop_pct
    
    @property
    def MAX_LOSS_PCT(self) -> float:
        """Legacy alias for max_loss_pct."""
        return self.max_loss_pct
    
    def __repr__(self) -> str:
        return (
            f"RiskConfig(exposure={self.exposure_pct:.1%}, "
            f"stop={self.stop_pct:.1%}, "
            f"max_loss={self.max_loss_pct:.1%})"
        )