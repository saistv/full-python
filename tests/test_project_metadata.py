from pathlib import Path


def test_runtime_dependencies_include_zstandard_for_databento_loader() -> None:
    pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '"zstandard>=0.22"' in pyproject_text
