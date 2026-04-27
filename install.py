#!/usr/bin/env python3
"""prashnam-voice — installer + daily launcher.

This single script is what you run to start prashnam-voice — both the
first time (it sets things up) and every time after (just relaunch).
Re-running is safe and fast: dependencies install once, then subsequent
runs skip the slow pip step and go straight to launching the server.

If port 8765 is busy (another instance running, or some other tool),
the script automatically tries 8766, 8767, ... up to 8775. The bootstrap
page at index.html probes the same range, so it auto-discovers whichever
port the server ended up on.

How to run:
  - macOS  (python.org installer): double-click in Finder. Python Launcher
    is registered for `.py` and will run it in Terminal.
  - Windows: double-click in Explorer. The `py` launcher runs it.
  - Linux  (or anywhere via terminal):  python3 install.py

Stop the server: close the window, or Ctrl-C.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import venv
import webbrowser
from pathlib import Path

# Port range probed when the preferred port is busy. The bootstrap page
# (index.html) probes the *same* range so it can find the running server
# regardless of which port we ended up binding to.
PORT_RANGE_START = 8765
PORT_RANGE_COUNT = 11
# `PRASHNAM_PORT` env var pins a single port (skips probing). 0 = auto.
ENV_PORT = int(os.environ.get("PRASHNAM_PORT", "0") or 0)
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


def deps_up_to_date() -> bool:
    """Skip the slow pip install when the package was already installed
    in the venv since the last `pyproject.toml` change.

    Heuristic: the egg-info inside the venv site-packages must exist and
    be at least as new as `pyproject.toml`. Not airtight (a pinned
    transitive dep could change without bumping pyproject), but it
    catches the common 'just relaunch' case which is the whole point.
    Run `rm -rf .venv` to force a clean reinstall if anything goes
    sideways.
    """
    if not VENV.exists():
        return False
    # `pip install -e .` writes egg-info into the source directory; older
    # setuptools versions and Windows layouts may put it under the venv.
    candidates: list[Path] = [REPO / "prashnam_voice.egg-info"]
    candidates.extend(VENV.glob("lib/python*/site-packages/prashnam_voice.egg-info"))
    candidates.extend(VENV.glob("Lib/site-packages/prashnam_voice.egg-info"))
    egg = next((c for c in candidates if c.exists()), None)
    if egg is None:
        return False
    pyproject = REPO / "pyproject.toml"
    return egg.stat().st_mtime >= pyproject.stat().st_mtime


def pip_install() -> None:
    if deps_up_to_date():
        ok("Dependencies up-to-date — skipping pip install.")
        return
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


def is_port_free(port: int) -> bool:
    """Try to bind 127.0.0.1:{port}; True iff the bind succeeds."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port() -> int:
    """Pick a port to bind. Honors PRASHNAM_PORT env var if set; otherwise
    walks 8765..8775 and returns the first free one."""
    if ENV_PORT:
        if not is_port_free(ENV_PORT):
            err(f"Port {ENV_PORT} (from PRASHNAM_PORT) is busy.")
            pause_and_exit(1)
        return ENV_PORT
    end = PORT_RANGE_START + PORT_RANGE_COUNT
    for port in range(PORT_RANGE_START, end):
        if is_port_free(port):
            return port
    err(f"All ports in {PORT_RANGE_START}..{end - 1} are busy.")
    print("Close any other prashnam-voice instance, or set PRASHNAM_PORT to a custom port.")
    pause_and_exit(1)
    return 0   # unreachable; keeps mypy happy


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


def launch_server(port: int) -> int:
    print()
    hr()
    info(f"Starting prashnam-voice on http://localhost:{port}/")
    if port != PORT_RANGE_START:
        warn(
            f"Port {PORT_RANGE_START} was busy — using {port} instead. "
            "The bootstrap page (index.html) auto-detects the right port."
        )
    print("(Close this window to stop the server.)")
    hr()
    print()

    open_bootstrap_after_delay()

    py = venv_python()
    cmd = [
        str(py), "-m", "prashnam_voice.cli", "serve",
        "--host", "127.0.0.1", "--port", str(port),
    ]
    try:
        return subprocess.call(cmd, cwd=REPO)
    except KeyboardInterrupt:
        return 0


def main() -> int:
    check_python()
    ensure_venv()
    pip_install()
    port = pick_port()
    return launch_server(port)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        err(f"Unexpected error: {exc}")
        pause_and_exit(1)
