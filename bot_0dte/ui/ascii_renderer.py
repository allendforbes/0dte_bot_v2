"""
ASCII Renderer v2.0 (REFACTORED)

Stateless ASCII renderer for terminal UI.

FIXES:
    - Enhanced entry debug HUD
    - Proper hold_bars display
    - VWAP deviation display
    - Clean formatting

Architecture:
    render(snapshot) → prints to stdout
    
    Takes snapshot dict from build_ui_snapshot().
    Clears screen and prints phase-appropriate view.
"""

from __future__ import annotations
from typing import Any, Dict

# ANSI escape codes
CLEAR = "\033[2J\033[H"
BOLD = "\033[1m"
RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"


def render(s: Dict[str, Any]) -> None:
    """
    Stateless ASCII renderer.
    Clears screen and prints phase-appropriate view.
    
    Args:
        s: Snapshot dict from build_ui_snapshot()
    """
    print(CLEAR, end="")

    phase = s.get("phase")

    if phase == "PRE_TRADE":
        _pre(s)
    elif phase == "IN_TRADE":
        _in(s)
    elif phase == "POST_TRADE":
        _post(s)
    else:
        print(f"STATE:        UNKNOWN ({phase})")
        if "error" in s:
            print(f"ERROR:        {s['error']}")


def _format_pct(val: float, width: int = 7) -> str:
    """Format percentage with color."""
    if val > 0.0001:
        return f"{GREEN}{val:>+{width}.3%}{RESET}"
    elif val < -0.0001:
        return f"{RED}{val:>+{width}.3%}{RESET}"
    return f"{val:>+{width}.3%}"


def _format_price(val: float, width: int = 9) -> str:
    """Format price."""
    return f"{val:>{width}.2f}"


def _pre(s: Dict[str, Any]) -> None:
    """Render PRE_TRADE phase: calm market watchlist with entry debug."""
    
    # ================================================================
    # ENTRY DEBUG HUD (observability only)
    # ================================================================
    debug = s.get("entry_debug", {})
    
    state = debug.get("state", "HUNTING")
    bias = debug.get("bias")
    hold_bars = debug.get("hold_bars", 0)
    hold_bars_req = debug.get("hold_bars_required", 2)
    aligned = debug.get("aligned", False)
    reclaim_dist = debug.get("reclaim_dist")
    
    # State line with color
    if state == "COOLDOWN":
        print(f"ENTRY_STATE:  {YELLOW}{state}{RESET}")
    elif bias:
        color = GREEN if bias == "CALL" else RED
        print(f"ENTRY_STATE:  {color}{state}{RESET}")
    else:
        print(f"ENTRY_STATE:  {state}")
    
    # Bias line
    if bias:
        color = GREEN if bias == "CALL" else RED
        print(f"BIAS:         {color}{bias}{RESET}")
        
        # Hold bars progress
        bars_filled = min(hold_bars, hold_bars_req)
        bars_empty = hold_bars_req - bars_filled
        bar_display = "█" * bars_filled + "░" * bars_empty
        
        aligned_str = f"{GREEN}YES{RESET}" if aligned else f"{RED}NO{RESET}"
        print(f"HOLD_BARS:    [{bar_display}] {hold_bars}/{hold_bars_req}  aligned={aligned_str}")
    
    # VWAP distance
    if reclaim_dist is not None:
        dist_str = _format_pct(reclaim_dist)
        print(f"VWAP_DIST:    {dist_str}")
    
    print("")
    
    # ================================================================
    # MAIN UI - WATCHLIST
    # ================================================================
    session = s.get("session", "—")
    print(f"SESSION:      {session}")
    print("")
    print(f"{'SYMBOL':<6} {'PRICE':>9} {'VWAP':>9} {'DEV':>9} {'TREND':>5}")
    print("-" * 45)

    for row in s.get("watchlist", []):
        sym = row['symbol']
        price = _format_price(row['price'])
        vwap = _format_price(row['vwap'])
        dev = _format_pct(row['dev'])
        trend = row.get('trend', '→')
        
        # Color trend arrow
        if trend == "↑":
            trend_str = f"{GREEN}{trend}{RESET}"
        elif trend == "↓":
            trend_str = f"{RED}{trend}{RESET}"
        else:
            trend_str = trend
        
        print(f"{sym:<6} {price} {vwap} {dev} {trend_str:>5}")


