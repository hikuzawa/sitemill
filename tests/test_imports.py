"""各サブパッケージを新しいインタープリタで単独に読み込み、循環インポートを検出する。"""

import subprocess
import sys

import pytest

PACKAGES = [
    "sitemill.models",
    "sitemill.settings",
    "sitemill.parse.jp",
    "sitemill.fetch",
    "sitemill.diff",
    "sitemill.store",
    "sitemill.extract",
    "sitemill.extract.llm",
    "sitemill.license",
    "sitemill.assets",
    "sitemill.metrics",
]


@pytest.mark.parametrize("package", PACKAGES)
def test_package_imports_standalone(package: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", f"import {package}"], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
