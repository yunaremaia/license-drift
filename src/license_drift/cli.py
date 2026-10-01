"""Main CLI entry point for license-drift."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FindingKind(str, Enum):
    LICENSE_CHANGE = "license_change"
    INCOMPATIBLE = "incompatible"
    MISSING = "missing"


@dataclass
class Finding:
    kind: FindingKind
    package: str
    severity: Severity
    message: str
    old_license: str | None = None
    new_license: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["severity"] = self.severity.value
        return d


# Compatibility matrix: which licenses are compatible with which project licenses.
# True = compatible, False = incompatible.
# This is simplified; a real implementation would use the full SPDX matrix.
_COMPATIBILITY: dict[str, dict[str, bool]] = {
    "MIT": {
        "MIT": True, "Apache-2.0": True, "BSD-2-Clause": True,
        "BSD-3-Clause": True, "ISC": True, "Python-2.0": True,
        "Unicode-2015": True, "CC0-1.0": True, "Unlicense": True,
        "0BSD": True, "GPL-2.0": False, "GPL-3.0": False,
        "AGPL-3.0": False, "SSPL-1.0": False, "LGPL-2.1": True,
        "LGPL-3.0": True, "MPL-2.0": True, "EPL-2.0": True,
        "NOASSERTION": False,
    },
    "Apache-2.0": {
        "MIT": True, "Apache-2.0": True, "BSD-2-Clause": True,
        "BSD-3-Clause": True, "ISC": True, "Python-2.0": True,
        "Unicode-2015": True, "CC0-1.0": True, "Unlicense": True,
        "0BSD": True, "GPL-2.0": False, "GPL-3.0": True,
        "AGPL-3.0": False, "SSPL-1.0": False, "LGPL-2.1": True,
        "LGPL-3.0": True, "MPL-2.0": True, "EPL-2.0": False,
        "NOASSERTION": False,
    },
}


def _normalize_license(license_str: str) -> str:
    """Normalize license string to SPDX identifier."""
    s = license_str.strip()
    # Common normalizations
    aliases = {
        "MIT License": "MIT",
        "Apache License 2.0": "Apache-2.0",
        "Apache Software License": "Apache-2.0",
        "BSD License": "BSD-3-Clause",
        "GNU General Public License v3": "GPL-3.0",
        "GNU General Public License v2": "GPL-2.0",
        "GNU Lesser General Public License v3": "LGPL-3.0",
        "GNU Lesser General Public License v2.1": "LGPL-2.1",
        "ISC License": "ISC",
        "The Unlicense": "Unlicense",
    }
    return aliases.get(s, s)


def check_compatibility(
    dep_license: str,
    project_license: str,
) -> tuple[bool, str]:
    """Check if a dependency license is compatible with the project license.

    Returns (is_compatible, reason).
    """
    dep = _normalize_license(dep_license)
    proj = _normalize_license(project_license)

    matrix = _COMPATIBILITY.get(proj, {})
    compatible = matrix.get(dep)

    if compatible is None:
        # Unknown combination — flag as potentially incompatible
        return False, f"Unknown compatibility: {dep} with project {proj}"
    if compatible:
        return True, f"{dep} is compatible with {proj}"
    return False, f"{dep} is NOT compatible with {proj} — risk of license violation"


def scan_pyproject_toml(path: Path) -> list[dict[str, Any]]:
    """Extract dependencies from pyproject.toml."""
    deps: list[dict[str, Any]] = []
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # PEP 621 project.dependencies
    project_deps = data.get("project", {}).get("dependencies", [])
    for dep_str in project_deps:
        name, version = _parse_dep_spec(dep_str)
        deps.append({"name": name, "version": version, "license": "unknown"})

    # PEP 621 optional-dependencies
    for group in data.get("project", {}).get("optional-dependencies", {}).values():
        for dep_str in group:
            name, version = _parse_dep_spec(dep_str)
            deps.append({"name": name, "version": version, "license": "unknown"})

    # Poetry
    poetry = data.get("tool", {}).get("poetry", {})
    for name, ver in poetry.get("dependencies", {}).items():
        if name == "python":
            continue
        version = ver if isinstance(ver, str) else ver.get("version", "unknown")
        deps.append({"name": name, "version": version, "license": "unknown"})

    return deps


def _parse_dep_spec(dep_str: str) -> tuple[str, str]:
    """Parse a PEP 508 dependency specification into (name, version)."""
    dep_str = dep_str.split(";", 1)[0].split("[", 1)[0].strip()
    for sep in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        if sep in dep_str:
            name, _, version = dep_str.partition(sep)
            return name.strip(), version.strip()
    # No version specifier
    return dep_str.strip(), "unknown"


def scan_package_json(path: Path) -> list[dict[str, Any]]:
    """Extract dependencies from package.json."""
    deps: list[dict[str, Any]] = []
    with open(path) as f:
        data = json.load(f)

    for name, ver in data.get("dependencies", {}).items():
        deps.append({"name": name, "version": ver, "license": "unknown"})

    for name, ver in data.get("devDependencies", {}).items():
        deps.append({"name": name, "version": ver, "license": "unknown"})

    # Optional: read license from package-lock if available
    lock_path = path.parent / "package-lock.json"
    if lock_path.exists():
        with open(lock_path) as f:
            lock = json.load(f)
        for pkg_path, pkg_data in lock.get("packages", {}).items():
            if pkg_path == "":
                continue
            # packages key is like "node_modules/foo"
            pkg_name = pkg_path.split("node_modules/")[-1]
            license_str = pkg_data.get("license", "unknown")
            version = pkg_data.get("version", "unknown")
            # Try to find matching dep and update
            for dep in deps:
                if dep["name"] == pkg_name:
                    dep["version"] = version
                    dep["license"] = license_str

    return deps


def scan_cargo_toml(path: Path) -> list[dict[str, Any]]:
    """Extract dependencies from Cargo.lock (preferred) or Cargo.toml."""
    deps: list[dict[str, Any]] = []
    lock_path = path.parent / "Cargo.lock"

    if lock_path.exists():
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore
        with open(lock_path, "rb") as f:
            data = tomllib.load(f)
        for pkg in data.get("package", []):
            deps.append({
                "name": pkg.get("name", "unknown"),
                "version": pkg.get("version", "unknown"),
                "license": pkg.get("license", "unknown"),
            })
    else:
        # Fallback to Cargo.toml (no version/license info usually)
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore
        with open(path, "rb") as f:
            data = tomllib.load(f)
        for name, ver in data.get("dependencies", {}).items():
            version = ver if isinstance(ver, str) else ver.get("version", "unknown")
            deps.append({"name": name, "version": version, "license": "unknown"})

    return deps


def scan_go_mod(path: Path) -> list[dict[str, Any]]:
    """Extract dependencies from go.mod (best-effort, no license info)."""
    deps: list[dict[str, Any]] = []
    lines = path.read_text().splitlines()
    in_block = False
    for line in lines:
        line = line.strip()
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block:
            if line == ")":
                in_block = False
                continue
            parts = line.split()
            if len(parts) >= 2:
                deps.append({
                    "name": parts[0],
                    "version": parts[1],
                    "license": "unknown",
                })
        elif line.startswith("require ") and not line.startswith("require ("):
            parts = line.split()
            if len(parts) >= 3:
                deps.append({
                    "name": parts[1],
                    "version": parts[2],
                    "license": "unknown",
                })
    return deps


def detect_manifest(path: Path) -> str | None:
    """Auto-detect package manager manifest in the given directory."""
    candidates = {
        "pyproject.toml": "python",
        "requirements.txt": "requirements",
        "package.json": "node",
        "Cargo.toml": "rust",
        "Cargo.lock": "rust-lock",
        "go.mod": "go",
        "Gemfile.lock": "ruby",
        "pom.xml": "maven",
        "build.gradle": "gradle",
    }
    for fname, ecosystem in candidates.items():
        if (path / fname).exists():
            return ecosystem
    return None


def load_requirements_txt(path: Path) -> list[dict[str, Any]]:
    """Parse requirements.txt into dependency list."""
    deps: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        for sep in ("==", ">=", "~=", "=="):
            if sep in line:
                name, _, version = line.partition(sep)
                deps.append({"name": name.strip(), "version": version.strip(), "license": "unknown"})
                break
        else:
            deps.append({"name": line.split(";", 1)[0].strip(), "version": "unknown", "license": "unknown"})
    return deps


def scan_project(project_dir: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Scan a project directory and return (detected_dependencies, ecosystem)."""
    ecosystem = detect_manifest(project_dir)

    match ecosystem:
        case "python":
            return scan_pyproject_toml(project_dir / "pyproject.toml"), ecosystem
        case "requirements":
            return load_requirements_txt(project_dir / "requirements.txt"), ecosystem
        case "node":
            return scan_package_json(project_dir / "package.json"), ecosystem
        case "rust":
            return scan_cargo_toml(project_dir / "Cargo.toml"), ecosystem
        case "rust-lock":
            # Cargo.lock exists but Cargo.toml should too
            return scan_cargo_toml(project_dir / "Cargo.toml"), ecosystem
        case "go":
            return scan_go_mod(project_dir / "go.mod"), ecosystem
        case _:
            return [], None


