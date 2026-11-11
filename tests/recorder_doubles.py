"""Test doubles for the recorder boundary. NOT ROS 2: they only exercise the supervisor logic and
the subprocess/signal handling of Rosbag2CliAdapter against a stand-in `ros2` script."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

from physicore_collector.errors import RecorderStartError
from physicore_collector.recorder import RecorderSpec
from tests.bagtools import write_bag


class FakeClock:
    """Deterministic time: sleep() advances it, nothing really waits."""

    def __init__(self) -> None:
        self.t = 1_700_000_000_000_000_000
        self.m = 1_000

    def wall_ns(self): return self.t
    def mono_ns(self): return self.m

    def sleep(self, s: float) -> None:
        self.t += int(s * 1e9)
        self.m += int(s * 1e9)


class FakeAdapter:
    """Scripted recorder. It does NOT record anything; it creates a synthetic bag directory
    on stop (like rosbag2 would) according to `behaviour`.

    behaviour: clean | start_fail | exit_early | stop_hangs | stop_error | no_output |
               partial_output | exit_early_with_output
    """

    name = "fake"

    def __init__(self, behaviour: str = "clean", clock: FakeClock | None = None, polls_before_exit: int = 3) -> None:
        self.behaviour, self.clock, self.polls_before_exit = behaviour, clock, polls_before_exit
        self.spec: RecorderSpec | None = None
        self._pid: int | None = None
        self._rc: int | None = None
        self._polls = 0
        self.calls: list[str] = []

    def command(self, spec): return ["/opt/ros/x/bin/ros2", "bag", "record", "--output", str(spec.bag_dir), *spec.topics]
    @property
    def pid(self): return self._pid
    def output_tail(self): return ["fake recorder output"]
    def output_signals(self): return list(getattr(self, "signals", []))

    def start(self, spec):
        self.calls.append("start")
        if self.behaviour == "start_fail":
            raise RecorderStartError("The 'ros2' executable was not found on PATH.", hint="Source ROS 2.")
        self.spec, self._pid = spec, 4242

    def poll(self):
        self.calls.append("poll")
        if self.behaviour in ("exit_early", "exit_early_with_output") and self._rc is None:
            self._polls += 1
            if self._polls >= self.polls_before_exit:
                if self.behaviour == "exit_early_with_output":
                    write_bag(self.spec.bag_dir)
                self._rc = 7
        return self._rc

    def request_stop(self):
        self.calls.append("request_stop")
        if self.behaviour in ("stop_hangs",):
            return
        self._rc = 1 if self.behaviour == "stop_error" else 0
        if self.behaviour == "no_output":
            return
        if self.behaviour == "partial_output":
            write_bag(self.spec.bag_dir, with_metadata=False)  # no metadata.yaml: not finalized
        else:
            write_bag(self.spec.bag_dir)

    def wait(self, timeout):
        self.calls.append(f"wait:{timeout:g}")
        if self.behaviour == "stop_hangs":
            self.clock and self.clock.sleep(timeout)
            return None
        return self._rc

    def kill(self):
        self.calls.append("kill")
        self._rc = -9
        if self.behaviour == "stop_hangs":
            write_bag(self.spec.bag_dir, with_metadata=False)


FAKE_ROS2 = '''\
#!{python}
"""Stand-in for `ros2`: parses `bag record` options, writes a real bag directory, finalizes on
SIGINT like rosbag2 does. Behaviour is chosen with FAKE_ROS2_MODE. This is NOT rosbag2.

The SIGINT handler is installed BEFORE anything heavy is imported, so the window in which a stop
request would kill it is just interpreter start-up (a real recorder has a comparable window; what
that window is for real rosbag2 is NOT known)."""
import os, signal, sys, time
mode = os.environ.get("FAKE_ROS2_MODE", "normal")
args = sys.argv[1:]
assert args[:2] == ["bag", "record"], args
out = args[args.index("--output") + 1]
ROOT = {root!r}


def say(*a, **k):  # a C++ recorder's log writes ignore EPIPE when SIGPIPE is ignored: model that
    try:
        print(*a, **k, flush=True)
    except OSError:
        pass


def _bagtools():
    sys.path.insert(0, ROOT)
    from tests.bagtools import write_bag
    return write_bag


def finalize(*_):
    import pathlib, shutil
    write_bag = _bagtools()
    shutil.rmtree(out, ignore_errors=True)  # replace the in-progress segments by the closed bag (with metadata.yaml)
    write_bag(pathlib.Path(out), payload_pad=int(os.environ.get("FAKE_ROS2_PAD", "0")))  # PAD: make the bag large
    say("finalized")
    sys.exit(0)


if mode == "slow_start":
    time.sleep(1.5)  # a recorder that is not ready for SIGINT yet (default disposition applies)
if mode == "ignore_sigint":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
else:
    signal.signal(signal.SIGINT, finalize)

if "FAKE_ROS2_ARGS_FILE" in os.environ:
    open(os.environ["FAKE_ROS2_ARGS_FILE"], "w").write("\\n".join(args))
say("fake ros2 started")
if os.environ.get("FAKE_ROS2_ENV_FILE"):
    import json
    open(os.environ["FAKE_ROS2_ENV_FILE"], "w").write(json.dumps(dict(os.environ)))
if mode == "reports_loss":
    say("[WARN] [1.0] [rosbag2_cpp]: Cache buffers lost messages per topic: ")
    say("\\t/camera/image: 12")
    say("Total lost: 12")
if mode == "start_fail":
    print("[ERROR] fake: cannot start", file=sys.stderr, flush=True)
    sys.exit(3)
if mode == "crash":
    time.sleep(0.3)
    sys.exit(5)
if mode == "spawn_child":
    import subprocess
    c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    open(os.environ["FAKE_ROS2_CHILD_FILE"], "w").write(str(c.pid))
if os.environ.get("FAKE_ROS2_EARLY_BAG"):  # like rosbag2: segments exist while recording, metadata.yaml only at close.
    # Created AFTER the SIGINT handler is installed, so "the bag exists" truthfully means "ready to be stopped".
    import pathlib
    # Written next to the target and renamed into place, so "the bag directory exists" means "the in-progress
    # segment is fully there" (a half-written directory would be a different, legitimate scenario).
    _tmp = out.rstrip("/") + ".partial"
    _bagtools()(pathlib.Path(_tmp), with_metadata=False, name=pathlib.Path(out).name)
    os.rename(_tmp, out)
if os.environ.get("FAKE_ROS2_READY_FILE"):
    open(os.environ["FAKE_ROS2_READY_FILE"], "w").write("ready")  # handlers are installed now
while True:
    time.sleep(0.05)
'''


def make_fake_ros2(directory: Path) -> Path:
    root = Path(__file__).resolve().parent.parent
    p = directory / "ros2"
    p.write_text(FAKE_ROS2.format(python=sys.executable, root=str(root)))
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p
