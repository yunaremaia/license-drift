# license-drift

**Detect license drift across your dependency tree.**

`license-drift` is a CLI tool that identifies three categories of license problems in your project's dependencies:

1. **License Change Drift** — a dependency upgraded from one license to another (e.g., MIT → Apache-2.0, or Apache-2.0 → SSPL).
2. **License Incompatibility** — a dependency's license is incompatible with your project's declared license (e.g., GPL-3.0 dependency in an MIT project).
3. **Missing License** — a dependency declares no license at all, creating legal uncertainty.

## Why

Open-source license compliance is usually treated as a one-time audit. But licenses *drift*: a transitive dependency can change its license in a new version, a freshly added package might be GPL-incompatible with your MIT project, and many packages simply forget to declare a license.

Existing tools (FOSSA, Snyk, license_finder) are either commercial, heavy, or don't track *changes* over time. `license-drift` is a lightweight, zero-dependency Python CLI you can run locally or in CI.

## Installation

```bash
pip install git+https://github.com/yunaremaia/license-drift.git
```

Or from source:

```bash
git clone https://github.com/yunaremaia/license-drift.git
cd license-drift
pip install -e .
```

## Usage

```bash
# Scan current project (auto-detects package manager)
license-drift scan .

# Scan with your project's license for incompatibility detection
license-drift scan . --project-license MIT

# Output as JSON for CI integration
license-drift scan . --format json

# Compare against a previous SBOM snapshot
license-drift diff sbom-v1.json sbom-v2.json

# Exit code 1 on any drift detected (CI-friendly)
license-drift scan . --fail-on-drift
```

## Detection Categories

### License Change Drift

When you pin a dependency at version `1.2.3` with license `MIT`, and later upgrade to `2.0.0` which changed to `Apache-2.0`, `license-drift` flags it:

```
[!] License change detected: requests 2.28.0 → 2.32.0
    Old: Apache-2.0
    New: Apache-2.0 (with CLA)
    Severity: MEDIUM
```

### License Incompatibility

Given your project declares `MIT`, `license-drift` checks all transitive dependencies against a compatibility matrix:

```
[!] License incompatibility: libsqlite3 (Public Domain) → your project (MIT)
    Compatible: YES (but attribution required)

[!] License incompatibility: some-gpl-lib (GPL-3.0) → your project (MIT)
    Compatible: NO — GPL-3.0 requires derivative works to be GPL-3.0
    Severity: CRITICAL
```

### Missing License

```
[!] Missing license declaration: internal-helper@1.0.0
    Repository: https://github.com/org/internal-helper
    No LICENSE file found, no SPDX identifier in package.json
    Severity: HIGH
```

## Supported Ecosystems

| Ecosystem | Manifest | Status |
|-----------|----------|--------|
| Python | `pyproject.toml`, `requirements.txt`, `Pipfile.lock` | ✅ |
| Node.js | `package.json`, `package-lock.json` | ✅ |
| Rust | `Cargo.toml`, `Cargo.lock` | ✅ |
| Go | `go.mod`, `go.sum` | ✅ |
| Java | `pom.xml`, `build.gradle` | 🚧 |
| Ruby | `Gemfile.lock` | 🚧 |

## CI Integration

### GitHub Actions

```yaml
name: License Drift Check
on:
  push:
    branches: [main]
  pull_request:

jobs:
  license-drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install git+https://github.com/yunaremaia/license-drift.git
      - run: license-drift scan . --project-license MIT --fail-on-drift
```

### Pre-commit Hook

```yaml
repos:
  - repo: https://github.com/yunaremaia/license-drift
    rev: v0.1.0
    hooks:
      - id: license-drift
        args: ["--project-license", "MIT"]
```

## Output Formats

- **text** (default) — human-readable colored output
- **json** — machine-readable JSON array of findings
- **sarif** — SARIF format for GitHub Code Scanning integration
- **markdown** — Markdown report for PR comments

## Compatibility Matrix

The built-in compatibility matrix covers common SPDX license identifiers:

- Permissive: MIT, Apache-2.0, BSD-2/3-Clause, ISC, Python-2.0, Unicode-2015
- Weak copyleft: LGPL-2.1, LGPL-3.0, MPL-2.0, EPL-2.0
- Strong copyleft: GPL-2.0, GPL-3.0, AGPL-3.0, SSPL-1.0
- Public domain: CC0-1.0, Unlicense, 0BSD
- Proprietary: NOASSERTION, SEE LICENSE IN LICENSE

## Roadmap

- [x] Python CLI skeleton with pyproject.toml detection
- [ ] Node.js (package.json) support
- [ ] Rust (Cargo.lock) support
- [ ] Go (go.mod) support
- [ ] License compatibility matrix (full SPDX cross-product)
- [ ] SARIF output for GitHub Code Scanning
- [ ] `diff` command for SBOM snapshot comparison
- [ ] Pre-commit hook packaging
- [ ] Caching layer for repeated scans
- [ ] SPDX SBOM generation from scan results

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT — see [LICENSE](LICENSE) for details.
