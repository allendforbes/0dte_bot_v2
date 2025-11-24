#!/bin/bash
# Rebuild minimal, correct __init__.py files for WS-native architecture

set -e

echo "�� Creating clean __init__.py files..."

# ----------------------------------------------------------
# Top-level package: bot_0dte/
# ----------------------------------------------------------
cat > bot_0dte/__init__.py << 'EOF'
"""
bot_0dte - 0DTE Options Trading Bot
WebSocket-native architecture with Massive.com feeds.
"""

__version__ = "2.0.0-ws-native"
__author__  = "Allen"

from bot_0dte.universe import get_universe_for_today, get_expiry_for_symbol
from bot_0dte.sizing import size_from_equity

__all__ = [
    "get_universe_for_today",
    "get_expiry_for_symbol",
    "size_from_equity",
]
EOF

# ----------------------------------------------------------
# Data layer
# ----------------------------------------------------------
mkdir -p bot_0dte/data
cat > bot_0dte/data/__init__.py << 'EOF'
"""
Data Layer - Market data providers and adapters.
WS-native pipeline uses Massive WebSocket providers.
"""

__all__ = ["providers"]
EOF

mkdir -p bot_0dte/data/adapters
cat > bot_0dte/data/adapters/__init__.py << 'EOF'
"""
Legacy adapters (IBKR, REST). Preserved for backward compatibility.
"""
__all__ = []
EOF

mkdir -p bot_0dte/data/providers
cat > bot_0dte/data/providers/__init__.py << 'EOF'
"""
Data Providers - Real-time market data (Massive.com)
"""
__all__ = ["massive"]
EOF

mkdir -p bot_0dte/data/providers/massive
cat > bot_0dte/data/providers/massive/__init__.py << 'EOF'
"""
Massive.com WebSocket Provider
Minimal exports to avoid circular imports.
"""

from .massive_stocks_ws_adapter import MassiveStocksWSAdapter
from .massive_options_ws_adapter import MassiveOptionsWSAdapter

__all__ = [
    "MassiveStocksWSAdapter",
    "MassiveOptionsWSAdapter",
]
EOF

mkdir -p bot_0dte/data/replay
cat > bot_0dte/data/replay/__init__.py << 'EOF'
"""
Replay Engine - WS-native historical playback (to be refactored).
"""
__all__ = []
EOF

# ----------------------------------------------------------
# Execution layer
# ----------------------------------------------------------
mkdir -p bot_0dte/execution
cat > bot_0dte/execution/__init__.py << 'EOF'
"""
Execution Layer - Order execution engine (mock/paper/live)
"""
from .engine import ExecutionEngine

__all__ = ["ExecutionEngine"]
EOF

mkdir -p bot_0dte/execution/adapters
cat > bot_0dte/execution/adapters/__init__.py << 'EOF'
"""
Execution Adapters - IBKR + mock.
"""
from .ibkr_exec import IBKRExecAdapter
from .mock_exec import MockExecAdapter

__all__ = ["IBKRExecAdapter", "MockExecAdapter"]
EOF

# ----------------------------------------------------------
# Strategy layer
# ----------------------------------------------------------
mkdir -p bot_0dte/strategy
cat > bot_0dte/strategy/__init__.py << 'EOF'
"""
Strategy Layer - Signals + trade logic.
"""
from .morning_breakout import MorningBreakout
from .latency_precheck import LatencyPrecheck, PrecheckResult
from .strike_selector import StrikeSelector

__all__ = [
    "MorningBreakout",
    "LatencyPrecheck",
    "PrecheckResult",
    "StrikeSelector",
]
EOF

# ----------------------------------------------------------
# Risk layer
# ----------------------------------------------------------
mkdir -p bot_0dte/risk
cat > bot_0dte/risk/__init__.py << 'EOF'
"""
Risk Management - Trailing stop logic.
"""
from .trail_logic import TrailLogic

__all__ = ["TrailLogic"]
EOF

# ----------------------------------------------------------
# Control layer
# ----------------------------------------------------------
mkdir -p bot_0dte/control
cat > bot_0dte/control/__init__.py << 'EOF'
"""
Control Layer - Legacy SessionController.
Used only for backward compatibility.
"""
from .session_controller import SessionController

__all__ = ["SessionController"]
EOF

# ----------------------------------------------------------
# UI layer
# ----------------------------------------------------------
mkdir -p bot_0dte/ui
cat > bot_0dte/ui/__init__.py << 'EOF'
"""
UI Layer - Live terminal dashboard.
"""
from .live_panel import LivePanel

__all__ = ["LivePanel"]
EOF

# ----------------------------------------------------------
# Simulation layer
# ----------------------------------------------------------
mkdir -p bot_0dte/sim
cat > bot_0dte/sim/__init__.py << 'EOF'
"""
Simulation - WS-native event playback.
"""
__all__ = []
EOF

# ----------------------------------------------------------
# Tests
# ----------------------------------------------------------
mkdir -p bot_0dte/tests
cat > bot_0dte/tests/__init__.py << 'EOF'
"""
Tests - Unit/integration tests.
"""
__all__ = []
EOF

echo "✅ All __init__.py files created and aligned!"
echo "Verify with: find bot_0dte -name '__init__.py' | sort"