def _in(s: Dict[str, Any]) -> None:
    """Render IN_TRADE phase: active trade panel."""
    
    print(f"{BOLD}═══════════════════════════════════════════{RESET}")
    print(f"{BOLD}           ACTIVE TRADE                    {RESET}")
    print(f"{BOLD}═══════════════════════════════════════════{RESET}")
    print("")
    
    sym = s.get('symbol', '—')
    bias = s.get('bias', '—')
    
    # Color bias
    if bias == "CALL":
        bias_str = f"{GREEN}{bias}{RESET}"
    elif bias == "PUT":
        bias_str = f"{RED}{bias}{RESET}"
    else:
        bias_str = bias
    
    print(f"SYMBOL:       {sym}")
    print(f"BIAS:         {bias_str}")
    print(f"SIGNAL:       {s.get('signal', '—')}")
    print(f"REGIME:       {s.get('regime', '—')}")
    print("")
    
    entry = s.get('entry', 0.0)
    mid = s.get('mid', 0.0)
    pnl_pct = s.get('pnl_pct', 0.0)
    r_mult = s.get('r_multiple', 0.0)
    
    # Color PnL
    if pnl_pct > 0:
        pnl_str = f"{GREEN}{pnl_pct:+.1f}%{RESET}"
        r_str = f"{GREEN}{r_mult:+.2f}R{RESET}"
    elif pnl_pct < 0:
        pnl_str = f"{RED}{pnl_pct:+.1f}%{RESET}"
        r_str = f"{RED}{r_mult:+.2f}R{RESET}"
    else:
        pnl_str = f"{pnl_pct:+.1f}%"
        r_str = f"{r_mult:+.2f}R"
    
    print(f"ENTRY:        ${entry:.2f}")
    print(f"MID:          ${mid:.2f}")
    print(f"PNL:          {pnl_str}   ({r_str})")
    print("")
    
    trail = s.get('trail', 0.0)
    trail_active = s.get('trail_active', False)
    trail_status = f"{GREEN}ACTIVE{RESET}" if trail_active else f"{YELLOW}OFF{RESET}"
    
    print(f"TRAIL:        ${trail:.2f}   [{trail_status}]")
    print(f"CONVEXITY:    {s.get('convexity', '—')}")


def _post(s: Dict[str, Any]) -> None:
    """Render POST_TRADE phase: last trade summary."""
    
    print(f"{BOLD}═══════════════════════════════════════════{RESET}")
    print(f"{BOLD}           LAST TRADE                      {RESET}")
    print(f"{BOLD}═══════════════════════════════════════════{RESET}")
    print("")
    
    pnl_pct = s.get('pnl_pct', 0.0)
    r_mult = s.get('r_multiple', 0.0)
    
    # Color result
    if r_mult > 0:
        result_str = f"{GREEN}{r_mult:+.2f}R{RESET}   ({GREEN}{pnl_pct:+.1f}%{RESET})"
    elif r_mult < 0:
        result_str = f"{RED}{r_mult:+.2f}R{RESET}   ({RED}{pnl_pct:+.1f}%{RESET})"
    else:
        result_str = f"{r_mult:+.2f}R   ({pnl_pct:+.1f}%)"
    
    print(f"RESULT:       {result_str}")
    print(f"EXIT:         {s.get('exit_reason', '—')}")
    print(f"DURATION:     {s.get('duration', '—')}")
    
    cooldown = s.get("cooldown_remaining_s", 0.0)
    if cooldown > 0:
        print(f"COOLDOWN:     {YELLOW}{cooldown:.0f}s{RESET}")


# ================================================================
# COMPACT RENDERERS (for logging/debugging)
# ================================================================

def render_oneline(s: Dict[str, Any]) -> str:
    """
    Render snapshot as single line for logging.
    
    Returns:
        Single line summary string
    """
    phase = s.get("phase", "?")
    
    if phase == "PRE_TRADE":
        debug = s.get("entry_debug", {})
        bias = debug.get("bias", "-")
        hold = debug.get("hold_bars", 0)
        return f"[PRE] bias={bias} hold={hold}"
    
    elif phase == "IN_TRADE":
        sym = s.get("symbol", "?")
        pnl = s.get("pnl_pct", 0.0)
        return f"[IN] {sym} pnl={pnl:+.1f}%"
    
    elif phase == "POST_TRADE":
        r = s.get("r_multiple", 0.0)
        reason = s.get("exit_reason", "?")
        return f"[POST] {r:+.2f}R ({reason})"
    
    return f"[{phase}]"


def render_watchlist_compact(watchlist: list) -> str:
    """
    Render watchlist as compact multi-line string.
    
    Returns:
        Formatted watchlist string
    """
    lines = []
    for row in watchlist:
        sym = row.get("symbol", "?")
        price = row.get("price", 0.0)
        dev = row.get("dev", 0.0)
        trend = row.get("trend", "→")
        lines.append(f"{sym}: ${price:.2f} ({dev:+.2%}) {trend}")
    return "\n".join(lines)