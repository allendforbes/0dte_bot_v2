# Governance Tools

This directory contains governance and audit utilities that enforce
architectural invariants for the 0DTE trading system.

These files are NOT strategy logic and should not be modified casually.

## permission_audit.py

Purpose:
- Detect helper code that can silently reintroduce entry vetoes
- Flag boolean returns, permission-shaped naming, and raised exceptions
- Enforce the rule: helpers may observe, only the orchestrator decides

When to run:
- After any change to entry, strike selection, latency, or convexity logic
- Before promoting SHADOW → PAPER or PAPER → LIVE

Failure policy:
- CRITICAL findings must be resolved before deployment
- HIGH findings require explicit review

## phase_audit.py (future)

Reserved for:
- Execution phase hard-gate verification
- SHADOW / PAPER / LIVE invariant enforcement
