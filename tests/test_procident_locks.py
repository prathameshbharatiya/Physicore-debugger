"""Process identity and lock primitives: the foundation of safe signalling and liveness."""

import os
import signal
import subprocess
import sys
import time

import pytest

from physicore_collector import procident as pi
from physicore_collector.procident import IdentityMatch, ProcessIdentity
from physicore_collector.sessionlock import FileLock, LockHeldError, lock_is_held_elsewhere


@pytest.fixture
def sleeper():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    yield p
    if p.poll() is None:
        p.kill()
    p.wait()


def test_identity_of_a_live_process_matches_itself():
    ident = pi.identity_of(os.getpid())
    assert ident is not None and ident.pid == os.getpid() and ident.boot_id
    assert pi.check_identity(ident) is IdentityMatch.SAME


def test_start_time_parsing_survives_hostile_command_names(tmp_path):
    """/proc/<pid>/stat's comm field may contain spaces and ')': parse after the LAST ')'."""
    exe = tmp_path / "we ird) name (x"
    exe.write_bytes(open(sys.executable, "rb").read()) if False else None
    p = subprocess.Popen(["bash", "-c", "exec -a 'a) b (c' sleep 30"])
    try:
        time.sleep(0.2)
        assert pi.read_starttime_ticks(p.pid) is not None
        assert pi.identity_of(p.pid) is not None
    finally:
        p.kill(); p.wait()


def test_reused_pid_is_not_the_same_process(sleeper):
    real = pi.identity_of(sleeper.pid)
    forged = ProcessIdentity(real.pid, real.starttime_ticks + 1, real.boot_id)  # same pid, other start time
    assert pi.check_identity(forged) is IdentityMatch.DIFFERENT
    assert pi.signal_if_same(forged, signal.SIGKILL) is False
    assert sleeper.poll() is None  # the unrelated process was NOT signalled


def test_identity_from_another_boot_is_never_same(sleeper):
    real = pi.identity_of(sleeper.pid)
    other_boot = ProcessIdentity(real.pid, real.starttime_ticks, "00000000-0000-0000-0000-000000000000")
    assert pi.check_identity(other_boot) is IdentityMatch.DIFFERENT
    assert pi.signal_if_same(other_boot, signal.SIGKILL) is False and sleeper.poll() is None


def test_gone_and_unknown(sleeper):
    real = pi.identity_of(sleeper.pid)
    sleeper.kill(); sleeper.wait()
    assert pi.check_identity(real) is IdentityMatch.GONE
    assert pi.signal_if_same(real, signal.SIGTERM) is False
    assert pi.check_identity(None) is IdentityMatch.UNKNOWN
    assert pi.signal_if_same(None, signal.SIGKILL) is False


def test_zombie_counts_as_gone():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    time.sleep(0.5)  # exited but not yet waited for => zombie
    ident = ProcessIdentity(p.pid, pi.read_starttime_ticks(p.pid), pi.read_boot_id())
    assert pi.check_identity(ident) is IdentityMatch.GONE
    p.wait()


def test_matching_identity_is_signalled_and_group_requires_leadership(sleeper):
    ident = pi.identity_of(sleeper.pid)
    assert pi.signal_if_same(ident, signal.SIGTERM, group=True) is True  # sleeper is its own group leader
    assert sleeper.wait(timeout=5) == -signal.SIGTERM


def test_group_signal_refused_for_non_leader():
    # a child in OUR process group is not a group leader: a group kill must be refused, or it would hit us
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        ident = pi.identity_of(p.pid)
        assert os.getpgid(p.pid) != p.pid
        assert pi.signal_if_same(ident, signal.SIGKILL, group=True) is False
        assert p.poll() is None
    finally:
        p.kill(); p.wait()


def test_no_proc_means_never_signal(monkeypatch, sleeper):
    ident = pi.identity_of(sleeper.pid)
    monkeypatch.setattr(pi, "BOOT_ID_PATH", pi.Path("/nonexistent/boot_id"))
    assert pi.check_identity(ident) is IdentityMatch.UNKNOWN
    assert pi.signal_if_same(ident, signal.SIGKILL) is False and sleeper.poll() is None


# ------------------------------------------------------------------ locks


def test_lock_is_exclusive_and_released_on_close(tmp_path):
    a, b = FileLock(tmp_path / "l"), FileLock(tmp_path / "l")
    a.acquire()
    assert lock_is_held_elsewhere(tmp_path / "l") is True
    with pytest.raises(LockHeldError):
        b.acquire(grace=0.1)
    a.release()
    assert lock_is_held_elsewhere(tmp_path / "l") is False
    b.acquire(); b.release()


def test_missing_lock_file_means_not_held(tmp_path):
    assert lock_is_held_elsewhere(tmp_path / "nope") is False


def test_lock_dies_with_a_sigkilled_holder(tmp_path):
    code = ("import sys,time; from physicore_collector.sessionlock import FileLock;"
            "l=FileLock(sys.argv[1]); l.acquire(); print('held', flush=True); time.sleep(60)")
    p = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "l")], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    assert lock_is_held_elsewhere(tmp_path / "l") is True
    p.kill(); p.wait()
    assert lock_is_held_elsewhere(tmp_path / "l") is False  # the kernel released it: no stale lock


def test_lock_is_not_inherited_by_children(tmp_path):
    """A recorder that outlives its supervisor must NOT keep the lock alive (else an orphaned
    recorder would make a dead supervisor look alive)."""
    code = ("import sys,subprocess,time; from physicore_collector.sessionlock import FileLock;"
            "l=FileLock(sys.argv[1]); l.acquire();"
            "c=subprocess.Popen(['sleep','60'], start_new_session=True); print(c.pid, flush=True); time.sleep(60)")
    p = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "l")], stdout=subprocess.PIPE, text=True)
    child = int(p.stdout.readline())
    try:
        p.kill(); p.wait()
        assert pi.read_starttime_ticks(child) is not None  # the child outlived its parent
        assert lock_is_held_elsewhere(tmp_path / "l") is False
    finally:
        os.kill(child, signal.SIGKILL)
