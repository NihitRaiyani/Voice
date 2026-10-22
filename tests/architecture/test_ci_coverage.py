import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_workflow_delegates_to_the_same_ci_test_command():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "run: make test-ci" in workflow
    assert "pytest -q" not in workflow


def test_ci_catalog_accounts_for_every_test_module():
    catalog = ROOT / "configs/ci-tests.json"
    assert catalog.exists(), "Every test module needs a CI entry or a reasoned exclusion"
    from scripts.run_ci_tests import validate_catalog

    validate_catalog(ROOT, json.loads(catalog.read_text()))


def test_new_module_and_unreasoned_exclusion_are_rejected(tmp_path):
    import pytest
    from scripts.run_ci_tests import validate_catalog

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_new.py").touch()
    with pytest.raises(ValueError, match="every test module"):
        validate_catalog(tmp_path, {"modules": [], "excluded": {}})
    with pytest.raises(ValueError, match="reasoned exclusions"):
        validate_catalog(tmp_path, {"modules": [], "excluded": {"tests/test_new.py": ""}})
    assert validate_catalog(
        tmp_path, {"modules": [], "excluded": {"tests/test_new.py": "Hardware-only manual acceptance"}}
    ) == []
