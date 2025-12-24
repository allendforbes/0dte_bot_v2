# ======================================================================
# MANUAL VERIFICATION CHECKLIST
# Strategy Helper Method Contracts
# ======================================================================

## CRITICAL: Verify No Downstream Blocking

The refactor moved blocking logic from orchestrator, but helpers must
also return DATA not PERMISSION.

## Files to Audit

### 1. elite_entry_diagnostic.py (or elite_entry_engine.py)

**Current method to verify:**
```python
async def evaluate(symbol, price, vwap_state, chain_rows):
    # Returns: signal object or None
```

**VERIFY:**
- [ ] Returns signal object with data fields (bias, signal_type, regime, etc.)
- [ ] Does NOT return boolean (should_enter, is_valid, etc.)
- [ ] Does NOT check convexity before returning signal
- [ ] Does NOT check Greeks before returning signal
- [ ] Does NOT check IV/skew before returning signal
- [ ] Does NOT check option chain pricing before returning signal

**DANGER PATTERNS:**
```python
# ❌ BAD: Returns permission
if convexity_score < threshold:
    return None  # Blocks entry implicitly

# ❌ BAD: Checks option state
if not self._option_chain_looks_good(chain_rows):
    return None

# ✓ GOOD: Returns data
signal = Signal(
    bias="CALL",
    signal_type="vwap_reclaim",
    regime="trend",
    ...
)
return signal
```

**ACTION IF FOUND:**
- Remove any option-state checks from evaluate()
- Move to post-entry observers or delete entirely
- Ensure method evaluates ONLY: price, VWAP, volume, structure

---

### 2. strike_selector.py

**Current method to verify:**
```python
async def select_from_chain(chain_rows, bias, underlying_price):
    # Returns: strike data dict or None
```

**VERIFY:**
- [ ] Returns strike data (contract, premium, strike, delta, etc.)
- [ ] Returns None ONLY if no strikes available (liquidity filter)
- [ ] Does NOT validate "trade quality" or "favorable Greeks"
- [ ] Does NOT check if premium is "too expensive"
- [ ] Does NOT enforce delta/gamma requirements beyond basic filtering

**DANGER PATTERNS:**
```python
# ❌ BAD: Quality gating
if premium > underlying_price * 0.05:
    return None  # "Too expensive" blocks entry

# ❌ BAD: Greek requirements
if abs(delta) < 0.30:
    return None  # "Delta too low" blocks entry

# ✓ GOOD: Availability only
strikes = [s for s in chain_rows if s['bid'] and s['ask']]
if not strikes:
    return None  # No liquid strikes available
return best_strike
```

**ACTION IF FOUND:**
- Remove quality/Greek requirements from selection
- Return best available strike by simple heuristic (ATM, delta target)
- Let orchestrator/management handle quality assessment post-entry

---

### 3. continuation_engine.py

**Current methods to verify:**

#### Method 1: should_continue() — MUST DELETE OR REFACTOR
```python
def should_continue(convexity_score, tier) -> bool:
    # Returns: permission boolean
```

**VERIFY:**
- [ ] This method is DELETED entirely
- [ ] OR refactored to return grade/score, not bool
- [ ] NOT called anywhere in entry path

**DANGER PATTERN:**
```python
# ❌ BAD: This method should not exist
def should_continue(convexity_score, tier) -> bool:
    if convexity_score < 0.5:
        return False  # Blocks entry
    return True
```

**ACTION:**
Delete this method entirely. Replace with:

```python
# ✓ GOOD: Returns data
def grade_convexity(convexity_score: float) -> str:
    if convexity_score >= 0.8: return "A"
    if convexity_score >= 0.6: return "B"
    if convexity_score >= 0.4: return "C"
    if convexity_score >= 0.2: return "D"
    return "F"
```

#### Method 2: measure_convexity() — Verify Non-Blocking
```python
async def measure_convexity(symbol, bias, chain_rows, underlying_price):
    # Returns: float score or None
```

**VERIFY:**
- [ ] Returns numeric score (float)
- [ ] Returns None if data unavailable (acceptable)
- [ ] Does NOT raise exceptions to block execution
- [ ] Does NOT return boolean
- [ ] Called ONLY for observability, never gates entry

---

### 4. elite_latency_precheck.py

**Current method to verify:**
```python
async def check(chain_rows):
    # Returns: result dict or None
```

**VERIFY:**
- [ ] Returns measurement data (latency_ms, freshness, etc.)
- [ ] Does NOT return boolean pass/fail
- [ ] Does NOT raise exceptions to block execution
- [ ] Used ONLY for logging, never gates entry

**DANGER PATTERNS:**
```python
# ❌ BAD: Returns permission
def check(chain_rows) -> bool:
    if latency > 500:
        return False  # Blocks entry
    return True

# ❌ BAD: Raises to block
def check(chain_rows):
    if not fresh:
        raise ValueError("Stale data")

# ✓ GOOD: Returns measurement
def check(chain_rows) -> Optional[Dict]:
    return {
        "latency_ms": 234,
        "freshness": True,
        "quote_age": 0.5
    }
```

---

## Verification Complete If:

✓ Entry engine evaluates ONLY price/VWAP/volume (no option state)
✓ Strike selector filters ONLY on availability (no quality gates)
✓ Continuation engine has NO should_continue() method
✓ Continuation engine returns grades/scores, not booleans
✓ Latency precheck returns measurements, not pass/fail
✓ NO helper method returns boolean to gate entry
✓ NO helper method raises exceptions to block execution

## If Any Issues Found:

1. **Delete blocking methods** (should_continue, is_valid_entry, etc.)
2. **Refactor to return data** (scores, measurements, grades)
3. **Move quality checks to management phase** (post-entry only)
4. **Update orchestrator** if helper contracts changed

## Common Refactor Pattern:

**Before (Permission):**
```python
def should_X(data) -> bool:
    if condition:
        return False
    return True

# Usage:
if not helper.should_X(data):
    return  # Blocked
```

**After (Data):**
```python
def measure_X(data) -> float:
    return score

# Usage:
score = helper.measure_X(data)
# Orchestrator decides what to do with score
```

---

## Final Confirmation Statement:

> "I have verified that NO strategy helper returns permission to block
> entry. All helpers return data (signals, scores, measurements). The
> orchestrator makes all entry decisions based on structural signals only.
> Convexity and quality checks occur post-entry in management phase."

Signed: _________________
Date: _________________