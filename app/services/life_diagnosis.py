"""Bounded read-only comparison, independent of monitoring checkpoints."""
import asyncio
import math
import time
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo


async def compare(adapter):
    from app.services.archives import is_archive
    day = str(datetime.now(ZoneInfo("Asia/Shanghai")).date())
    results = []
    for kind in ("", "move_file", "upload_file"):
        payload = {"date": day, "limit": 1000, "offset": 0}
        if kind:
            payload["type"] = kind
        started = time.monotonic()
        response = await adapter.call("life_behavior_detail", payload)
        data = response.get("data", {})
        rows = data.get("list", [])
        if not isinstance(rows, list):
            raise ValueError("INVALID_LIFE_RESPONSE")
        count = int(data.get("count", len(rows)))
        relevant = [r for r in rows if str(r.get("type", "")) in {"1", "2", "5", "6", "14", "17", "18", "23"}]
        named = []
        missing_name = 0
        for row in relevant:
            name = row.get("file_name") or row.get("name")
            if not isinstance(name, str) or not name:
                missing_name += 1
            elif is_archive(name):
                named.append(row)
        results.append({"filter": kind or "all", "total_reported": count,
                        "rows_sampled": len(rows), "estimated_pages": math.ceil(count / 1000),
                        "relevant_rows": len(relevant),
                        "unique_relevant_parents": len({str(r["parent_id"]) for r in relevant if r.get("parent_id")}),
                        "named_archive_rows": len(named), "missing_name_rows": missing_name,
                        "unique_archive_parents": len({str(r["parent_id"]) for r in named if r.get("parent_id")}),
                        "archive_parent_counts": dict(Counter(str(r.get("parent_id", "")) for r in named)),
                        "archive_event_examples": [{k: r.get(k) for k in ("id", "file_id", "parent_id", "type", "update_time", "create_time")} for r in named[:10]],
                        "event_types": dict(Counter(str(r.get("type", "unknown")) for r in rows)),
                        "field_names": sorted({k for r in rows for k in r}),
                        "elapsed_seconds": round(time.monotonic() - started, 3)})
        await asyncio.sleep(1)
    return {"date": day, "requests": 3, "results": results,
            "note": "First page per filter only; page counts are estimates. No paths queried, checkpoints changed or jobs created."}
