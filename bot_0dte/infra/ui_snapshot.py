"""
UI Snapshot Builder v2.0 (REFACTORED)

Read-only projection of orchestrator state for UI rendering.

FIXES:
    - Proper VWAP display (uses VWAPResult from tracker)
    - Entry debug HUD with hold_bars, alignment state
    - Clean data flow (no side effects)
    - Graceful fallbacks for missing data

CHANGELOG:
    - FIXED: _get_entry_debug now handles missing get_debug_state method gracefully

Architecture:
    build_ui_snapshot(orch) → Dict[str, Any]
    
    MUST be side-effect free.
    Returns phase-appropriate data for ASCII renderer.
"""

from __future__ import annotations
from typing import Any, Dict, Optional
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


def _get_entry_debug(orch: Any, symbol: str) -> Dict[str, Any]:
    """
    Build entry debug HUD data for a symbol.
    
    Shows mandate engine state for observability:
        - Current bias
        - Hold bars progress
        - Alignment state
        - VWAP distance
    
    FIXED: Gracefully handles SessionMandateEngine without get_debug_state()
    """
    debug = {
        "state": "HUNTING",
        "bias": None,
        "hold_bars": 0,
        "hold_bars_required": 2,
        "aligned": False,
        "reclaim_dist": None,
    }
    
    # Get mandate engine state - with graceful fallback
    if hasattr(orch, "mandate_engine"):
        mandate_engine = orch.mandate_engine
        
        # ✅ FIXED: Check if get_debug_state exists, otherwise use _acceptance directly
        if hasattr(mandate_engine, "get_debug_state"):
            mandate_state = mandate_engine.get_debug_state(symbol)
            acceptance = mandate_state.get("acceptance")
            
            if acceptance:
                debug["bias"] = acceptance.get("bias")
                debug["hold_bars"] = acceptance.get("hold_bars", 0)
                debug["aligned"] = acceptance.get("last_aligned", False)
            
            config = mandate_state.get("config", {})
            debug["hold_bars_required"] = config.get("hold_bars_required", 2)
            
            if mandate_state.get("cooldown_active"):
                debug["state"] = "COOLDOWN"
            elif debug["bias"]:
                debug["state"] = f"BIAS:{debug['bias']}"
        
        elif hasattr(mandate_engine, "_acceptance"):
            # ✅ Fallback: Access internal state directly
            acceptance = mandate_engine._acceptance.get(symbol, {})
            debug["bias"] = acceptance.get("bias")
            debug["hold_bars"] = acceptance.get("hold_bars", 0)
            debug["aligned"] = acceptance.get("last_hold_ts") is not None
            
            # Get hold_bars_required from class constant
            debug["hold_bars_required"] = getattr(
                mandate_engine, "HOLD_BARS_REQUIRED", 2
            )
            
            # Check cooldown
            last_exit = getattr(mandate_engine, "_last_exit_ts", None)
            cooldown_sec = getattr(mandate_engine, "POST_EXIT_COOLDOWN_SEC", 30.0)
            if last_exit and (time.monotonic() - last_exit) < cooldown_sec:
                debug["state"] = "COOLDOWN"
            elif debug["bias"]:
                debug["state"] = f"BIAS:{debug['bias']}"
    
    # Get VWAP distance
    price = orch.last_price.get(symbol)
    
    # Try to get VWAP from tracker manager
    if hasattr(orch, "vwap_manager"):
        tracker = orch.vwap_manager.get_tracker(symbol)
        if tracker and tracker.current_vwap:
            vwap = tracker.current_vwap
            if price and vwap:
                debug["reclaim_dist"] = (price - vwap) / vwap
    elif hasattr(orch, "vwap"):
        # Fallback to old vwap dict
        vwap = orch.vwap.get(symbol)
        if price and vwap:
            debug["reclaim_dist"] = (price - vwap) / vwap
    
    return debug


def _pre_trade(orch: Any) -> Dict[str, Any]:
    """
    Build snapshot for PRE_TRADE phase.
    Shows calm market watchlist view with price/vwap/deviation/trend.
    """
    rows = []
    entry_debug = {}
    
    for sym in orch.symbols:
        # Get last price
        price = orch.last_price.get(sym)
        if price is None:
            continue
        
        # Get VWAP data - FIX: Properly handle VWAP tracker
        vwap = None
        dev = 0.0
        trend = "↔"
        
        # Try VWAP tracker manager first
        if hasattr(orch, "vwap_manager"):
            tracker = orch.vwap_manager.get_tracker(sym)
            if tracker and tracker.is_valid:
                vwap = tracker.current_vwap
                dev = tracker.current_dev / vwap if vwap else 0.0
                
                # Trend from deviation sign
                if dev > 0.0001:
                    trend = "↑"
                elif dev < -0.0001:
                    trend = "↓"
        
        # Fallback to old vwap dict
        if vwap is None and hasattr(orch, "vwap"):
            vwap = orch.vwap.get(sym)
            if vwap and vwap > 0:
                dev = (price - vwap) / vwap
                if dev > 0.0001:
                    trend = "↑"
                elif dev < -0.0001:
                    trend = "↓"
        
        # Final fallback
        if vwap is None:
            vwap = price
            dev = 0.0
            trend = "↔"
        
        rows.append({
            "symbol": sym,
            "price": float(price),
            "vwap": float(vwap),
            "dev": float(dev),
            "trend": trend,
        })
        
        # Build entry debug for first symbol (primary)
        if not entry_debug:
            entry_debug = _get_entry_debug(orch, sym)
    
    # Determine session label
    session = _get_session_label()
    
    return {
        "phase": "PRE_TRADE",
        "state": entry_debug.get("state", "HUNTING"),
        "session": session,
        "watchlist": rows,
        "entry_debug": entry_debug,
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
    mid = entry  # Default fallback
    
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
    
    mid = float(mid)
    
    # PnL %
    pnl_pct = ((mid - entry) / entry) * 100.0 if entry else 0.0
    
    # R multiple from canonical oneR (persisted at entry)
    oneR = float(trade.get("oneR", 0.0))
    r_mult = ((mid - entry) / oneR) if oneR else 0.0
    
    # Trail state
    trail_price = float(trade.get("trail", 0.0))
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
    
    # Calculate duration
    duration_str = "—"
    if "entry_ts" in last and "ts" in last:
        duration_sec = last["ts"] - last.get("entry_ts", last["ts"])
        if duration_sec > 0:
            minutes = int(duration_sec // 60)
            seconds = int(duration_sec % 60)
            duration_str = f"{minutes}m{seconds}s"
    
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


def _get_session_label() -> str:
    """Determine current market session label."""
    try:
        from datetime import datetime
        import pytz
        tz = pytz.timezone("US/Eastern")
        now_et = datetime.now(tz)
        hour = now_et.hour
        minute = now_et.minute
        
        if hour < 9 or (hour == 9 and minute < 30):
            return "PRE"
        elif hour >= 16:
            return "CLOSE"
        else:
            return "OPEN"
    except Exception:
        return "OPEN"