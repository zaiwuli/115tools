"""Read-only upstream checks. Installation remains an application release action."""
import asyncio
import re
import time
import json
from pathlib import Path
from importlib.metadata import distribution, PackageNotFoundError

import httpx

from app.adapters.extract_worker import component_info


def version_key(value):
    return tuple(int(part) for part in value.split("."))


def p115_info():
    lock = json.loads((Path(__file__).resolve().parents[2] / "components.lock.json").read_text(encoding="utf-8"))
    pinned = lock["p115client"]["commit"]
    info = {"locked_commit": pinned, "installed": False, "version": None, "running_commit": None}
    try:
        package = distribution("p115client")
        direct = json.loads(package.read_text("direct_url.json") or "{}")
        info.update(installed=True, version=package.version,
                    running_commit=direct.get("vcs_info", {}).get("commit_id"))
    except (PackageNotFoundError, ValueError):
        pass
    return info


async def check_p115(previous):
    info = p115_info()
    result = {**info, "checked_at": time.time(), "installation": "application_release"}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(
                "https://api.github.com/repos/ChenyangGao/p115client/commits?per_page=1",
                headers={"Accept": "application/vnd.github+json"})
            response.raise_for_status()
        candidate = response.json()[0]
        commit = candidate["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("UPSTREAM_VERSION_UNKNOWN")
        result.update(status="checked", upstream_commit=commit,
                      upstream_url=f"https://github.com/ChenyangGao/p115client/commit/{commit}",
                      update_available=commit != info["locked_commit"],
                      last_success=result["checked_at"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        result.update(status="failed", error="UPDATE_CHECK_FAILED",
                      last_success=previous.get("last_success"))
    return result


async def check(db, config):
    previous = db.settings().get("component_updates", {})
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get("https://www.7-zip.org/download.html")
            response.raise_for_status()
        candidates = re.findall(r"Download 7-Zip\s+(\d+\.\d+)", response.text)
        if not candidates:
            raise ValueError("UPSTREAM_VERSION_UNKNOWN")
        latest = max(candidates, key=version_key)
        current = await asyncio.to_thread(component_info, config)
        result = {"checked_at": time.time(), "status": "checked", "upstream": latest,
                  "current": current["version"], "compatible": "26.01",
                  "update_available": bool(current["version"] and
                                           version_key(latest) > version_key(current["version"])),
                  "installation": "application_release"}
    except (httpx.HTTPError, ValueError):
        result = {"checked_at": time.time(), "status": "failed",
                  "error": "UPDATE_CHECK_FAILED", "last_success": previous.get("last_success")}
    if result["status"] == "checked":
        result["last_success"] = result["checked_at"]
    result["p115"] = await check_p115(previous.get("p115", {}))
    history = db.settings().get("component_update_history", [])
    db.set_settings({"component_updates": result,
                     "component_update_history": (history + [result])[-30:]})
    return result


async def monitor(db, config):
    while True:
        settings = db.settings()
        last = settings.get("component_updates", {}).get("checked_at", 0)
        if settings.get("component_update_checks", True) and time.time() - last >= 86400:
            await check(db, config)
        await asyncio.sleep(60)
