import subprocess
import sys
from pathlib import Path


def test_hello_output():
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "main.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "hello" in result.stdout
