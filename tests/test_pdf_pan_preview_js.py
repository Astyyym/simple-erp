"""Exercise preview pan/zoom behavior through Node's built-in test runner."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_pdf_preview_pointer_geometry_and_lifecycle_regressions():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is needed only to run frontend regression tests, not ERP runtime")
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "--test", *[str(path) for path in sorted((project / "tests/frontend").glob("*.test.mjs"))]],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
