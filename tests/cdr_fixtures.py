"""Synthetic CDR recordings for the M3a tests. No ROS 2 and no robot involved.

The CDR bytes are produced by `rosbags` (an INDEPENDENT serializer: it is not the decoder under test) from ROS 2 Humble message
definitions, and the schema text embedded in each channel is what `rosbags` writes for rosbag2 MCAP files (encoding `ros2msg`).
`rosbags` is the optional `xcheck` extra: tests that need it skip when it is absent. The committed example under examples/m3a/ carries
the same kind of bytes, so decoding can be checked without it. Deterministic: every value and timestamp is a fixed constant."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from mcap.writer import CompressionType, Writer

from tests import make_fixtures as mf

BASE = mf.BASE_NS
MS = 1_000_000


def typestore():
    """The ROS 2 Humble type store, or skip the calling test when `rosbags` (extra `xcheck`) is not installed."""
    pytest.importorskip("rosbags")
    from rosbags.typesys import Stores, get_typestore
    return get_typestore(Stores.ROS2_HUMBLE)


def schema_text(msgtype: str) -> bytes:
    return typestore().generate_msgdef(msgtype, ros_version=2)[0].encode()


def cdr(msgtype: str, msg: Any) -> bytes:
    return bytes(typestore().serialize_cdr(msg, msgtype))


# -- message constructors (explicit, so the expected values in the tests are obvious) ----------------------------------

def _t(ns: int):
    T = typestore().types["builtin_interfaces/msg/Time"]
    return T(sec=ns // 10**9, nanosec=ns % 10**9)


def _hdr(stamp_ns: int, frame: str):
    return typestore().types["std_msgs/msg/Header"](stamp=_t(stamp_ns), frame_id=frame)


def _f64(xs):
    import numpy as np
    return np.array(xs, dtype=np.float64)


def joint_state(stamp_ns: int, names: list[str], position: list[float], velocity: list[float] = (), effort: list[float] = (), frame: str = "base"):
    return typestore().types["sensor_msgs/msg/JointState"](header=_hdr(stamp_ns, frame), name=list(names), position=_f64(position),
                                                           velocity=_f64(velocity), effort=_f64(effort))


def vector3(x: float, y: float, z: float):
    return typestore().types["geometry_msgs/msg/Vector3"](x=x, y=y, z=z)


def twist(lin=(0.0, 0.0, 0.0), ang=(0.0, 0.0, 0.0)):
    return typestore().types["geometry_msgs/msg/Twist"](linear=vector3(*lin), angular=vector3(*ang))


def odometry(stamp_ns: int, x: float, y: float, vx: float, wz: float, frame: str = "odom", child: str = "base_link"):
    ts = typestore()
    pt = ts.types["geometry_msgs/msg/Point"](x=x, y=y, z=0.0)
    q = ts.types["geometry_msgs/msg/Quaternion"](x=0.0, y=0.0, z=0.0, w=1.0)
    pose = ts.types["geometry_msgs/msg/Pose"](position=pt, orientation=q)
    cov = _f64([float(i) for i in range(36)])
    pwc = ts.types["geometry_msgs/msg/PoseWithCovariance"](pose=pose, covariance=cov)
    tw = ts.types["geometry_msgs/msg/TwistWithCovariance"](twist=twist((vx, 0.0, 0.0), (0.0, 0.0, wz)), covariance=_f64([0.0] * 36))
    return ts.types["nav_msgs/msg/Odometry"](header=_hdr(stamp_ns, frame), child_frame_id=child, pose=pwc, twist=tw)


def float64(x: float):
    return typestore().types["std_msgs/msg/Float64"](data=x)


def int32(x: int):
    return typestore().types["std_msgs/msg/Int32"](data=x)


def boolean(x: bool):
    return typestore().types["std_msgs/msg/Bool"](data=x)


def string(x: str):
    return typestore().types["std_msgs/msg/String"](data=x)


def battery(stamp_ns: int, voltage: float, percentage: float, present: bool = True, serial: str = "SN-7"):
    """BatteryState has float32 fields: used to show float32 values keep their own type's decimal text."""
    import numpy as np
    ts = typestore()
    return ts.types["sensor_msgs/msg/BatteryState"](
        header=_hdr(stamp_ns, "battery"), voltage=voltage, temperature=float("nan"), current=-1.5, charge=float("nan"), capacity=float("nan"),
        design_capacity=float("nan"), percentage=percentage, power_supply_status=2, power_supply_health=1, power_supply_technology=3,
        present=present, cell_voltage=np.array([], dtype=np.float32), cell_temperature=np.array([], dtype=np.float32), location="slot0",
        serial_number=serial)


def image(stamp_ns: int):
    import numpy as np
    return typestore().types["sensor_msgs/msg/Image"](header=_hdr(stamp_ns, "cam"), height=2, width=2, encoding="mono8", is_bigendian=0, step=2,
                                                      data=np.array([1, 2, 3, 4], dtype=np.uint8))


# -- the MCAP writer ---------------------------------------------------------------------------------------------------

Channel = tuple[str, str, list[tuple[int, Any]]]  # (topic, msgtype, [(log_time_ns, message object | raw bytes)])


def build_cdr_mcap(channels: list[Channel], *, schema_encoding: str = "ros2msg", omit_schema: bool = False, schema_override: dict[str, bytes] | None = None,
                   message_encoding: str = "cdr", chunk_size: int = 4096, compression: CompressionType = CompressionType.NONE) -> bytes:
    """One MCAP with rosbag2-style channels (`message_encoding cdr`, schema `ros2msg` named by the ROS type). Raw `bytes` messages are
    written as given, so a test can hand in truncated or corrupted CDR."""
    out = io.BytesIO()
    w = Writer(out, chunk_size=chunk_size, compression=compression, use_chunking=True, enable_crcs=True, enable_data_crcs=True)
    w.start(profile="", library=mf.LIBRARY)
    events = []
    for idx, (topic, msgtype, msgs) in enumerate(channels):
        if omit_schema:
            sid = 0
        else:
            text = (schema_override or {}).get(msgtype) or schema_text(msgtype)
            sid = w.register_schema(name=msgtype, encoding=schema_encoding, data=text)
        cid = w.register_channel(topic=topic, message_encoding=message_encoding, schema_id=sid)
        for seq, (t, m) in enumerate(msgs):
            events.append((t, idx, seq, cid, m if isinstance(m, bytes) else cdr(msgtype, m)))
    for t, _i, seq, cid, data in sorted(events, key=lambda e: e[:4]):
        w.add_message(channel_id=cid, log_time=t, publish_time=t, sequence=seq, data=data)
    w.finish()
    return out.getvalue()


def write_cdr_mcap(path: Path, channels: list[Channel], **kw) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_cdr_mcap(channels, **kw))
    return path


def t(i: int, step_ms: int = 100) -> int:
    """Log time of the i-th sample of a default series."""
    return BASE + i * step_ms * MS
