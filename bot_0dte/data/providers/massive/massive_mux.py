"""
MassiveMux v3.6 — FULLY CORRECTED + INSTRUMENTED
------------------------------------------------
✔ Deterministic WS shutdown
✔ No orphan adapters
✔ Proper orchestrator ownership
✔ Clean Ctrl+C exit
✔ INSTRUMENTED: Precise await boundary logging
"""

import asyncio
import logging
from typing import Dict, List
import time

from bot_0dte.contracts.massive_contract_engine import MassiveContractEngine
from bot_0dte.infra.freshness import FreshnessTracker

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class MassiveMux:
    def __init__(self, options_ws=None, ib_underlying=None, loop=None, **kwargs):
        if options_ws is None:
            options_ws = kwargs.pop("options_adapter", None)
        if options_ws is None:
            raise TypeError("MassiveMux requires 'options_ws'.")

        self.options = options_ws
        self.ib = ib_underlying
        self.loop = loop or asyncio.get_event_loop()

        self._parent = None
        self._on_option_cb = None
        self._on_underlying_cb = None

        self.freshness: Dict[str, FreshnessTracker] = {}
        self.engines: Dict[str, MassiveContractEngine] = {}

    # ---------------------------------------------------------
    def set_parent(self, orch):
        """Explicit parent assignment with NO side effects."""
        self._parent = orch

    @property
    def parent_orchestrator(self):
        """Read-only access to parent."""
        return self._parent

    # ---------------------------------------------------------
    def on_option(self, cb):
        """Store callback reference only - no work."""
        self._on_option_cb = cb
        self.options.on_option(cb)

    # ---------------------------------------------------------
    def on_underlying(self, cb):
        """Store callback reference only - NO side effects."""
        self._on_underlying_cb = cb

    # ---------------------------------------------------------
    async def _handle_underlying_event(self, event):
        sym = event.get("symbol")

        eng = self.engines.get(sym)
        if eng:
            try:
                await eng.on_underlying(event)
            except Exception:
                logger.exception("[MUX] Engine underlying handler failed")

        if self._on_underlying_cb:
            try:
                cb = self._on_underlying_cb
                if asyncio.iscoroutinefunction(cb):
                    await cb(event)
                else:
                    cb(event)
            except Exception:
                logger.exception("[MUX] Orchestrator underlying handler failed")

    # ---------------------------------------------------------
    async def connect(self, symbols: List[str], expiry_map: Dict[str, str]):
        import time as time_module
        
        print("[MUX] connect() ENTRY")
        print(f"[MUX] Symbols: {symbols}")
        print(f"[MUX] Expiry map: {expiry_map}")
        
        logger.info("[MUX] Connecting with symbols: %s", symbols)
        
        # ============================================================
        # PHASE 0.5: Register IB underlying handler
        # ============================================================
        if self.ib:
            print("[MUX] Registering IB underlying handler")
            self.ib.on_underlying(self._handle_underlying_event)
        
        # ============================================================
        # PHASE 1: Build OCC subscription lists
        # ============================================================
        print("[MUX] PHASE 1: Building OCC subscription lists")
        t_phase1 = time_module.monotonic()
        
        final_topics = []

        for i, sym in enumerate(symbols):
            print(f"[MUX] → Symbol {i+1}/{len(symbols)}: {sym}")
            t_symbol = time_module.monotonic()
            
            print(f"[MUX]   Creating MassiveContractEngine for {sym}")
            eng = MassiveContractEngine(symbol=sym, ws=self.options)
            self.engines[sym] = eng
            self.freshness[sym] = FreshnessTracker()
            print(f"[MUX]   ✓ Engine created")

            print(f"[MUX]   → await eng.build_occ_list_for_symbol({sym}, {expiry_map[sym]})")
            t_occ = time_module.monotonic()
            occ_codes = await eng.build_occ_list_for_symbol(
                symbol=sym,
                expiry=expiry_map[sym],
                inc_strikes=1,
            )
            print(f"[MUX]   ✓ OCC list built: {len(occ_codes)} contracts in {time_module.monotonic() - t_occ:.3f}s")

            logger.info("[OCC_INIT] %s → %d contracts", sym, len(occ_codes))
            final_topics.extend(occ_codes)
            
            print(f"[MUX] ✓ {sym} complete in {time_module.monotonic() - t_symbol:.3f}s")
        
        print(f"[MUX] ✓ PHASE 1 complete: {len(final_topics)} total contracts in {time_module.monotonic() - t_phase1:.3f}s")

        # ============================================================
        # PHASE 2: Set OCC subscriptions
        # ============================================================
        print("[MUX] PHASE 2: Setting OCC subscriptions")
        print(f"[MUX] → await self.options.set_occ_subscriptions({len(final_topics)} topics)")
        t_phase2 = time_module.monotonic()
        await self.options.set_occ_subscriptions(final_topics)
        print(f"[MUX] ✓ PHASE 2 complete in {time_module.monotonic() - t_phase2:.3f}s")

        # ============================================================
        # PHASE 3: Connect Massive WebSocket
        # ============================================================
        print("[MUX] PHASE 3: Connecting Massive WebSocket")
        logger.info("[MUX] Connecting Massive WS…")
        print("[MUX] → await self.options.connect()")
        t_phase3 = time_module.monotonic()
        await self.options.connect()
        print(f"[MUX] ✓ PHASE 3 complete: WebSocket connected in {time_module.monotonic() - t_phase3:.3f}s")

        # ============================================================
        # PHASE 4: Verify underlying feed
        # ============================================================
        print("[MUX] PHASE 4: Verifying underlying feed")
        if self.ib:
            logger.info("[MUX] Underlying feed active")
            print("[MUX] ✓ Underlying feed active")
        else:
            print("[MUX] ⚠ No underlying feed configured")

        logger.info("[MUX] Ready")
        print("[MUX] connect() EXIT - all phases complete")

    # ---------------------------------------------------------
    async def fetch_snapshot_and_hydrate(self, chain_agg):
        if not self.parent_orchestrator:
            print("[HYDRATE] ERROR: No parent orchestrator")
            return
        if not hasattr(self.parent_orchestrator, "snapshot_client"):
            print("[HYDRATE] ERROR: Parent has no snapshot_client")
            return

        snap_client = self.parent_orchestrator.snapshot_client

        print("\n================ REST SNAPSHOT WARMUP ================\n")

        for sym, eng in self.engines.items():
            occ_list = eng.current_subs.get(sym, [])
            print(f"[WARMUP] {sym}: {len(occ_list)} contracts")
            print(f"[WARMUP] {sym} strikes: {occ_list[:3]}...")  # Show first 3
            
            if not occ_list:
                print(f"[WARMUP] WARNING: No contracts for {sym}")
                continue

            hydrated_count = 0
            for occ in occ_list:
                try:
                    print(f"[HYDRATE] Fetching {sym} {occ}...")
                    
                    # Add timeout to prevent hanging
                    rest = await asyncio.wait_for(
                        snap_client.fetch_contract(sym, occ),
                        timeout=5.0
                    )
                    
                    if not rest:
                        print(f"[HYDRATE] No data returned for {occ}")
                        continue

                    print(f"[HYDRATE] Got data: {list(rest.keys())[:5]}")
                    
                    # Use update_from_snapshot for REST-only data (no bid/ask yet)
                    result = chain_agg.update_from_snapshot(sym, occ, rest)
                    if result:
                        hydrated_count += 1
                        print(f"[HYDRATE] ✓ Hydrated {occ}")
                    else:
                        print(f"[HYDRATE] ✗ update_from_snapshot returned None for {occ}")
                        
                except asyncio.TimeoutError:
                    print(f"[HYDRATE] TIMEOUT (5s) for {occ} - skipping")
                    continue
                except Exception as e:
                    print(f"[HYDRATE] ERROR for {occ}: {e}")
                    continue
            
            print(f"[WARMUP] {sym}: Hydrated {hydrated_count}/{len(occ_list)} contracts")

        print("\n================ WARMUP COMPLETE =================\n")
        
        # Debug: Check what's actually in the cache
        for sym in ['SPY', 'QQQ']:
            cache_size = len(chain_agg.cache.get(sym, {}))
            print(f"[HYDRATE] Final cache for {sym}: {cache_size} contracts")
            if cache_size > 0:
                sample_keys = list(chain_agg.cache[sym].keys())[:3]
                print(f"[HYDRATE] Sample keys: {sample_keys}")

    # ---------------------------------------------------------
    async def close(self):
        logger.info("[MUX] Shutdown requested")

        # ✅ STOP ALL ENGINE TASKS FIRST (CRITICAL)
        for eng in self.engines.values():
            try:
                await eng.stop()
            except Exception:
                logger.exception("[MUX] Engine stop failed")

        # Shutdown Massive options adapter
        if hasattr(self.options, "shutdown"):
            await self.options.shutdown()
        else:
            await self.options.close()

        # Shutdown IB underlying
        if self.ib:
            try:
                await self.ib.close()
            except Exception:
                pass

        logger.info("[MUX] Shutdown complete")