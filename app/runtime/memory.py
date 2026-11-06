"""Read-only Linux memory diagnostics; all sizes are bytes, unavailable values are null."""
import os
import time
from pathlib import Path


def _text(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _number(path):
    value = _text(path).strip()
    return int(value) if value.isdigit() else None


def _pairs(text, kb=False):
    result = {}
    for line in text.splitlines():
        fields = line.replace(":", " ").split()
        if len(fields) >= 2 and fields[1].isdigit():
            result[fields[0]] = int(fields[1]) * (1024 if kb else 1)
    return result


def memory_snapshot(proc=Path("/proc"), cgroup=Path("/sys/fs/cgroup")):
    status = _pairs(_text(proc / "self/status"), kb=True)
    rollup = _pairs(_text(proc / "self/smaps_rollup"), kb=True)
    process = {
        "available": "VmRSS" in status, "pid": os.getpid(),
        "rss_bytes": status.get("VmRSS"), "peak_rss_bytes": status.get("VmHWM"),
        "anonymous_bytes": status.get("RssAnon"), "file_bytes": status.get("RssFile"),
        "pss_bytes": rollup.get("Pss"),
        "private_bytes": (sum(rollup.get(k, 0) for k in ("Private_Clean", "Private_Dirty", "Private_Hugetlb"))
                          if rollup else None),
    }
    container = {"available": False, "version": None, "usage_bytes": None,
                 "limit_bytes": None, "anonymous_bytes": None, "file_cache_bytes": None,
                 "inactive_file_bytes": None, "working_set_bytes": None}
    memberships = _text(proc / "self/cgroup").splitlines()
    candidates = [(cgroup, 2), (cgroup / "memory", 1)]
    for line in memberships:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        relative = Path(parts[2].lstrip("/"))
        if ".." in relative.parts:
            continue
        if parts[0] == "0" and not parts[1]:
            candidates.insert(0, (cgroup / relative, 2))
        elif "memory" in parts[1].split(","):
            candidates.insert(0, (cgroup / "memory" / relative, 1))
    for folder, version in candidates:
        usage = _number(folder / ("memory.current" if version == 2 else "memory.usage_in_bytes"))
        if usage is None:
            continue
        stat = _pairs(_text(folder / "memory.stat"))
        limit = _number(folder / ("memory.max" if version == 2 else "memory.limit_in_bytes"))
        if limit is not None and limit >= 1 << 60:
            limit = None
        inactive = stat.get("inactive_file" if version == 2 else "total_inactive_file")
        container.update(available=True, version=version, usage_bytes=usage, limit_bytes=limit,
                         anonymous_bytes=stat.get("anon" if version == 2 else "total_rss"),
                         file_cache_bytes=stat.get("file" if version == 2 else "total_cache"),
                         inactive_file_bytes=inactive,
                         working_set_bytes=max(0, usage - inactive) if inactive is not None else None)
        break
    return {"sampled_at": time.time(), "unit": "bytes", "process": process, "container": container}
