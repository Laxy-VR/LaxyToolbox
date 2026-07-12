"""Tests for the child process registry that prevents orphaned encoders."""

import subprocess
import sys
import time

from sysutil import track_child, untrack_child, terminate_children


def _sleeper():
    return subprocess.Popen([sys.executable, "-c",
                             "import time; time.sleep(60)"])


def test_terminate_children_kills_tracked_process():
    proc = _sleeper()
    track_child(proc)
    try:
        assert proc.poll() is None  # alive before
        terminate_children(timeout=10)
        assert proc.poll() is not None  # dead after
    finally:
        untrack_child(proc)
        if proc.poll() is None:
            proc.kill()


def test_untracked_process_is_left_alone():
    proc = _sleeper()
    track_child(proc)
    untrack_child(proc)
    try:
        terminate_children(timeout=1)
        time.sleep(0.2)
        assert proc.poll() is None  # still running: it was untracked
    finally:
        proc.kill()


def test_terminate_children_tolerates_already_dead():
    proc = _sleeper()
    proc.kill()
    proc.wait()
    track_child(proc)
    try:
        terminate_children(timeout=1)  # must not raise
    finally:
        untrack_child(proc)
