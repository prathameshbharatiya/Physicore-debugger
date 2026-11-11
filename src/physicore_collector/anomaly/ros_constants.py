"""Built-in verification table of the ROS constants the Tier A detectors use (DESIGN 3). Values were verified against the rosbags
typestore (VERIFIED-TS); official ROS prose could not be read, so no semantic claim beyond the numeric value is made here.
Role: cross-check and fallback only. A constant embedded in the RECORDED schema always wins (DESIGN 3.0)."""
from __future__ import annotations

TABLE: dict[str, dict[str, int]] = {
    "rcl_interfaces/msg/Log": {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "FATAL": 50},
    "rosgraph_msgs/msg/Log": {"DEBUG": 1, "INFO": 2, "WARN": 4, "ERROR": 8, "FATAL": 16},
    "diagnostic_msgs/msg/DiagnosticStatus": {"OK": 0, "WARN": 1, "ERROR": 2, "STALE": 3},
    "action_msgs/msg/GoalStatus": {"STATUS_UNKNOWN": 0, "STATUS_ACCEPTED": 1, "STATUS_EXECUTING": 2, "STATUS_CANCELING": 3,
                                   "STATUS_SUCCEEDED": 4, "STATUS_CANCELED": 5, "STATUS_ABORTED": 6},
    "actionlib_msgs/msg/GoalStatus": {"PENDING": 0, "ACTIVE": 1, "PREEMPTED": 2, "SUCCEEDED": 3, "ABORTED": 4, "REJECTED": 5,
                                      "PREEMPTING": 6, "RECALLING": 7, "RECALLED": 8, "LOST": 9},
    "sensor_msgs/msg/BatteryState": {"POWER_SUPPLY_HEALTH_UNKNOWN": 0, "POWER_SUPPLY_HEALTH_GOOD": 1, "POWER_SUPPLY_HEALTH_OVERHEAT": 2,
                                     "POWER_SUPPLY_HEALTH_DEAD": 3, "POWER_SUPPLY_HEALTH_OVERVOLTAGE": 4,
                                     "POWER_SUPPLY_HEALTH_UNSPEC_FAILURE": 5, "POWER_SUPPLY_HEALTH_COLD": 6,
                                     "POWER_SUPPLY_HEALTH_WATCHDOG_TIMER_EXPIRE": 7, "POWER_SUPPLY_HEALTH_SAFETY_TIMER_EXPIRE": 8},
}


def builtin(type_name: str) -> dict[str, int]:
    return dict(TABLE.get(type_name, {}))
