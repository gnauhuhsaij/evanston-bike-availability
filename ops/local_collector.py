"""Run the local collector as a macOS launchd user job.

Usage: python3 ops/local_collector.py install|status|uninstall
The generated plist and logs stay in this project's ignored data directory.
"""
from __future__ import annotations

import pathlib
import plistlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PLIST = DATA / "local_collector.plist"
LABEL = "dev.local.dockside-evanston.collector"
DOMAIN = f"gui/{subprocess.check_output(['id', '-u'], text=True).strip()}"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], text=True, capture_output=True, check=check)


def install() -> None:
    DATA.mkdir(exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(ROOT / "server.py")],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(DATA / "collector.log"),
        "StandardErrorPath": str(DATA / "collector-error.log"),
    }
    PLIST.write_bytes(plistlib.dumps(payload))
    run("bootout", f"{DOMAIN}/{LABEL}", check=False)
    result = run("bootstrap", DOMAIN, str(PLIST), check=False)
    if result.returncode:
        raise SystemExit(f"launchctl bootstrap failed: {result.stderr.strip()}")
    print(f"Installed {LABEL}; logs: {DATA / 'collector.log'}")


def status() -> None:
    result = run("print", f"{DOMAIN}/{LABEL}", check=False)
    print(result.stdout if result.returncode == 0 else result.stderr)
    raise SystemExit(result.returncode)


def uninstall() -> None:
    result = run("bootout", f"{DOMAIN}/{LABEL}", check=False)
    if result.returncode:
        raise SystemExit(result.stderr.strip())
    print(f"Stopped {LABEL}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"install", "status", "uninstall"}:
        raise SystemExit(__doc__)
    {"install": install, "status": status, "uninstall": uninstall}[sys.argv[1]]()