def run_drift_checks(
    deps: list[dict[str, Any]],
    project_license: str | None,
    previous_snapshot: list[dict[str, Any]] | None = None,
) -> list[Finding]:
    """Run all drift checks on the dependency list."""
    findings: list[Finding] = []

    # Build previous snapshot lookup
    prev_lookup: dict[str, dict[str, Any]] = {}
    if previous_snapshot:
        for dep in previous_snapshot:
            prev_lookup[dep["name"]] = dep

    # Compatibility check (only if project license known)
    if project_license:
        for dep in deps:
            dep_license = dep.get("license", "unknown")
            if dep_license == "unknown":
                findings.append(Finding(
                    kind=FindingKind.MISSING,
                    package=dep["name"],
                    severity=Severity.HIGH,
                    message=f"No license declared for {dep['name']}@{dep['version']} — cannot verify compatibility",
                    extra={"version": dep["version"]},
                ))
                continue

            compatible, reason = check_compatibility(dep_license, project_license)
            if not compatible:
                findings.append(Finding(
                    kind=FindingKind.INCOMPATIBLE,
                    package=dep["name"],
                    severity=Severity.CRITICAL,
                    message=reason,
                    extra={
                        "version": dep["version"],
                        "dependency_license": dep_license,
                        "project_license": project_license,
                    },
                ))

    # Snapshot comparison (license change detection)
    if previous_snapshot:
        for dep in deps:
            prev = prev_lookup.get(dep["name"])
            if prev:
                old_license = prev.get("license", "unknown")
                new_license = dep.get("license", "unknown")
                if old_license != new_license and old_license != "unknown" and new_license != "unknown":
                    findings.append(Finding(
                        kind=FindingKind.LICENSE_CHANGE,
                        package=dep["name"],
                        severity=Severity.HIGH,
                        message=f"License changed for {dep['name']}: {old_license} → {new_license}",
                        old_license=old_license,
                        new_license=new_license,
                        extra={
                            "old_version": prev.get("version"),
                            "new_version": dep.get("version"),
                        },
                    ))

    return findings


