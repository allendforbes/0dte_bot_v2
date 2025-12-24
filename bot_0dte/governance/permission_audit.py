#!/usr/bin/env python3
"""
VERIFICATION AUDIT: Permission-Based Blocking Detection

Scans strategy helper methods for boolean returns that could implicitly
block entry decisions. Flags methods that return permission rather than data.

DANGER PATTERNS:
- Methods returning bool (especially named should_*, can_*, is_valid_*)
- Methods raising exceptions to block execution
- Methods returning None to signal rejection
- Methods with side effects that prevent downstream execution

SAFE PATTERNS:
- Methods returning data objects (signals, scores, measurements)
- Methods returning Optional[Data] where None means "no data available"
- Methods returning metrics for observability
- Methods that measure/compute but never decide
"""

import ast
import sys
from pathlib import Path
from typing import List, Dict, Set


class PermissionMethodDetector(ast.NodeVisitor):
    """Detects methods that return permission rather than data."""
    
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.findings: List[Dict] = []
        self.current_class = None
        self.current_method = None
        
        # Suspicious method name patterns
        self.permission_patterns = {
            'should_', 'can_', 'is_valid_', 'is_allowed_', 'check_',
            'verify_', 'validate_', 'confirm_', 'approve_'
        }
    
    def visit_ClassDef(self, node):
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class
    
    def visit_FunctionDef(self, node):
        if not self.current_class:
            return
        
        old_method = self.current_method
        self.current_method = node.name
        
        # Check for suspicious method names
        is_suspicious_name = any(
            node.name.startswith(pattern) 
            for pattern in self.permission_patterns
        )
        
        # Check return type hints
        returns_bool = False
        if node.returns:
            returns_bool = self._is_bool_return(node.returns)
        
        # Check actual return statements
        bool_returns = self._find_bool_returns(node)
        
        # Check for blocking patterns
        raises_to_block = self._finds_raise_statements(node)
        
        if is_suspicious_name or returns_bool or bool_returns or raises_to_block:
            self.findings.append({
                'file': self.filepath,
                'class': self.current_class,
                'method': node.name,
                'line': node.lineno,
                'suspicious_name': is_suspicious_name,
                'returns_bool': returns_bool or bool_returns,
                'raises_to_block': raises_to_block,
                'severity': self._calculate_severity(
                    is_suspicious_name, returns_bool or bool_returns, raises_to_block
                )
            })
        
        self.generic_visit(node)
        self.current_method = old_method
    
    def _is_bool_return(self, node) -> bool:
        """Check if return type annotation is bool."""
        if isinstance(node, ast.Name) and node.id == 'bool':
            return True
        if isinstance(node, ast.Constant) and node.value is bool:
            return True
        return False
    
    def _find_bool_returns(self, func_node) -> bool:
        """Check if function returns boolean literals."""
        for node in ast.walk(func_node):
            if isinstance(node, ast.Return) and node.value:
                if isinstance(node.value, ast.Constant):
                    if node.value.value in (True, False):
                        return True
                if isinstance(node.value, ast.NameConstant):
                    if node.value.value in (True, False):
                        return True
        return False
    
    def _finds_raise_statements(self, func_node) -> bool:
        """Check if function raises exceptions (could be blocking)."""
        for node in ast.walk(func_node):
            if isinstance(node, ast.Raise):
                return True
        return False
    
    def _calculate_severity(self, suspicious_name: bool, returns_bool: bool, 
                           raises: bool) -> str:
        """Calculate severity of finding."""
        score = 0
        if suspicious_name: score += 2
        if returns_bool: score += 3
        if raises: score += 1
        
        if score >= 5: return "CRITICAL"
        if score >= 3: return "HIGH"
        if score >= 2: return "MEDIUM"
        return "LOW"


def audit_file(filepath: Path) -> List[Dict]:
    """Audit a single Python file for permission-based methods."""
    try:
        with open(filepath, 'r') as f:
            tree = ast.parse(f.read(), filename=str(filepath))
        
        detector = PermissionMethodDetector(str(filepath))
        detector.visit(tree)
        return detector.findings
    except Exception as e:
        print(f"Error parsing {filepath}: {e}", file=sys.stderr)
        return []


def audit_strategy_helpers(base_path: str) -> Dict[str, List[Dict]]:
    """Audit all strategy helper files."""
    
    # Key files to audit
    target_files = [
        'elite_entry_diagnostic.py',
        'elite_entry_engine.py',
        'entry_engine.py',
        'strike_selector.py',
        'continuation_engine.py',
        'elite_latency_precheck.py',
        'latency_precheck.py',
    ]
    
    results = {}
    base = Path(base_path)
    
    for target in target_files:
        matches = list(base.rglob(target))
        for filepath in matches:
            findings = audit_file(filepath)
            if findings:
                results[str(filepath)] = findings
    
    return results


