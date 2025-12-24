#!/bin/bash
# ======================================================================
# QUICK VERIFICATION: Grep-Based Helper Audit
# ======================================================================
# Searches strategy files for permission-based blocking patterns
# Run from bot root directory: ./verify_helpers.sh
# ======================================================================

set -e

echo "========================================================================"
echo " STRATEGY HELPER VERIFICATION AUDIT"
echo "========================================================================"
echo

# Colors for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

FINDINGS=0

# ======================================================================
# 1. Search for should_continue method
# ======================================================================
echo "1. Checking for should_continue() method..."
echo "   (This method should NOT exist)"
echo

if grep -rn "def should_continue" --include="*.py" bot_0dte/strategy/ 2>/dev/null; then
    echo -e "${RED}❌ FOUND: should_continue() method exists${NC}"
    echo "   ACTION: Delete this method or refactor to return data"
    FINDINGS=$((FINDINGS + 1))
else
    echo -e "${GREEN}✓ OK: No should_continue() method found${NC}"
fi
echo

# ======================================================================
# 2. Search for boolean return types in strategy helpers
# ======================================================================
echo "2. Checking for boolean return types..."
echo "   (Helpers should return data, not permission)"
echo

if grep -rn "-> bool:" --include="*.py" bot_0dte/strategy/ 2>/dev/null | \
   grep -E "(should_|can_|is_valid|check|verify)" ; then
    echo -e "${YELLOW}⚠️  WARNING: Found boolean returns in strategy helpers${NC}"
    echo "   ACTION: Review these methods - they may be permission gates"
    FINDINGS=$((FINDINGS + 1))
else
    echo -e "${GREEN}✓ OK: No suspicious boolean returns found${NC}"
fi
echo

# ======================================================================
# 3. Search for convexity checks in entry engine
# ======================================================================
echo "3. Checking entry engine for convexity references..."
echo "   (Entry should NOT check convexity)"
echo

if grep -rn "convex" --include="*entry*.py" bot_0dte/strategy/ 2>/dev/null | \
   grep -v "^[^:]*:.*#"; then
    echo -e "${RED}❌ FOUND: Entry engine references convexity${NC}"
    echo "   ACTION: Remove convexity checks from entry evaluation"
    FINDINGS=$((FINDINGS + 1))
else
    echo -e "${GREEN}✓ OK: Entry engine does not reference convexity${NC}"
fi
echo

# ======================================================================
# 4. Search for Greek checks in entry engine
# ======================================================================
echo "4. Checking entry engine for Greek references..."
echo "   (Entry should NOT check Greeks)"
echo

if grep -rn -E "(delta|gamma|theta|vega|greek)" --include="*entry*.py" \
   bot_0dte/strategy/ 2>/dev/null | grep -v "^[^:]*:.*#"; then
    echo -e "${YELLOW}⚠️  WARNING: Entry engine references Greeks${NC}"
    echo "   ACTION: Verify Greeks are not used to gate entry"
    FINDINGS=$((FINDINGS + 1))
else
    echo -e "${GREEN}✓ OK: Entry engine does not reference Greeks${NC}"
fi
echo

# ======================================================================
# 5. Search for IV/skew checks in entry engine
# ======================================================================
echo "5. Checking entry engine for IV/skew references..."
echo "   (Entry should NOT check IV or skew)"
echo

if grep -rn -E "(\\biv\\b|skew|implied.*vol)" --include="*entry*.py" \
   bot_0dte/strategy/ 2>/dev/null | grep -v "^[^:]*:.*#"; then
    echo -e "${YELLOW}⚠️  WARNING: Entry engine references IV/skew${NC}"
    echo "   ACTION: Verify IV/skew not used to gate entry"
    FINDINGS=$((FINDINGS + 1))
else
    echo -e "${GREEN}✓ OK: Entry engine does not reference IV/skew${NC}"
fi
echo

# ======================================================================
# 6. Search for quality gates in strike selector
# ======================================================================
echo "6. Checking strike selector for quality gates..."
echo "   (Should filter on availability, not quality)"
echo

if grep -rn -E "(too.*expensive|quality|favorable|optimal)" \
   --include="*strike*.py" bot_0dte/strategy/ 2>/dev/null | \
   grep -v "^[^:]*:.*#"; then
    echo -e "${YELLOW}⚠️  WARNING: Strike selector may have quality gates${NC}"
    echo "   ACTION: Verify selector only filters on availability"
    FINDINGS=$((FINDINGS + 1))
else
    echo -e "${GREEN}✓ OK: No obvious quality gates in strike selector${NC}"
fi
echo

# ======================================================================
# 7. Search for raise statements in helpers (blocking exceptions)
# ======================================================================
echo "7. Checking for blocking exceptions in helpers..."
echo "   (Helpers should not raise to block execution)"
echo

if grep -rn "raise.*Error" --include="*.py" bot_0dte/strategy/ 2>/dev/null | \
   grep -E "(entry|strike|continuation|latency)" | \
   grep -v "^[^:]*:.*#.*raise"; then
    echo -e "${YELLOW}⚠️  WARNING: Found raise statements in strategy helpers${NC}"
    echo "   ACTION: Verify exceptions are not used to block entry"
    FINDINGS=$((FINDINGS + 1))
else
    echo -e "${GREEN}✓ OK: No obvious blocking exceptions found${NC}"
fi
echo

# ======================================================================
# Summary
# ======================================================================
echo "========================================================================"
echo " SUMMARY"
echo "========================================================================"
echo

if [ $FINDINGS -eq 0 ]; then
    echo -e "${GREEN}✓ VERIFICATION PASSED${NC}"
    echo
    echo "No critical blocking patterns detected."
    echo "All helpers appear to return data, not permission."
    echo
    exit 0
else
    echo -e "${YELLOW}⚠️  VERIFICATION COMPLETED WITH WARNINGS${NC}"
    echo
    echo "Found $FINDINGS potential issues."
    echo "Review flagged patterns to ensure helpers return data, not permission."
    echo
    echo "See VERIFICATION_CHECKLIST.md for detailed review steps."
    echo
    exit 1
fi