def cli() -> None:
    """Entry point for the CLI."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="license-drift",
        description="Detect license drift across your dependency tree",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # scan command
    scan_parser = subparsers.add_parser("scan", help="Scan a project for license drift")
    scan_parser.add_argument("path", default=".", nargs="?", help="Project directory (default: current)")
    scan_parser.add_argument("--project-license", help="Your project's license SPDX identifier")
    scan_parser.add_argument("--format", choices=["text", "json", "sarif"], default="text", help="Output format")
    scan_parser.add_argument("--fail-on-drift", action="store_true", help="Exit 1 if any drift detected")
    scan_parser.add_argument("--snapshot", help="Path to previous snapshot JSON for comparison")

    # diff command
    diff_parser = subparsers.add_parser("diff", help="Compare two dependency snapshots")
    diff_parser.add_argument("snapshot_a", help="First snapshot JSON")
    diff_parser.add_argument("snapshot_b", help="Second snapshot JSON")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "scan":
        project_dir = Path(args.path).resolve()
        if not project_dir.is_dir():
            print(f"Error: not a directory: {project_dir}", file=sys.stderr)
            sys.exit(2)

        deps, ecosystem = scan_project(project_dir)

        if not deps:
            print(f"Warning: no supported manifest detected in {project_dir}", file=sys.stderr)

        # Load previous snapshot if provided
        previous = None
        if args.snapshot:
            snap_path = Path(args.snapshot)
            if snap_path.exists():
                with open(snap_path) as f:
                    previous = json.load(f)

        findings = run_drift_checks(deps, args.project_license, previous)

        if args.format == "json":
            print(json.dumps([f.to_dict() for f in findings], indent=2))
        elif args.format == "sarif":
            sarif = _to_sarif(findings, project_dir)
            print(json.dumps(sarif, indent=2))
        else:
            _print_text_report(findings, ecosystem or "unknown")

        if args.fail_on_drift and findings:
            sys.exit(1)

    elif args.command == "diff":
        path_a = Path(args.snapshot_a)
        path_b = Path(args.snapshot_b)
        if not path_a.exists() or not path_b.exists():
            print("Error: snapshot file not found", file=sys.stderr)
            sys.exit(2)
        with open(path_a) as f:
            snap_a = json.load(f)
        with open(path_b) as f:
            snap_b = json.load(f)
        findings = run_drift_checks(snap_b, None, snap_a)
        _print_text_report(findings, "diff")

    sys.exit(0)


def _print_text_report(findings: list[Finding], ecosystem: str) -> None:
    """Print a human-readable report."""
    if not findings:
        print(f"✅ No license drift detected (ecosystem: {ecosystem})")
        return

    print(f"⚠️  {len(findings)} license drift finding(s) detected (ecosystem: {ecosystem})")
    print()

    for f in findings:
        icon = {
            Severity.LOW: "ℹ️",
            Severity.MEDIUM: "⚠️",
            Severity.HIGH: "🔴",
            Severity.CRITICAL: "🚨",
        }.get(f.severity, "•")

        print(f"  {icon} [{f.severity.value}] {f.kind.value}: {f.package}")
        print(f"      {f.message}")
        if f.old_license:
            print(f"      Old: {f.old_license} → New: {f.new_license}")
        print()


def _to_sarif(findings: list[Finding], project_dir: Path) -> dict[str, Any]:
    """Convert findings to SARIF format."""
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "license-drift",
                    "informationUri": "https://github.com/yunaremaia/license-drift",
                }
            },
            "results": [
                {
                    "ruleId": f.kind.value,
                    "level": "error" if f.severity in (Severity.CRITICAL, Severity.HIGH) else "warning",
                    "message": {"text": f.message},
                    "locations": [{
                        "physicalLocation": {
                            "artifactLocation": {"uri": str(project_dir)},
                        }
                    }],
                }
                for f in findings
            ],
        }],
    }