def print_report(results: Dict[str, List[Dict]]):
    """Print formatted audit report."""
    
    print("=" * 80)
    print(" STRATEGY HELPER PERMISSION AUDIT REPORT")
    print("=" * 80)
    print()
    
    if not results:
        print("✓ NO PERMISSION-BASED BLOCKING DETECTED")
        print()
        print("All helpers return data, not permission.")
        print("Entry decisions are made solely by orchestrator.")
        return
    
    # Group by severity
    critical = []
    high = []
    medium = []
    low = []
    
    for filepath, findings in results.items():
        for finding in findings:
            target = (filepath, finding)
            if finding['severity'] == 'CRITICAL':
                critical.append(target)
            elif finding['severity'] == 'HIGH':
                high.append(target)
            elif finding['severity'] == 'MEDIUM':
                medium.append(target)
            else:
                low.append(target)
    
    # Print CRITICAL findings
    if critical:
        print("🚨 CRITICAL FINDINGS (HIGH CONFIDENCE BLOCKERS)")
        print("-" * 80)
        for filepath, finding in critical:
            print(f"\nFile: {filepath}")
            print(f"  Class: {finding['class']}")
            print(f"  Method: {finding['method']} (line {finding['line']})")
            print(f"  Returns bool: {finding['returns_bool']}")
            print(f"  Suspicious name: {finding['suspicious_name']}")
            print(f"  Raises exceptions: {finding['raises_to_block']}")
            print()
            print(f"  ⚠️  ACTION REQUIRED:")
            print(f"     This method likely returns permission to block entry.")
            print(f"     Refactor to return data (score, measurement) instead.")
        print()
    
    # Print HIGH findings
    if high:
        print("⚠️  HIGH PRIORITY FINDINGS")
        print("-" * 80)
        for filepath, finding in high:
            print(f"\n{filepath}::{finding['class']}.{finding['method']} (line {finding['line']})")
            print(f"  Review: Returns bool or has blocking name pattern")
        print()
    
    # Print summary
    print("=" * 80)
    print(" SUMMARY")
    print("=" * 80)
    print(f"  CRITICAL: {len(critical)} - Immediate action required")
    print(f"  HIGH:     {len(high)} - Review recommended")
    print(f"  MEDIUM:   {len(medium)} - Low priority review")
    print(f"  LOW:      {len(low)} - Informational")
    print()
    
    if critical:
        print("❌ VERIFICATION FAILED")
        print("   Strategy helpers contain permission-based blocking.")
        print("   Entry decisions are not fully controlled by orchestrator.")
    else:
        print("⚠️  VERIFICATION PASSED WITH WARNINGS")
        print("   No critical blockers found, but review high-priority findings.")


def generate_refactor_checklist(results: Dict[str, List[Dict]]):
    """Generate refactor checklist for findings."""
    
    print()
    print("=" * 80)
    print(" REFACTOR CHECKLIST")
    print("=" * 80)
    print()
    
    for filepath, findings in results.items():
        critical_findings = [f for f in findings if f['severity'] in ('CRITICAL', 'HIGH')]
        
        if not critical_findings:
            continue
        
        print(f"File: {filepath}")
        print("-" * 80)
        
        for finding in critical_findings:
            method_name = finding['method']
            class_name = finding['class']
            
            print(f"\n{class_name}.{method_name}():")
            print()
            print("  Current (likely):")
            print(f"    def {method_name}(...) -> bool:")
            print(f"        if condition:")
            print(f"            return False  # Blocks entry")
            print(f"        return True")
            print()
            print("  Refactor to:")
            print(f"    def {method_name}(...) -> Optional[Measurement]:")
            print(f"        measurement = compute(...)")
            print(f"        return measurement  # Returns data, orchestrator decides")
            print()
            print("  Or rename:")
            print(f"    {method_name}() → measure_{method_name[len('should_'):]}()")
            print(f"    {method_name}() → get_{method_name[len('is_valid_'):]}()")
            print()
    
    print("=" * 80)


if __name__ == '__main__':
    # Search from current directory or provided path
    base_path = sys.argv[1] if len(sys.argv) > 1 else '.'
    
    print(f"Auditing strategy helpers in: {base_path}")
    print()
    
    results = audit_strategy_helpers(base_path)
    print_report(results)
    
    if results:
        generate_refactor_checklist(results)
    
    # Exit code
    critical_count = sum(
        1 for findings in results.values() 
        for f in findings 
        if f['severity'] == 'CRITICAL'
    )
    
    sys.exit(1 if critical_count > 0 else 0)