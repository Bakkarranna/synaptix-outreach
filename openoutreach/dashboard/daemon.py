# openoutreach/dashboard/daemon.py
"""Process control for the ``rundaemon`` loop, and its log tail.

The daemon is an ordinary long-running process (``manage.py rundaemon``). This
module tracks it by pidfile so the dashboard can show its status and start/stop it
from the browser. Every piece of pipeline state lives in the DB, so stopping the
process is always safe — the next start resumes exactly where it left off.

The pidfile/log live in the data dir next to the SQLite file, so a wiped database
directory is the only thing that could orphan a pidfile, and a stale pid is detected
by the liveness check rather than trusted.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

PIDFILE = Path(settings.BASE_DIR) / "data" / "daemon.pid"
LOGFILE = Path(settings.BASE_DIR) / "data" / "daemon.log"


def _pid() -> int | None:
    try:
        return int(PIDFILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _alive(pid: int | None) -> bool:
    """True when a process with this pid exists. Cross-platform (os.kill(pid, 0)
    is not reliable on Windows)."""
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def status() -> str:
    """A one-line human status: running with its pid, or stopped."""
    pid = _pid()
    if pid and _alive(pid):
        return f"running (pid {pid})"
    return "stopped"


def is_running() -> bool:
    return _alive(_pid())


def start() -> str:
    """Spawn one detached ``rundaemon`` process and record its pid.

    Refuses when one is already running — two daemons on one DB is how duplicate
    sends happen. The child inherits the verified env (UTF-8 output, offline HF
    hub since the embedding model is cached) and logs to ``data/daemon.log``.
    """
    if is_running():
        return f"already running (pid {_pid()})"

    LOGFILE.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    with open(LOGFILE, "ab") as log:
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "manage.py", "rundaemon",
             "--log-level", "info"],
            cwd=str(settings.BASE_DIR),
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )
    PIDFILE.write_text(str(proc.pid), encoding="utf-8")
    logger.info("daemon started (pid %s)", proc.pid)
    return f"started (pid {proc.pid})"


def stop() -> str:
    """Terminate the tracked daemon process, if it is alive."""
    pid = _pid()
    if pid and _alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            return f"could not stop pid {pid}: {exc}"
        logger.info("daemon stop signal sent (pid %s)", pid)
    PIDFILE.unlink(missing_ok=True)
    return f"stopped (pid {pid})" if pid else "not running"


def tail(max_chars: int = 8000) -> str:
    """The tail of the daemon log, decoded defensively."""
    try:
        data = LOGFILE.read_bytes()
    except OSError:
        return "(no daemon log yet)"
    return data.decode("utf-8", errors="replace")[-max_chars:].strip() or "(empty)"
