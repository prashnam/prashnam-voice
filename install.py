#!/usr/bin/env python3
"""prashnam-voice — installer + launcher.

Run this once after Python 3.11+ is installed. It creates a virtual
environment, installs the package into it, then launches the local
server. Re-running it just updates dependencies and restarts the server.

How to run:
  - macOS  (python.org installer): double-click in Finder. Python Launcher
    is registered for `.py` and will run it in Terminal.
  - Windows: double-click in Explorer. The `py` launcher runs it.
  - Linux  (or anywhere via terminal):  python3 install.py

Stop the server: close the window, or Ctrl-C.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import venv
import webbrowser
from pathlib import Path

PORT = int(os.environ.get("PRASHNAM_PORT", "8765"))
REPO = Path(__file__).resolve().parent
VENV = REPO / ".venv"
LOG = REPO / "install.log"

# ---------------------------------------------------------------------------
# Pretty output (ANSI on TTY, plain text otherwise)
# ---------------------------------------------------------------------------


def _color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def info(msg: str) -> None:
    print(f"\033[1m{msg}\033[0m" if _color() else msg)


def ok(msg: str) -> None:
    print(f"\033[32m✓\033[0m {msg}" if _color() else f"[ok] {msg}")


def err(msg: str) -> None:
    target = sys.stderr
    print(f"\033[31m✗\033[0m {msg}" if _color() else f"[error] {msg}", file=target)


def hr() -> None:
    print("─" * 60)


def pause_and_exit(code: int = 1) -> None:
    """Keep the window open so the user can read the message before it closes."""
    try:
        input("\nPress Enter to close this window…")
    except (EOFError, KeyboardInterrupt):
        pass
    sys.exit(code)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def check_python() -> None:
    info("prashnam-voice setup")
    hr()
    if sys.version_info < (3, 11):
        err(
            f"Need Python 3.11. You have "
            f"{sys.version_info.major}.{sys.version_info.minor}."
        )
        py311 = "https://www.python.org/downloads/release/python-3119/"
        print(f"Install Python 3.11 from {py311}")
        print("(3.13/3.14 lack wheels for our ML deps — please pick 3.11.)")
        try:
            webbrowser.open(py311)
        except Exception:
            pass
        pause_and_exit(1)
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}."
       f"{sys.version_info.micro}  ({sys.executable})")


def venv_python() -> Path:
    if sys.platform == "win32":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def ensure_venv() -> None:
    if VENV.exists() and venv_python().exists():
        ok("Found existing .venv")
        return
    print("Creating virtual environment at .venv/ …")
    try:
        venv.create(VENV, with_pip=True)
    except Exception as exc:
        err(f"Failed to create venv: {exc}")
        pause_and_exit(1)
    ok("Created .venv")


def pip_install() -> None:
    print("Installing dependencies (first run downloads ~500 MB; takes a few minutes).")
    print(f"  detail log → {LOG.name}")
    py = venv_python()
    with open(LOG, "ab") as logf:
        subprocess.run(
            [str(py), "-m", "pip", "install", "--upgrade", "--quiet", "pip"],
            cwd=REPO, stdout=logf, stderr=logf,
        )
        result = subprocess.run(
            [str(py), "-m", "pip", "install", "--quiet", "-e", "."],
            cwd=REPO, stdout=logf, stderr=logf,
        )
    if result.returncode != 0:
        err(f"Dependency install failed — see {LOG} for the full output.")
        pause_and_exit(result.returncode)
    ok("Dependencies installed.")


def open_bootstrap_after_delay() -> None:
    """Open index.html (the bootstrap page) once the server has had time to bind."""
    bootstrap = REPO / "index.html"
    if not bootstrap.exists():
        return
    def _open():
        time.sleep(2.5)
        try:
            webbrowser.open(bootstrap.as_uri())
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()


def launch_server() -> int:
    print()
    hr()
    info(f"Starting prashnam-voice on http://localhost:{PORT}/")
    print("(Close this window to stop the server.)")
    hr()
    print()

    open_bootstrap_after_delay()

    py = venv_python()
    cmd = [
        str(py), "-m", "prashnam_voice.cli", "serve",
        "--host", "127.0.0.1", "--port", str(PORT),
    ]
    try:
        return subprocess.call(cmd, cwd=REPO)
    except KeyboardInterrupt:
        return 0


def main() -> int:
    check_python()
    ensure_venv()
    pip_install()
    return launch_server()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        err(f"Unexpected error: {exc}")
        pause_and_exit(1)
