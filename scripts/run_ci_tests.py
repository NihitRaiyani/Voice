"""Run the single explicit offline CI catalog; never invoke providers or install data."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate_catalog(root: Path, catalog: dict) -> list[str]:
    modules, excluded = catalog["modules"], catalog["excluded"]
    if (
        not isinstance(modules, list)
        or not all(isinstance(name, str) for name in modules)
        or len(set(modules)) != len(modules)
        or not isinstance(excluded, dict)
        or not all(isinstance(reason, str) and reason.strip() for reason in excluded.values())
        or set(modules) & set(excluded)
    ):
        raise ValueError("CI catalog requires unique modules and reasoned exclusions")
    discovered = {str(p.relative_to(root)) for p in (root / "tests").rglob("test_*.py")}
    if set(modules) | set(excluded) != discovered:
        raise ValueError("CI catalog must account for every test module")
    return modules


def main() -> int:
    modules = validate_catalog(ROOT, json.loads((ROOT / "configs/ci-tests.json").read_text()))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *modules], cwd=ROOT
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
