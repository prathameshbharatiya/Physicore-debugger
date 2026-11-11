from tests.anomaly.tools.mutate import Mutant

A = "physicore_collector/anomaly/access.py"
R = "physicore_collector/anomaly/runner.py"
T = "tests/anomaly/"
ACC = (T + "test_access.py",)
RUN = (T + "test_runner_skeleton.py",)

MUTANTS = [
    Mutant("decode twice (X-10)", A, "        if not self._done:\n            self._done = True\n", "        if True:\n            self._done = True\n", ACC + RUN),
    Mutant("eager decode in constructor (X-11)", A, "        self._decoded: Any = None\n        self._done = False\n", "        self._decoded: Any = None\n        self._done = False\n        self.decoded()\n", RUN),
    Mutant("counter not recorded (X-10)", A, "            self._counter.record((self._msg.order, self._msg.index))\n", "", ACC + RUN),
    Mutant("order ignores segment tie-break (X-01)", R, "key=lambda m: (m.log_time, m.order, m.index))", "key=lambda m: (m.log_time, m.index))", RUN),
    Mutant("order is arrival order (X-01)", R, "    return sorted(msgs, key=lambda m: (m.log_time, m.order, m.index))", "    return list(msgs)", RUN),
    Mutant("order ignores time", R, "key=lambda m: (m.log_time, m.order, m.index))", "key=lambda m: (m.order, m.index))", RUN),
    Mutant("not-enabled detector still fed", R, "        if not config.enabled(d.detector_id):\n            results.append(_not_configured(d))\n            continue\n", "", RUN),
    Mutant("leaf walker stops after first leaf", A, "            yield leaf\n", "            yield leaf\n            return\n", ACC),
    Mutant("array length counts strings", A, "    if isinstance(node, (str, bytes, bytearray)) or not _is_seq(node):\n        return None\n    return len(node)", "    if not hasattr(node, '__len__'):\n        return None\n    return len(node)", ACC),
    Mutant("nested schema constants leak to root", A, "            cur = normalise_type(m.group(1)) if m else None\n", "            cur = normalise_type(root_type)\n", ACC),
    Mutant("type normalisation by substring", A, '    if name.count("/") == 1:', '    if "/" in name and name.count("/") <= 2 and "msg" not in name:', ACC),
    Mutant("builtin table value wrong (log WARN)", "physicore_collector/anomaly/ros_constants.py", '"WARN": 30', '"WARN": 3', ACC),
    Mutant("schema names from topic name", R, "out.setdefault(t.topic, set()).add(t.schema_name or None)", "out.setdefault(t.topic, set()).add(t.topic)", RUN + ACC),
]
