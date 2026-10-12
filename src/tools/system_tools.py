"""
Read-only system info tools. None of these can change anything, so they
run without asking.
"""

import time

import psutil

# how long to wait between the two cpu samples in _by_cpu (explained there)
_CPU_SAMPLE_INTERVAL = 0.3


def get_system_stats() -> dict:
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()

    return {
        "cpu_percent": cpu,
        "ram_percent": mem.percent,
        "ram_used_gb": round(mem.used / 1e9, 1),
        "ram_total_gb": round(mem.total / 1e9, 1),
        "disk_percent": disk.percent,
        "disk_free_gb": round(disk.free / 1e9, 1),
        "network_sent_mb": round(net.bytes_sent / 1e6, 1),
        "network_recv_mb": round(net.bytes_recv / 1e6, 1),
    }


def _by_memory(limit: int) -> list[dict]:
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_percent"]):
        try:
            procs.append(p.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda p: p.get("memory_percent") or 0, reverse=True)
    return procs[:limit]


def _by_cpu(limit: int) -> list[dict]:
    # psutil's per-process cpu_percent() needs two readings with some time
    # in between. If you only call it once it returns 0.0 for everything, and
    # that was a bug I had where "top processes by CPU" just gave a random list.
    # So: call it once for every process to set a baseline, sleep a bit, then
    # call it again to get the real number.
    procs = []
    for p in psutil.process_iter():
        try:
            p.cpu_percent(interval=None)  # throw this one away, it just sets the baseline
            procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    time.sleep(_CPU_SAMPLE_INTERVAL)

    results = []
    for p in procs:
        try:
            results.append({
                "pid": p.pid,
                "name": p.name(),
                "cpu_percent": p.cpu_percent(interval=None),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    results.sort(key=lambda r: r["cpu_percent"], reverse=True)
    return results[:limit]


def list_processes(sort_by: str = "cpu", limit: int = 5) -> list[dict]:
    limit = max(1, min(int(limit), 20))  # never return more than 20 no matter what the model asks for
    if sort_by == "memory":
        return _by_memory(limit)
    return _by_cpu(limit)