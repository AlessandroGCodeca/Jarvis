"""Real system statistics for the HUD panels, via psutil.

Every field degrades to None when psutil is missing or a probe fails (e.g.
desktops without a battery), so the /stats endpoint never errors.
"""

try:
    import psutil
except Exception:  # noqa: BLE001 - stats become best-effort nulls
    psutil = None

_GB = 1024**3


def get_stats() -> dict:
    """Return CPU / RAM / battery / disk stats (None for anything unreadable)."""
    stats = {
        "cpu_percent": None,
        "ram_percent": None,
        "ram_used_gb": None,
        "ram_total_gb": None,
        "battery_percent": None,
        "battery_charging": None,
        "disk_percent": None,
    }
    if psutil is None:
        return stats

    try:
        stats["cpu_percent"] = psutil.cpu_percent(interval=0.1)
    except Exception:  # noqa: BLE001
        pass

    try:
        vm = psutil.virtual_memory()
        stats["ram_percent"] = vm.percent
        stats["ram_used_gb"] = round((vm.total - vm.available) / _GB, 1)
        stats["ram_total_gb"] = round(vm.total / _GB, 1)
    except Exception:  # noqa: BLE001
        pass

    try:
        battery = psutil.sensors_battery()
        if battery is not None:
            stats["battery_percent"] = int(battery.percent)
            stats["battery_charging"] = bool(battery.power_plugged)
    except Exception:  # noqa: BLE001
        pass

    try:
        stats["disk_percent"] = psutil.disk_usage("/").percent
    except Exception:  # noqa: BLE001
        pass

    return stats
