#!/usr/bin/env python3
"""
RUSH Policy RAG Agent — Security Audit Orchestrator
=====================================================

Runs all security tests, SAST scanning, and dependency audits,
then generates a consolidated Markdown report.

Usage:
    python scripts/security_audit.py                    # Full audit
    python scripts/security_audit.py --backend-only     # Skip frontend tests
    python scripts/security_audit.py --no-semgrep       # Skip SAST scan
    python scripts/security_audit.py --ci               # CI mode (exit 1 on critical)

Output:
    reports/security_audit_YYYY-MM-DD.md
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


# Paths relative to project root
PROJECT_ROOT = Path(__file__).parent.parent
BACKEND_DIR = PROJECT_ROOT / "apps" / "backend"
FRONTEND_DIR = PROJECT_ROOT / "apps" / "frontend"
REPORTS_DIR = PROJECT_ROOT / "reports"
SEMGREP_CONFIG = PROJECT_ROOT / ".semgrep.yml"


@dataclass
class AuditResult:
    """Result from a single audit step."""
    name: str
    passed: int = 0
    failed: int = 0
    xfailed: int = 0
    skipped: int = 0
    errors: int = 0
    findings: list = field(default_factory=list)
    raw_output: str = ""
    exit_code: int = 0


@dataclass
class AuditReport:
    """Consolidated audit report."""
    timestamp: str
    results: list = field(default_factory=list)

    @property
    def total_findings(self) -> int:
        return sum(r.failed + r.errors for r in self.results)

    @property
    def total_xfails(self) -> int:
        return sum(r.xfailed for r in self.results)


def run_command(cmd: list, cwd: Optional[Path] = None, timeout: int = 300) -> tuple:
    """Run a command and return (stdout, stderr, exit_code)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd or PROJECT_ROOT,
            timeout=timeout,
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", f"Command timed out after {timeout}s", 1
    except FileNotFoundError:
        return "", f"Command not found: {cmd[0]}", 127


def run_backend_security_tests() -> AuditResult:
    """Run pytest security tests for the backend."""
    print("\n[1/5] Running backend security tests...")
    result = AuditResult(name="Backend Security Tests (pytest)")

    stdout, stderr, exit_code = run_command(
        [
            sys.executable, "-m", "pytest",
            "tests/test_prompt_injection.py",
            "tests/test_rag_security.py",
            "tests/test_api_security.py",
            "-v", "--tb=short", "-m", "security",
            "--no-header",
        ],
        cwd=BACKEND_DIR,
        timeout=120,
    )

    result.raw_output = stdout + stderr
    result.exit_code = exit_code

    # Parse pytest output for pass/fail/xfail counts
    for line in (stdout + stderr).split("\n"):
        if "passed" in line or "failed" in line or "xfailed" in line:
            import re
            passed = re.findall(r"(\d+) passed", line)
            failed = re.findall(r"(\d+) failed", line)
            xfailed = re.findall(r"(\d+) xfailed", line)
            skipped = re.findall(r"(\d+) skipped", line)
            errors = re.findall(r"(\d+) error", line)

            if passed:
                result.passed = int(passed[0])
            if failed:
                result.failed = int(failed[0])
            if xfailed:
                result.xfailed = int(xfailed[0])
            if skipped:
                result.skipped = int(skipped[0])
            if errors:
                result.errors = int(errors[0])

    print(f"    Passed: {result.passed}, Failed: {result.failed}, "
          f"XFail: {result.xfailed}, Skipped: {result.skipped}")
    return result


