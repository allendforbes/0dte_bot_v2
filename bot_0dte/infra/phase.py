# CHANGES:
# - Mechanical wiring only
# - No strategy or convexity logic modified

"""
Execution Phase Control - Convex Framework

CRITICAL: Use EXECUTION_PHASE as the single source of truth.
Do not introduce or rely on TRADE_MODE if they diverge.
"""
from enum import Enum


class ExecutionPhase(Enum):
    """
    Three phases with explicit behavioral contracts:
    
    SHADOW: Logic validation only
        - No IBKR connection
        - No order placement
        - Full decision logging
        - Identical signal logic to Paper/Live
    
    PAPER: Execution validation
        - IBKR paper account (port 7497)
        - Real order placement
        - Real fills
        - Identical signal logic to Shadow/Live
    
    LIVE: Production deployment
        - IBKR live account (port 4001)
        - Real capital at risk
        - Smallest risk tier only (L0)
        - Identical signal logic to Shadow/Paper
    """
    SHADOW = "shadow"
    PAPER = "paper"
    LIVE = "live"
    
    @classmethod
    def from_env(cls, default="shadow"):
        """Read from EXECUTION_PHASE environment variable."""
        import os
        value = os.getenv("EXECUTION_PHASE", default).lower().strip()
        
        if value not in {"shadow", "paper", "live"}:
            raise ValueError(
                f"Invalid EXECUTION_PHASE: '{value}'. "
                f"Must be 'shadow', 'paper', or 'live'."
            )
        
        return cls(value)