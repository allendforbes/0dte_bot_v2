# bot_0dte/ui/ascii_renderer.py
from __future__ import annotations
from typing import Any, Dict

CLEAR = "\033[2J\033[H"


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


def _pre(s: Dict[str, Any]) -> None:
    """Render PRE_TRADE phase: calm market watchlist."""
    
    # ================================================================
    # ENTRY DEBUG HUD (observability only)
    # ================================================================
    debug = s.get("entry_debug", {})
    
    print(f"ENTRY_STATE:  {debug.get('state','—')}")
    
    if debug.get("bias"):
        print(f"BIAS:         {debug.get('bias')}")
        print(f"HOLD_BARS:    {debug.get('hold_bars',0)}")
    
    rd = debug.get("reclaim_dist")
    if rd is not None:
        print(f"VWAP_DIST:    {rd:+.3%}")
    
    print("")
    
    # ================================================================
    # MAIN UI
    # ================================================================
    print(f"STATE:        {s.get('state','HUNTING')}")
    print(f"SESSION:      {s.get('session','—')}")
    print("")
    print("SYMBOL   PRICE     VWAP     DEV       TREND")
    print("-" * 50)

    for row in s.get("watchlist", []):
        print(
            f"{row['symbol']:<6} "
            f"{row['price']:>9.2f} "
            f"{row['vwap']:>8.2f} "
            f"{row['dev']:>+7.3%} "
            f"{row.get('trend','—'):>5}"
        )


def _in(s: Dict[str, Any]) -> None:
    """Render IN_TRADE phase: active trade panel."""
    print(f"STATE:        {s.get('state','IN TRADE')}")
    print(f"SYMBOL:       {s.get('symbol','—')}")
    print(f"BIAS:         {s.get('bias','—')}")
    print(f"SIGNAL:       {s.get('signal','—')}")
    print(f"REGIME:       {s.get('regime','—')}")
    print("")
    print(f"ENTRY:        ${s.get('entry',0.0):.2f}")
    print(f"MID:          ${s.get('mid',0.0):.2f}")
    print(f"PNL:          {s.get('pnl_pct',0.0):+.1f}%   ({s.get('r_multiple',0.0):+.2f}R)")
    print("")
    trail_active = s.get("trail_active", False)
    trail_status = "ACTIVE" if trail_active else "OFF"
    print(f"TRAIL:        ${s.get('trail',0.0):.2f}   [{trail_status}]")
    print(f"CONVEXITY:    {s.get('convexity','—')}")


def _post(s: Dict[str, Any]) -> None:
    """Render POST_TRADE phase: last trade summary."""
    print(f"STATE:        {s.get('state', 'LAST TRADE')}")
    print("")
    print(f"RESULT:       {s.get('r_multiple',0.0):+.2f}R   ({s.get('pnl_pct',0.0):+.1f}%)")
    print(f"EXIT:         {s.get('exit_reason','—')}")
    print(f"DURATION:     {s.get('duration','—')}")
    
    cooldown = s.get("cooldown_remaining_s", 0.0)
    if cooldown > 0:
        print(f"COOLDOWN:     {cooldown:.0f}s")