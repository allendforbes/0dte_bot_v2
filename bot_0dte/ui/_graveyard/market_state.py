"""
UI-only market state publisher.

This class is intentionally boring and safe:
- No async
- No Rich imports
- No formatting
- No strategy logic
- No side effects

It exists solely to decouple UI rendering from async WebSocket callbacks.
"""

from typing import Dict, Any, List, Optional


class MarketStatePublisher:
    """
    Lightweight state holder for UI-facing market data.
    
    Responsibilities:
    - Maintain one row per symbol
    - Accept incremental updates (price, bid, ask, signal, strike)
    - Expose a snapshot for UI rendering
    
    Guarantees:
    - Never raises exceptions
    - Never formats values
    - Never mutates outside its own state
    """
    
    def __init__(self, symbols: List[str]):
        """
        Initialize publisher with empty state for each symbol.
        
        Args:
            symbols: List of ticker symbols to track
        """
        self._rows: Dict[str, Dict[str, Any]] = {}
        
        for symbol in symbols:
            self._rows[symbol] = {
                "symbol": symbol,
                "price": None,
                "bid": None,
                "ask": None,
                "signal": "--",
                "strike": "--",
            }
    
    def update_price(self, symbol: str, price: Optional[float]) -> None:
        """
        Update underlying price for a symbol.
        
        Args:
            symbol: Ticker symbol
            price: New price value (or None)
        """
        if symbol not in self._rows:
            return
        
        self._rows[symbol]["price"] = price
    
    def update_nbbo(
        self,
        symbol: str,
        bid: Optional[float] = None,
        ask: Optional[float] = None
    ) -> None:
        """
        Update option NBBO for a symbol.
        
        Args:
            symbol: Ticker symbol
            bid: New bid value (or None)
            ask: New ask value (or None)
        """
        if symbol not in self._rows:
            return
        
        if bid is not None:
            self._rows[symbol]["bid"] = bid
        
        if ask is not None:
            self._rows[symbol]["ask"] = ask
    
    def update_signal(
        self,
        symbol: str,
        signal: Optional[str] = None,
        strike: Optional[Any] = None
    ) -> None:
        """
        Update signal and strike information for a symbol.
        
        Args:
            symbol: Ticker symbol
            signal: Signal string (e.g., "CALL", "PUT", "--")
            strike: Strike value (any type, stored as-is)
        """
        if symbol not in self._rows:
            return
        
        if signal is not None:
            self._rows[symbol]["signal"] = signal
        
        if strike is not None:
            self._rows[symbol]["strike"] = strike
    
    def snapshot(self) -> List[Dict[str, Any]]:
        """
        Return a snapshot of all market rows for UI rendering.
        
        Returns:
            List of row dictionaries, one per symbol
            
        Note:
            Values may be None if not yet updated.
            UI layer is responsible for formatting.
        """
        return list(self._rows.values())