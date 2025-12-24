"""
Live dashboard UI for Elite Orchestrator.

UI-ONLY CODE - NO TRADING LOGIC.

This module uses the __rich__ pattern for automatic pull-based updates.
"""

from typing import Any, List, Dict

from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.console import Console
from rich.text import Text


def _fmt(x: Any, decimals: int = 2) -> str:
    """
    Safely format a value for display, handling None and type errors.
    
    Args:
        x: Value to format (may be None, float, int, or other)
        decimals: Number of decimal places (default 2)
        
    Returns:
        Formatted string, or "--" if value cannot be formatted
    """
    if x is None:
        return "--"
    if isinstance(x, (int, float)):
        try:
            return f"{x:.{decimals}f}"
        except Exception:
            return "--"
    return str(x)


class LiveDashboard:
    """
    Rich-based live dashboard with __rich__ pattern for automatic updates.
    
    Architecture:
    - Live(self, refresh_per_second=5) calls __rich__() automatically
    - __rich__() pulls state from MarketStatePublisher and returns layout
    - Layout structure created once in __init__, never recreated
    - No Rich calls in async callbacks
    
    Display phases:
    - PRE-TRADE: Show price, VWAP dev (rounded), signal if detected
    - IN-TRADE: Show contract bid/ask, PnL, trail level, tier
    - POST-TRADE: Show exit reason and final PnL
    """
    
    def __init__(self, console: Console, market_state):
        """
        Initialize dashboard with stable layout structure.
        
        Args:
            console: Rich console for rendering
            market_state: MarketStatePublisher instance (for pulling snapshots)
        """
        self.console = console
        self.market_state = market_state
        
        # Initialization guard (prevent __rich__ recursion during setup)
        self._initialized = False
        
        # Build layout ONCE
        self.layout = Layout()
        self.layout.split_row(
            Layout(name="left", ratio=2),
            Layout(name="right", ratio=3)
        )
        
        # Initialize panels
        self.market_panel = Panel("Loading...", title="📡 LIVE MARKET")
        self.signal_panel = Panel("Waiting signal...", title="📊 SIGNAL PIPELINE")
        self.trade_panel = Panel("No active trade", title="🟢 ACTIVE TRADE")
        
        # Create left column layout with children
        self.layout["left"].split_column(
            Layout(self.market_panel, name="market", ratio=3),
            Layout(self.signal_panel, name="signal", ratio=2),
            Layout(self.trade_panel, name="trade", ratio=2),
        )

        # Initialize log stream
        self.log_lines: List[str] = []
        self.max_logs = 150
        self.layout["right"].update(Panel("", title="📋 LOG STREAM"))
        
        # Repaint suppression (avoid flickering on micro-changes)
        self._last_snapshot_hash = None
        
        # Mark as initialized
        self._initialized = True
        
        # IMPORTANT: Live is created lazily in start() to avoid event loop issues
        self.live = None
    
    # -------------------------------------------------
    # __rich__ pattern — called automatically by Live
    # -------------------------------------------------
    
    def __rich__(self):
        """
        Called by Rich Live at ~5 FPS.
        Pulls fresh state and returns updated layout.
        """
        # Guard against recursion during initialization
        if not getattr(self, '_initialized', False):
            return self.layout
        
        try:
            self.refresh_market()
        except Exception as e:
            # Never crash the render loop
            print(f"[UI] __rich__ error: {e}")
            import traceback
            traceback.print_exc()
        
        return self.layout
    
    # -------------------------------------------------
    # Lifecycle (CORRECT – NO THREADS)
    # -------------------------------------------------

    def start(self):
        """
        Start Rich Live rendering.
        MUST run in the main thread.
        """
        if self.live is not None:
            return  # already started

        self.live = Live(
            self,
            refresh_per_second=5,
            console=self.console,
            transient=False,
        )
        self.live.start()
        print("[UI] Dashboard live rendering started")


    def stop(self):
        """
        Stop Rich Live rendering cleanly.
        """
        if self.live:
            self.live.stop()
            self.live = None
            print("[UI] Dashboard live rendering stopped")
  
    def stop(self):
        """Stop the live rendering and restore cursor."""
        try:
            # Signal thread to stop
            self._stop_requested = True
            
            # Wait for thread to finish
            if hasattr(self, '_thread') and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            
            # Live context manager handles cleanup automatically
            
        except Exception:
            pass
        finally:
            # Failsafe: restore cursor even if stop() fails
            print("\033[?25h")
    
    # -------------------------------------------------
    # Market Panel (pull-based, called from __rich__)
    # -------------------------------------------------
    
    def refresh_market(self) -> None:
        """
        Pull market snapshot from publisher and render.
        Called automatically by __rich__().
        
        Display logic:
        - PRE-TRADE: Show price, signal if any
        - IN-TRADE: Show contract bid/ask (if available)
        - Always show signal/strike when detected
        """
        try:
            rows = self.market_state.snapshot()
            
            # Suppress repaint if nothing material changed
            snapshot_hash = hash(str([(r.get("symbol"), r.get("price"), r.get("bid"), 
                                       r.get("ask"), r.get("signal")) for r in rows]))
            
            if snapshot_hash == self._last_snapshot_hash:
                return
            
            self._last_snapshot_hash = snapshot_hash
            
            # Build table
            table = Table(box=None, expand=True)
            table.add_column("Symbol", style="bold")
            table.add_column("Price", justify="right")
            table.add_column("Bid", justify="right")
            table.add_column("Ask", justify="right")
            table.add_column("Signal", justify="center")
            table.add_column("Strike", justify="right")
            
            for r in rows:
                # Determine if in-trade (bid/ask populated)
                in_trade = r.get("bid") is not None and r.get("ask") is not None
                
                # Signal display
                signal_str = str(r.get("signal", "--"))
                if signal_str != "--":
                    if signal_str == "CALL":
                        signal_str = "[green]CALL[/green]"
                    elif signal_str == "PUT":
                        signal_str = "[red]PUT[/red]"
                
                # Strike display (defensive: handle numeric or string)
                strike = r.get("strike")
                strike_str = _fmt(strike, decimals=0) if isinstance(strike, (int, float)) else "--"
                
                table.add_row(
                    str(r.get("symbol", "")),
                    _fmt(r.get("price"), decimals=2),
                    _fmt(r.get("bid"), decimals=2) if in_trade else "--",
                    _fmt(r.get("ask"), decimals=2) if in_trade else "--",
                    signal_str,
                    strike_str,
                )
            
            self.market_panel = Panel(table, title="📡 LIVE MARKET")
            self.layout["left"]["left_container"]["market"].update(self.market_panel)
            
        except Exception as e:
            # UI-only failure should never bubble
            print(f"[UI] refresh_market error: {e}")
    
    # -------------------------------------------------
    # Signal Panel
    # -------------------------------------------------
    
    def update_signal(self, signal_text: str, cont_text: str = "") -> None:
        """
        Update signal pipeline panel.
        
        Args:
            signal_text: Primary signal text
            cont_text: Continuation text (optional)
        """
        try:
            combined = signal_text
            if cont_text:
                combined += "\n" + cont_text
            
            self.signal_panel = Panel(
                Text(combined),
                title="📊 SIGNAL PIPELINE",
            )
            self.layout["left"]["left_container"]["signal"].update(self.signal_panel)
        except Exception as e:
            print(f"[UI] update_signal error: {e}")
    
    # -------------------------------------------------
    # Trade Panel
    # -------------------------------------------------
    
    def update_trade(self, panel_text: str) -> None:
        """
        Update active trade panel.
        
        Args:
            panel_text: Trade status text
        """
        try:
            self.trade_panel = Panel(panel_text, title="🟢 ACTIVE TRADE")
            self.layout["left"]["left_container"]["trade"].update(self.trade_panel)

        except Exception as e:
            print(f"[UI] update_trade error: {e}")
    
    # -------------------------------------------------
    # Log Stream
    # -------------------------------------------------
    
    def push_log(self, line: str) -> None:
        """
        Add a log line to the stream.
        
        Args:
            line: Log text to append
        """
        try:
            if len(self.log_lines) >= self.max_logs:
                self.log_lines.pop(0)
            
            self.log_lines.append(line)
            
            self.layout["right"].update(
                Panel("\n".join(self.log_lines[-self.max_logs:]), title="📋 LOG STREAM")
            )
        except Exception as e:
            # UI-only failure should never bubble
            print(f"[UI] push_log error: {e}")