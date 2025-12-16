"""
Decision Logger - Single canonical log for all evaluations
Integrates with existing StructuredLogger architecture
"""
import json
from datetime import datetime
from pathlib import Path


class DecisionLogger:
    """
    Logs ENTER/HOLD/EXIT/BLOCK decisions with FIXED SCHEMA.
    
    CRITICAL: This is the canonical decision log.
    Schema is rigid to enable Shadow ↔ Paper ↔ Live diffs.
    
    No kwargs. No optional fields. No schema drift.
    """
    
    def __init__(self, phase: str):
        """
        Args:
            phase: "shadow", "paper", or "live"
        """
        self.phase = phase
        
        # Create phase-specific directory
        log_dir = Path(f"runtime/logs/{phase}")
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Daily log file
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_path = log_dir / f"decisions_{date_str}.log"
        
        # Line-buffered for real-time tailing
        self.handle = log_path.open("a", buffering=1)
    
    def log(
        self,
        *,
        decision: str,
        symbol: str,
        reason: str,
        convexity_score: float,
        tier: str,
        price: float,
    ):
        """
        Log a single evaluation decision with FIXED SCHEMA.
        
        CONTRACT:
        - Exactly ONE call to this method per evaluation per symbol
        - Caller must ensure mutual exclusivity of decisions
        - Repeated calls per tick are a logic bug
        
        Args:
            decision: "ENTER" | "HOLD" | "EXIT" | "BLOCK"
            symbol: Ticker symbol
            reason: Why this decision was made
            convexity_score: Current convexity score (0.0-1.0)
            tier: Current risk tier ("L0" | "L1" | "L2")
            price: Current underlying price
        
        All fields are REQUIRED. No defaults. No optionals.
        This enables reliable diffs across Shadow/Paper/Live.
        """
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "phase": self.phase,
            "symbol": symbol,
            "decision": decision,
            "reason": reason,
            "convexity_score": convexity_score,
            "tier": tier,
            "price": price,
        }
        
        self.handle.write(json.dumps(entry) + "\n")
    
    def close(self):
        """Close log file handle."""
        try:
            self.handle.close()
        except:
            pass


class ConvexityLogger:
    """
    Logs convexity score evolution and tier transitions.
    
    Events: score_update, promotion, demotion, false_signal
    
    Organized by phase:
        runtime/logs/{phase}/convexity_{date}.log
    """
    
    def __init__(self, phase: str):
        self.phase = phase
        
        log_dir = Path(f"runtime/logs/{phase}")
        log_dir.mkdir(parents=True, exist_ok=True)
        
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_path = log_dir / f"convexity_{date_str}.log"
        
        self.handle = log_path.open("a", buffering=1)
    
    def log(self, event: str, symbol: str, **kwargs):
        """
        Log a convexity event.
        
        Args:
            event: "score_update" | "promotion" | "demotion" | "false_signal"
            symbol: Ticker symbol
            **kwargs: Additional context (score, old_tier, new_tier, reason, etc.)
        """
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "phase": self.phase,
            "event": event,
            "symbol": symbol,
            **kwargs
        }
        
        self.handle.write(json.dumps(entry) + "\n")
    
    def close(self):
        try:
            self.handle.close()
        except:
            pass