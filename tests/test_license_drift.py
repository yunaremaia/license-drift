"""Tests for license-drift."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

from license_drift.cli import (
    FindingKind,
    Severity,
    check_compatibility,
    cli,
    load_requirements_txt,
    run_drift_checks,
    scan_cargo_toml,
    scan_go_mod,
    scan_package_json,
    scan_pyproject_toml,
)


class TestCheckCompatibility:
    def test_mit_compatible_with_mit(self):
        ok, reason = check_compatibility("MIT", "MIT")
        assert ok
        assert reason == "MIT is compatible with MIT"

    def test_mit_incompatible_with_gpl3(self):
        ok, reason = check_compatibility("GPL-3.0", "MIT")
        assert not ok
        assert "GPL-3.0 is NOT compatible with MIT" in reason

    def test_apache2_compatible_with_gpl3(self):
        ok, reason = check_compatibility("GPL-3.0", "Apache-2.0")
        assert ok
        assert reason == "GPL-3.0 is compatible with Apache-2.0"

    def test_mit_compatible_with_apache2(self):
        ok, reason = check_compatibility("Apache-2.0", "MIT")
        assert ok
        assert reason == "Apache-2.0 is compatible with MIT"

    def test_unknown_license_returns_incompatible(self):
        ok, reason = check_compatibility("Custom-License", "MIT")
        assert not ok
        assert reason == "Unknown compatibility: Custom-License with project MIT"

    def test_normalize_license_aliases(self):
        from license_drift.cli import _normalize_license
        assert _normalize_license("MIT License") == "MIT"
        assert _normalize_license("Apache License 2.0") == "Apache-2.0"


class TestScanPyprojectToml:
    def test_pep621_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "pyproject.toml").write_text("""
[project]
name = "test"
version = "0.1.0"
dependencies = ["requests>=2.28.0", "flask~=2.0"]
""")
            deps = scan_pyproject_toml(td / "pyproject.toml")
            assert len(deps) == 2
            assert deps[0]["name"] == "requests"
            assert deps[1]["name"] == "flask"

    def test_poetry_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "pyproject.toml").write_text("""
[tool.poetry]
name = "test"
version = "0.1.0"

[tool.poetry.dependencies]
python = "^3.10"
requests = "^2.28"
""")
            deps = scan_pyproject_toml(td / "pyproject.toml")
            assert any(d["name"] == "requests" for d in deps)


class TestScanPackageJson:
    def test_basic_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "package.json").write_text(json.dumps({
                "name": "test",
                "version": "1.0.0",
                "dependencies": {"lodash": "^4.17.0"},
                "devDependencies": {"jest": "^29.0"},
            }))
            deps = scan_package_json(td / "package.json")
            names = {d["name"] for d in deps}
            assert "lodash" in names
            assert "jest" in names

    def test_package_lock_merges_license(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "package.json").write_text(json.dumps({
                "name": "test",
                "dependencies": {"lodash": "^4.17.0"},
            }))
            (td / "package-lock.json").write_text(json.dumps({
                "packages": {
                    "node_modules/lodash": {"version": "4.17.21", "license": "MIT"},
                }
            }))
            deps = scan_package_json(td / "package.json")
            assert deps[0]["license"] == "MIT"
            assert deps[0]["version"] == "4.17.21"


class TestScanCargoToml:
    def test_cargo_lock_parses_license(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "Cargo.toml").write_text("""
[package]
name = "test"
version = "0.1.0"

[dependencies]
serde = "1.0"
""")
            (td / "Cargo.lock").write_text("""
version = 3

[[package]]
name = "serde"
version = "1.0.190"
license = "MIT OR Apache-2.0"
""")
            deps = scan_cargo_toml(td / "Cargo.toml")
            assert any(d["name"] == "serde" and d["license"] == "MIT OR Apache-2.0" for d in deps)


class TestScanGoMod:
    def test_go_mod_parses(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "go.mod").write_text("""
module example.com/test

go 1.21

require (
    github.com/spf13/cobra v1.8.0
    google.golang.org/grpc v1.59.0
)
""")
            deps = scan_go_mod(td / "go.mod")
            assert len(deps) == 2
            assert deps[0]["name"] == "github.com/spf13/cobra"


class TestRunDriftChecks:
    def test_missing_license_detection(self):
        deps = [
            {"name": "unknown-pkg", "version": "1.0.0", "license": "unknown"},
        ]
        findings = run_drift_checks(deps, "MIT")
        assert len(findings) == 1
        assert findings[0].kind == FindingKind.MISSING
        assert findings[0].severity == Severity.HIGH

    def test_incompatible_license_detection(self):
        deps = [
            {"name": "gpl-lib", "version": "1.0.0", "license": "GPL-3.0"},
        ]
        findings = run_drift_checks(deps, "MIT")
        assert len(findings) == 1
        assert findings[0].kind == FindingKind.INCOMPATIBLE
        assert findings[0].severity == Severity.CRITICAL

    def test_license_change_detection(self):
        prev = [
            {"name": "some-pkg", "version": "1.0.0", "license": "MIT"},
        ]
        curr = [
            {"name": "some-pkg", "version": "2.0.0", "license": "Apache-2.0"},
        ]
        findings = run_drift_checks(curr, "MIT", prev)
        assert len(findings) == 1
        assert findings[0].kind == FindingKind.LICENSE_CHANGE
        assert findings[0].old_license == "MIT"
        assert findings[0].new_license == "Apache-2.0"

    def test_no_project_license_skips_compatibility(self):
        deps = [
            {"name": "gpl-lib", "version": "1.0.0", "license": "GPL-3.0"},
        ]
        findings = run_drift_checks(deps, None)
        assert len(findings) == 0

    def test_no_change_when_licenses_match(self):
        prev = [
            {"name": "some-pkg", "version": "1.0.0", "license": "MIT"},
        ]
        curr = [
            {"name": "some-pkg", "version": "2.0.0", "license": "MIT"},
        ]
        findings = run_drift_checks(curr, "MIT", prev)
        assert len(findings) == 0


class TestCli:
    def test_scan_empty_project(self):
        with tempfile.TemporaryDirectory() as td:
            old_argv = sys.argv
            try:
                sys.argv = ["license-drift", "scan", td, "--project-license", "MIT"]
                with pytest.raises(SystemExit) as exc_info:
                    cli()
                # An empty directory has no manifest: a warning, but not a failure.
                assert exc_info.value.code == 0
            finally:
                sys.argv = old_argv

    def test_scan_missing_directory_exits_two(self, capsys):
        old_argv = sys.argv
        try:
            sys.argv = ["license-drift", "scan", "/nonexistent/path/xyz", "--project-license", "MIT"]
            with pytest.raises(SystemExit) as exc_info:
                cli()
            assert exc_info.value.code == 2
        finally:
            sys.argv = old_argv
        assert "not a directory" in capsys.readouterr().err

    def test_help(self):
        old_argv = sys.argv
        try:
            sys.argv = ["license-drift", "--help"]
            with pytest.raises(SystemExit) as exc_info:
                cli()
            assert exc_info.value.code == 0
        finally:
            sys.argv = old_argv


class TestLoadRequirementsTxt:
    def test_basic(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "requirements.txt").write_text("""
# comment line
requests==2.28.0
flask>=2.0
pytest~=7.0
-e git+https://github.com/foo/bar.git#egg=bar
""")
            deps = load_requirements_txt(td / "requirements.txt")
            names = {d["name"] for d in deps}
            assert "requests" in names
            assert "flask" in names
            assert "pytest" in names
            # -e lines should be skipped
            assert "-e" not in names
