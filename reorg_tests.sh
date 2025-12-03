#!/bin/bash

set -e

echo "=== 0DTE Test Directory Reorganization ==="

# Ensure we run from repo root
if [ ! -d "bot_0dte" ]; then
    echo "ERROR: Run this script from repo root (where bot_0dte/ exists)."
    exit 1
fi

echo "[1] Creating new test folder structure…"

mkdir -p tests/unit
mkdir -p tests/integration
mkdir -p tests/shadow
touch tests/__init__.py
touch tests/unit/__init__.py
touch tests/integration/__init__.py
touch tests/shadow/__init__.py

echo "[2] Moving existing deep tests into tests/unit/…"

DEEP_TEST_DIR="bot_0dte/tests"

if [ -d "$DEEP_TEST_DIR" ]; then
    for f in "$DEEP_TEST_DIR"/*.py; do
        [ -e "$f" ] || continue
        echo "  moving: $f  -> tests/unit/"
        mv "$f" tests/unit/
    done
else
    echo "  No deep test dir found at bot_0dte/tests — skipping."
fi

echo "[3] Creating placeholder integration + shadow test harnesses…"

# Integration placeholders (A + B harness locations)
touch tests/integration/test_chain_snapshot.py
touch tests/integration/test_contract_engine_sim.py

# Shadow run harness location (C)
touch tests/shadow/test_shadow_run.py
touch tests/shadow/config_shadow.yaml

echo "[4] Cleaning up empty old test directory…"

if [ -d "$DEEP_TEST_DIR" ]; then
    if [ -z "$(ls -A "$DEEP_TEST_DIR")" ]; then
        rmdir "$DEEP_TEST_DIR"
        echo "  Removed empty directory: $DEEP_TEST_DIR"
    else
        echo "  Directory not empty, leaving in place: $DEEP_TEST_DIR"
    fi
fi

echo "[5] Re-org complete."
echo "New structure:"
echo
tree tests || find tests -maxdepth 3
echo
echo "=== Done. ==="
