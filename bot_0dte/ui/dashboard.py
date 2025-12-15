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


def _fmt(x: Any) -> str:
    """
    Safely format a value for display, handling None and type errors.
    
    Args:
        x: Value to format (may be None, float, int, or other)
        
    Returns:
        Formatted string, or "--" if value cannot be formatted
    """
    if x is None:
        return "--"
    if isinstance(x, (int, float)):
        try:
            return f"{x:.2f}"
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
        left = Layout(name="left_container")
        left.split_column(
            Layout(self.market_panel, name="market", ratio=3),
            Layout(self.signal_panel, name="signal", ratio=2),
            Layout(self.trade_panel, name="trade", ratio=2),
        )
        self.layout["left"].update(left)
        
        # Initialize log stream
        self.log_lines: List[str] = []
        self.max_logs = 150
        self.layout["right"].update(Panel("", title="📝 LOG STREAM"))
        
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
        
        return self.layout
    
    # -------------------------------------------------
    # Lifecycle
    # -------------------------------------------------
    
    def start(self):
        """Start the live rendering loop in a background thread."""
        try:
            # Create Live instance lazily (CRITICAL: only after event loop exists)
            if self.live is None:
                self.live = Live(self, refresh_per_second=5)
            
            # CRITICAL: Live.start() is BLOCKING - must run in background thread
            import threading
            self._thread = threading.Thread(
                target=self.live.start,
                daemon=True
            )
            self._thread.start()
        except Exception as e:
            print(f"[UI] Dashboard start failed: {e}")
    
    def stop(self):
        """Stop the live rendering and restore cursor."""
        try:
            if self.live is not None:
                self.live.stop()
                # Give thread time to clean up
                if hasattr(self, '_thread') and self._thread.is_alive():
                    self._thread.join(timeout=1.0)
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
        """
        try:
            rows = self.market_state.snapshot()
            table = Table(box=None, expand=True)
            table.add_column("Symbol")
            table.add_column("Price", justify="right")
            table.add_column("Bid", justify="right")
            table.add_column("Ask", justify="right")
            table.add_column("Signal")
            table.add_column("Strike")
            
            for r in rows:
                table.add_row(
                    str(r.get("symbol", "")),
                    _fmt(r.get("price")),
                    _fmt(r.get("bid")),
                    _fmt(r.get("ask")),
                    str(r.get("signal", "--")),
                    str(r.get("strike", "--")),
                )
            
            self.market_panel = Panel(table, title="📡 LIVE MARKET")
            # Defensive access; keys exist because we created them in __init__
            left = self.layout["left"]
            left["market"].update(self.market_panel)
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
            self.layout["left"]["signal"].update(self.signal_panel)
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
            self.layout["left"]["trade"].update(self.trade_panel)
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
                Panel("\n".join(self.log_lines[-self.max_logs:]), title="📝 LOG STREAM")
            )
        except Exception as e:
            # UI-only failure should never bubble
            print(f"[UI] push_log error: {e}")