def run_frontend_security_tests() -> AuditResult:
    """Run Vitest security tests for the frontend."""
    print("\n[2/5] Running frontend security tests...")
    result = AuditResult(name="Frontend Security Tests (vitest)")

    stdout, stderr, exit_code = run_command(
        ["npx", "vitest", "run", "src/__tests__/security.test.ts", "--reporter=verbose"],
        cwd=FRONTEND_DIR,
        timeout=60,
    )

    result.raw_output = stdout + stderr
    result.exit_code = exit_code

    # Count test results from vitest output
    for line in (stdout + stderr).split("\n"):
        if "Tests " in line:
            import re
            passed = re.findall(r"(\d+) passed", line)
            failed = re.findall(r"(\d+) failed", line)
            if passed:
                result.passed = int(passed[0])
            if failed:
                result.failed = int(failed[0])

    print(f"    Passed: {result.passed}, Failed: {result.failed}")
    return result


def run_semgrep_scan() -> AuditResult:
    """Run Semgrep SAST scan with custom rules."""
    print("\n[3/5] Running Semgrep SAST scan...")
    result = AuditResult(name="Semgrep SAST Scan")

    if not SEMGREP_CONFIG.exists():
        result.raw_output = "Semgrep config not found at .semgrep.yml"
        result.errors = 1
        return result

    # Find semgrep binary — check common locations, then PATH
    import shutil
    semgrep_bin = shutil.which("semgrep")
    if not semgrep_bin:
        # Check common Python install locations
        for candidate in [
            Path(sys.prefix) / "bin" / "semgrep",
            Path("/Library/Frameworks/Python.framework/Versions/3.12/bin/semgrep"),
            Path.home() / ".local" / "bin" / "semgrep",
        ]:
            if candidate.exists():
                semgrep_bin = str(candidate)
                break
    if not semgrep_bin:
        result.raw_output = "semgrep not installed. Run: pip install semgrep"
        result.skipped = 1
        print("    Skipped: semgrep not found")
        return result

    stdout, stderr, exit_code = run_command(
        [
            semgrep_bin,
            "--config", str(SEMGREP_CONFIG),
            "--json",
            "--exclude", "node_modules",
            "--exclude", ".venv",
            "--exclude", "venv",
            "--exclude", "__pycache__",
            "--exclude", "*.lock",
            ".",
        ],
        timeout=120,
    )

    result.raw_output = stdout
    result.exit_code = exit_code

    try:
        data = json.loads(stdout)
        results_list = data.get("results", [])
        result.failed = len(results_list)
        for finding in results_list:
            severity = finding.get("extra", {}).get("severity", "UNKNOWN")
            rule_id = finding.get("check_id", "unknown")
            file_path = finding.get("path", "unknown")
            line = finding.get("start", {}).get("line", 0)
            result.findings.append({
                "severity": severity,
                "rule": rule_id,
                "file": file_path,
                "line": line,
                "message": finding.get("extra", {}).get("message", ""),
            })
    except json.JSONDecodeError:
        result.raw_output = stdout + stderr

    print(f"    Findings: {result.failed}")
    return result


def run_pip_audit() -> AuditResult:
    """Run pip-audit for backend dependency vulnerabilities."""
    print("\n[4/5] Running pip-audit (backend dependencies)...")
    result = AuditResult(name="Backend Dependency Audit (pip-audit)")

    req_file = BACKEND_DIR / "requirements.txt"
    if not req_file.exists():
        result.raw_output = "requirements.txt not found"
        result.skipped = 1
        return result

    stdout, stderr, exit_code = run_command(
        [sys.executable, "-m", "pip_audit", "-r", str(req_file), "--format", "json"],
        timeout=120,
    )

    result.raw_output = stdout
    result.exit_code = exit_code

    try:
        data = json.loads(stdout)
        vulnerabilities = data if isinstance(data, list) else data.get("dependencies", [])
        for dep in vulnerabilities:
            vulns = dep.get("vulns", [])
            if vulns:
                result.failed += len(vulns)
                for vuln in vulns:
                    result.findings.append({
                        "severity": vuln.get("fix_versions", ["unknown"])[0] if vuln.get("fix_versions") else "unknown",
                        "rule": vuln.get("id", "unknown"),
                        "file": dep.get("name", "unknown"),
                        "line": 0,
                        "message": vuln.get("description", ""),
                    })
    except (json.JSONDecodeError, TypeError):
        # pip-audit may not be installed
        if "No module named" in stderr:
            result.skipped = 1
            result.raw_output = "pip-audit not installed. Run: pip install pip-audit"
        else:
            result.raw_output = stdout + stderr

    print(f"    Vulnerabilities: {result.failed}, Skipped: {result.skipped}")
    return result


