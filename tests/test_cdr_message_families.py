"""Step 4 message-family coverage for the pilot-oriented ROS 2 CDR decoder."""
from __future__ import annotations

import pytest

from physicore_collector.inventory import inspect_evidence
from physicore_collector.investigator import cdr
from physicore_collector.investigator.decoding import decode_topic
from tests import cdr_fixtures as cf

pytestmark = pytest.mark.skipif(not cdr.decoder_info().available,
    reason="optional extra 'ros2' (mcap-ros2-support) is not installed")


def _value(view, path):
    for message in view.messages:
        for leaf in message.leaves:
            if leaf.path == path:
                return leaf.value
    raise AssertionError(f"decoded leaf {path!r} was not present; status={view.status!r}")


def test_mobile_robot_sensor_and_motion_message_families_decode(tmp_path):
    """Exercise nested structs, covariance arrays, variable arrays, sequences, enums and stamped messages."""
    stamp = cf.t(0)
    channels = [
        ("/imu/data", "sensor_msgs/msg/Imu", [(stamp, cf.imu(stamp))]),
        ("/scan", "sensor_msgs/msg/LaserScan", [(stamp, cf.laser_scan(stamp))]),
        ("/gps/fix", "sensor_msgs/msg/NavSatFix", [(stamp, cf.nav_sat_fix(stamp))]),
        ("/range/front", "sensor_msgs/msg/Range", [(stamp, cf.range_sensor(stamp))]),
        ("/pose", "geometry_msgs/msg/PoseStamped", [(stamp, cf.pose_stamped(stamp))]),
        ("/cmd_vel_stamped", "geometry_msgs/msg/TwistStamped", [(stamp, cf.twist_stamped(stamp))]),
        ("/wrench", "geometry_msgs/msg/WrenchStamped", [(stamp, cf.wrench_stamped(stamp))]),
        ("/joint_trajectory", "trajectory_msgs/msg/JointTrajectory", [(stamp, cf.joint_trajectory(stamp))]),
    ]
    path = tmp_path / "pilot_message_families.mcap"
    cf.write_cdr_mcap(path, channels)
    inventory = inspect_evidence(path)
    expected = {
        "/imu/data": {"orientation.z": "0.25", "angular_velocity.z": "0.3",
            "linear_acceleration.z": "9.81", "orientation_covariance.0": "0.0"},
        "/scan": {"ranges.1": "2.5", "intensities.2": "30.0", "angle_increment": "0.5"},
        "/gps/fix": {"latitude": "19.076", "longitude": "72.8777", "status.status": "0",
            "position_covariance.4": "0.0"},
        "/range/front": {"range": "1.35", "min_range": "0.02", "max_range": "4.0"},
        "/pose": {"pose.position.x": "4.0", "pose.position.y": "5.0"},
        "/cmd_vel_stamped": {"twist.linear.x": "0.4", "twist.angular.z": "0.15"},
        "/wrench": {"wrench.force.z": "3.0", "wrench.torque.y": "0.2"},
        "/joint_trajectory": {"joint_names.1": "elbow", "points.0.positions.1": "0.2",
            "points.0.time_from_start.sec": "1"},
    }
    for topic, leaves in expected.items():
        view = decode_topic(inventory, topic, limit=5)
        assert view.status == "ok", (topic, view.detail)
        assert len(view.messages) == 1
        for field, value in leaves.items():
            assert _value(view, field) == value, (topic, field)


def test_expanded_message_family_retains_type_and_topic_provenance(tmp_path):
    stamp = cf.t(0)
    path = tmp_path / "imu.mcap"
    cf.write_cdr_mcap(path, [("/imu/data", "sensor_msgs/msg/Imu", [(stamp, cf.imu(stamp))])])
    view = decode_topic(inspect_evidence(path), "/imu/data", limit=1)
    leaf = next(x for x in view.messages[0].leaves if x.path == "angular_velocity.z")
    assert leaf.ros_type == "float64"
    assert view.topic == "/imu/data" and view.schema_name == "sensor_msgs/msg/Imu"
