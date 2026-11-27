"""Run the mod's test: `python3 -I -B tests/mod_test.py`.

`claude plugin test <dir>` runs only the tests under the plugin's own folder, and an install gets all of
`plugin/`, so the test lives here and runs on a temporary copy of `plugin/` with the test put back in it.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    with tempfile.TemporaryDirectory() as tmp:
        plugin = Path(tmp) / "plugin"
        shutil.copytree(ROOT / "plugin", plugin)
        shutil.copy2(ROOT / "tests" / "register.test.tsx", plugin / "hooks" / "register.test.tsx")
        return subprocess.run([shutil.which("claude") or "claude", "plugin", "test", str(plugin)]).returncode


if __name__ == "__main__":
    sys.exit(main())
