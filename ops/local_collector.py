"""Run the local collector as a macOS launchd user job.

Usage: python3 ops/local_collector.py install|status|uninstall
The generated plist and logs stay in this project's ignored data directory.

The job runs server.py directly (launchd cannot spawn a wrapper such as caffeinate on a
project inside ~/Documents: exit 78). With KEEP_AWAKE=on, server.py itself starts
`caffeinate -is -w <its pid>`, so the Mac does not idle-sleep (and, on AC power, does not
system-sleep) while the collector is running. Closing the lid on a
laptop still sleeps it. Set NETWORK_ARCHIVE=on in the environment when running `install`
to turn on the Chicago-wide archive (off by default).
"""
from __future__ import annotations

import os
import pathlib
import plistlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PLIST = DATA / "local_collector.plist"
# Logs live outside the project: launchd itself (not python) opens them before the job starts,
# and it may not open files inside ~/Documents or ~/Desktop (exit 78 EX_CONFIG).
LOGS = pathlib.Path.home() / "Library" / "Logs" / "dockside-evanston"
LABEL = "dev.local.dockside-evanston.collector"
DOMAIN = f"gui/{subprocess.check_output(['id', '-u'], text=True).strip()}"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], text=True, capture_output=True, check=check)


def install() -> None:
    DATA.mkdir(exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(ROOT / "server.py")],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 15,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1",
                                 "NETWORK_ARCHIVE": os.environ.get("NETWORK_ARCHIVE", "off"),
                                 "KEEP_AWAKE": os.environ.get("KEEP_AWAKE", "on")},
        "StandardOutPath": str(LOGS / "collector.log"),
        "StandardErrorPath": str(LOGS / "collector-error.log"),
    }
    PLIST.write_bytes(plistlib.dumps(payload))
    run("bootout", f"{DOMAIN}/{LABEL}", check=False)
    result = run("bootstrap", DOMAIN, str(PLIST), check=False)
    if result.returncode:
        raise SystemExit(f"launchctl bootstrap failed: {result.stderr.strip()}")
    print(f"Installed {LABEL} (runs server.py directly; keep-awake {payload['EnvironmentVariables']['KEEP_AWAKE']}; Chicago-wide archive "
          f"{payload['EnvironmentVariables']['NETWORK_ARCHIVE']}); logs: {LOGS / 'collector.log'}")


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
