# bot_0dte/infra/ui_snapshot.py
from __future__ import annotations
from typing import Any, Dict
import time

# Trading phase enum for PRE/IN/POST states
from bot_0dte.infra.trading_phase import TradingPhase


def build_ui_snapshot(orch: Any) -> Dict[str, Any]:
    """
    Read-only projection of orchestrator state for UI rendering.
    MUST be side-effect free.
    
    Args:
        orch: Orchestrator instance with state
    
    Returns:
        Dict with phase-appropriate data for rendering
    """
    
    # Use trading_phase (PRE/IN/POST), not execution_phase (SHADOW/PAPER/LIVE)
    phase = orch.trading_phase
    
    if phase == TradingPhase.PRE_TRADE:
        return _pre_trade(orch)
    
    if phase == TradingPhase.IN_TRADE:
        return _in_trade(orch)
    
    if phase == TradingPhase.POST_TRADE:
        return _post_trade(orch)
    
    # Fallback (should never happen)
    return {"phase": "UNKNOWN", "error": f"Invalid phase: {phase}"}


def _pre_trade(orch: Any) -> Dict[str, Any]:
    """
    Build snapshot for PRE_TRADE phase.
    Shows calm market watchlist view with price/vwap/deviation/trend.
    """
    rows = []
    
    for sym in orch.symbols:
        # Get last price
        price = orch.last_price.get(sym)
        if price is None:
            continue
        
        # Get VWAP data (read-only, no mutation)
        vwap_tracker = orch.vwap.get(sym)
        if vwap_tracker:
            vwap_data = vwap_tracker.current
            vwap = vwap_data.get("vwap")
            if vwap is None:
                vwap = price
                dev = 0.0
                trend = "→"
            else:
                dev = (price - vwap) / vwap if vwap else 0.0
                
                # Trend character based on deviation change
                dev_change = vwap_data.get("dev_change", 0.0)
                if dev_change > 0.0001:
                    trend = "↑"
                elif dev_change < -0.0001:
                    trend = "↓"
                else:
                    trend = "→"
        else:
            vwap = price
            dev = 0.0
            trend = "→"
        
        rows.append({
            "symbol": sym,
            "price": float(price),
            "vwap": float(vwap),
            "dev": float(dev),
            "trend": trend,
        })
    
    # Determine session label
    try:
        from datetime import datetime
        import pytz
        tz = pytz.timezone("US/Eastern")
        now_et = datetime.now(tz)
        hour = now_et.hour
        minute = now_et.minute
        
        if hour < 9 or (hour == 9 and minute < 30):
            session = "PRE"
        elif hour >= 16:
            session = "CLOSE"
        else:
            session = "OPEN"
    except:
        session = "OPEN"
    
    return {
        "phase": "PRE_TRADE",
        "state": "HUNTING",
        "session": session,
        "watchlist": rows,
    }


def _in_trade(orch: Any) -> Dict[str, Any]:
    """
    Build snapshot for IN_TRADE phase.
    Shows single trade panel with frozen signal, PnL, trail state, convexity.
    Uses trade_view property for clean access.
    """
    
    trade = orch.trade_view
    if not trade:
        # Defensive: should not happen in IN_TRADE phase
        return {"phase": "IN_TRADE", "error": "No active trade"}
    
    sym = trade["symbol"]
    entry = float(trade["entry"])
    
    # Get current mid price from chain
    chain_rows = orch.chain_agg.get_chain(sym)
    contract_row = next(
        (r for r in chain_rows if r["contract"] == trade["contract"]),
        None
    )
    
    if contract_row:
        bid = contract_row.get("bid")
        ask = contract_row.get("ask")
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        else:
            mid = entry  # fallback
    else:
        mid = entry  # fallback
    
    mid = float(mid)
    
    # PnL %
    pnl_pct = ((mid - entry) / entry) * 100.0 if entry else 0.0
    
    # R multiple from canonical oneR (persisted at entry)
    oneR = float(trade.get("oneR", 0.0))
    r_mult = ((mid - entry) / oneR) if oneR else 0.0
    
    # Trail state
    trail_price = float(trade.get("trail_price", 0.0))
    trail_active = bool(trade.get("trail_active", False))
    
    # Convexity label (tier)
    convexity_label = str(trade.get("grade", "L0"))
    
    # Signal info (frozen at entry)
    bias = str(trade.get("bias", "—"))
    signal = bias  # Simple display
    
    # Regime (placeholder for now)
    regime = "—"
    
    return {
        "phase": "IN_TRADE",
        "state": "IN TRADE",
        "symbol": sym,
        "bias": bias,
        "signal": signal,
        "regime": regime,
        
        "entry": entry,
        "mid": mid,
        "pnl_pct": pnl_pct,
        "r_multiple": r_mult,
        
        "trail": trail_price,
        "trail_active": trail_active,
        "convexity": convexity_label,
    }


def _post_trade(orch: Any) -> Dict[str, Any]:
    """
    Build snapshot for POST_TRADE phase.
    Shows last trade summary with PnL, exit reason, duration.
    Uses last_trade_view stored at exit.
    """
    
    last = orch.last_trade_view
    if not last:
        # Defensive: should not happen in POST_TRADE phase
        return {"phase": "POST_TRADE", "error": "No last trade data"}
    
    # Calculate duration (placeholder until we add entry timestamp)
    duration_str = "—"
    
    # Calculate cooldown remaining
    cooldown_remaining_s = 0.0
    if orch._post_trade_ts:
        elapsed = time.monotonic() - orch._post_trade_ts
        cooldown_remaining_s = max(0.0, 4.0 - elapsed)
    
    return {
        "phase": "POST_TRADE",
        "state": "LAST TRADE",
        "pnl_pct": float(last.get("pnl_pct", 0.0)),
        "r_multiple": float(last.get("r_multiple", 0.0)),
        "exit_reason": str(last.get("reason", "—")),
        "duration": duration_str,
        "cooldown_remaining_s": float(cooldown_remaining_s),
    }