def run_npm_audit() -> AuditResult:
    """Run npm audit for frontend dependency vulnerabilities."""
    print("\n[5/5] Running npm audit (frontend dependencies)...")
    result = AuditResult(name="Frontend Dependency Audit (npm audit)")

    stdout, stderr, exit_code = run_command(
        ["npm", "audit", "--json"],
        cwd=FRONTEND_DIR,
        timeout=60,
    )

    result.raw_output = stdout
    result.exit_code = exit_code

    try:
        data = json.loads(stdout)
        vulnerabilities = data.get("vulnerabilities", {})
        for name, info in vulnerabilities.items():
            severity = info.get("severity", "unknown")
            result.findings.append({
                "severity": severity,
                "rule": name,
                "file": "package.json",
                "line": 0,
                "message": info.get("title", info.get("via", [{}])[0] if isinstance(info.get("via", []), list) and info.get("via") else ""),
            })
            if severity in ("critical", "high"):
                result.failed += 1
            else:
                result.passed += 1
    except (json.JSONDecodeError, TypeError):
        result.raw_output = stdout + stderr

    severity_counts = {}
    for f in result.findings:
        sev = f.get("severity", "unknown")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
    print(f"    Findings: {dict(severity_counts)}")
    return result


def generate_report(report: AuditReport) -> str:
    """Generate consolidated Markdown report."""
    lines = [
        f"# Security Audit Report — RUSH Policy RAG Agent",
        f"",
        f"**Date:** {report.timestamp}",
        f"**Auditor:** Automated Security Test Suite",
        f"**Scope:** Backend (FastAPI), Frontend (Next.js), Dependencies, SAST",
        f"",
        f"---",
        f"",
        f"## Executive Summary",
        f"",
    ]

    total_passed = sum(r.passed for r in report.results)
    total_failed = sum(r.failed for r in report.results)
    total_xfailed = sum(r.xfailed for r in report.results)
    total_skipped = sum(r.skipped for r in report.results)

    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Tests Passed | {total_passed} |")
    lines.append(f"| Tests Failed | {total_failed} |")
    lines.append(f"| Known Gaps (xfail) | {total_xfailed} |")
    lines.append(f"| Skipped | {total_skipped} |")
    lines.append(f"")

    if total_xfailed > 0:
        lines.append(f"> **{total_xfailed} known security gaps** are documented as expected failures (xfail).")
        lines.append(f"> These represent areas requiring remediation — see details below.")
        lines.append(f"")

    lines.append(f"---")
    lines.append(f"")

    for result in report.results:
        status = "PASS" if result.exit_code == 0 and result.failed == 0 else "FINDINGS"
        lines.append(f"## {result.name} — {status}")
        lines.append(f"")
        lines.append(f"| Metric | Count |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Passed | {result.passed} |")
        lines.append(f"| Failed | {result.failed} |")
        if result.xfailed:
            lines.append(f"| Expected Failures (known gaps) | {result.xfailed} |")
        if result.skipped:
            lines.append(f"| Skipped | {result.skipped} |")
        lines.append(f"")

        if result.findings:
            lines.append(f"### Findings")
            lines.append(f"")
            lines.append(f"| Severity | Rule/Package | File | Line | Description |")
            lines.append(f"|----------|-------------|------|------|-------------|")
            for f in result.findings[:50]:  # Cap at 50 findings
                msg = str(f.get("message", ""))[:100].replace("|", "\\|").replace("\n", " ")
                lines.append(
                    f"| {f.get('severity', 'N/A')} | {f.get('rule', 'N/A')} | "
                    f"{f.get('file', 'N/A')} | {f.get('line', '-')} | {msg} |"
                )
            lines.append(f"")

        lines.append(f"<details>")
        lines.append(f"<summary>Raw Output</summary>")
        lines.append(f"")
        lines.append(f"```")
        # Truncate raw output to avoid huge reports
        raw = result.raw_output[:5000]
        lines.append(raw)
        if len(result.raw_output) > 5000:
            lines.append(f"... (truncated, {len(result.raw_output)} total chars)")
        lines.append(f"```")
        lines.append(f"</details>")
        lines.append(f"")
        lines.append(f"---")
        lines.append(f"")

    # Known Gaps Summary
    lines.append(f"## Known Security Gaps (Requiring Remediation)")
    lines.append(f"")
    lines.append(f"| # | Gap | Severity | Remediation |")
    lines.append(f"|---|-----|----------|-------------|")
    lines.append(f"| 1 | No Unicode normalization in adversarial detection | CRITICAL | Add `unicodedata.normalize('NFKC', query)` before pattern matching |")
    lines.append(f"| 2 | Rate limit bypass via X-Forwarded-For spoofing | CRITICAL | Configure trusted proxies or use Azure-specific headers |")
    lines.append(f"| 3 | Azure AD auth disabled by default | CRITICAL | Set `REQUIRE_AAD_AUTH=true` in production |")
    lines.append(f"| 4 | No multi-language adversarial detection | HIGH | Add translated adversarial patterns or use embedding-based detection |")
    lines.append(f"| 5 | CSP allows unsafe-eval + unsafe-inline | HIGH | Implement nonce-based CSP for production |")
    lines.append(f"| 6 | Health endpoint exposes architecture without auth | HIGH | Add auth or reduce detail in /health |")
    lines.append(f"| 7 | PDF error message leaks internal details | HIGH | Return generic error in pdf.py:34 |")
    lines.append(f"| 8 | Synonym expansion preserves injection payloads | MEDIUM | Add injection pattern filtering post-expansion |")
    lines.append(f"| 9 | No PHI detection before Cohere API calls | MEDIUM | Add PHI regex/NER filter before external API |")
    lines.append(f"| 10 | Query max length 2000 chars | MEDIUM | Reduce to 500 for chat endpoint |")
    lines.append(f"")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="RUSH Policy RAG Security Audit")
    parser.add_argument("--backend-only", action="store_true", help="Skip frontend tests")
    parser.add_argument("--no-semgrep", action="store_true", help="Skip SAST scan")
    parser.add_argument("--ci", action="store_true", help="Exit 1 on critical findings")
    args = parser.parse_args()

    print("=" * 60)
    print("  RUSH Policy RAG Agent — Security Audit")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    report = AuditReport(timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # Step 1: Backend security tests
    report.results.append(run_backend_security_tests())

    # Step 2: Frontend security tests
    if not args.backend_only:
        report.results.append(run_frontend_security_tests())

    # Step 3: Semgrep SAST
    if not args.no_semgrep:
        report.results.append(run_semgrep_scan())

    # Step 4: pip-audit
    report.results.append(run_pip_audit())

    # Step 5: npm audit
    if not args.backend_only:
        report.results.append(run_npm_audit())

    # Generate report
    REPORTS_DIR.mkdir(exist_ok=True)
    report_content = generate_report(report)
    report_file = REPORTS_DIR / f"security_audit_{datetime.now().strftime('%Y-%m-%d')}.md"
    report_file.write_text(report_content)

    print(f"\n{'=' * 60}")
    print(f"  Report saved to: {report_file}")
    print(f"  Total findings: {report.total_findings}")
    print(f"  Known gaps (xfail): {report.total_xfails}")
    print(f"{'=' * 60}")

    if args.ci and report.total_findings > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
