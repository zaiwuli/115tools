import time
import asyncio
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.adapters.p115 import AdapterError
from app.persistence.models import DateFolder, Offline, Settings, Source
from app.scheduler.store import enqueue
from app.services.events import record
from app.services.folders import durable_folder


PROTECTED_KEYS = ("cloud_output_cid", "cloud_archive_cid", "cloud_failed_cid")


async def initialize_root(state, root):
    if state.config.adapter != "p115":
        raise ValueError("115_CONNECTION_REQUIRED")
    if state.engine.running_jobs:
        raise ValueError("CLOUD_CONFIGURATION_BUSY")
    directories = {}
    for key, name in (
        ("offline_root_cid", "离线目录"), ("cloud_output_cid", "解压目录"),
        ("cloud_archive_cid", "归档目录"), ("cloud_failed_cid", "失败目录"),
    ):
        directories[key] = await durable_folder(
            state.db, state.engine.adapter, root, name, f"managed-root:{root}:{key}",
        )
    state.db.set_settings({"cloud_root_cid": root, "offline_extract_cid": "",
                           **directories})
    with state.db.session() as session:
        record(session, "system", "CLOUD_ROOT_INITIALIZED", "cloud_monitor",
               "云根目录初始化完成；" + "；".join(f"{key}={cid}" for key, cid in directories.items()))
    return directories


class CloudMonitor:
    def __init__(self, db, adapter):
        self.db, self.adapter = db, adapter

    def schedule(self, session, now):
        settings = self.db.settings()
        if not settings["life_monitor_enabled"]:
            return
        saved = session.get(Settings, "life-monitor-state")
        if saved and saved.value.get("paused"):
            return
        if saved is None or saved.value.get("next", 0) <= now:
            enqueue(session, "life-monitor", "life_monitor", {})

    async def completed_downloads(self):
        from app.persistence.models import Batch, BatchItem
        from app.services.archives import is_archive
        if not hasattr(self.adapter, "offline_task_snapshot"):
            return {}
        snapshot = await self.adapter.offline_task_snapshot()
        now = time.time()
        with self.db.session() as session:
            saved = session.get(Settings, "offline-monitor-seen")
            seen = dict(saved.value) if saved else {}
            sources = list(session.scalars(select(Source).where(Source.kind == "115", Source.enabled)))
            owned = {x.hash_value.lower() for x in session.scalars(select(Offline))
                     if not x.identity.startswith("external:") and x.hash_value}
            tracked = {x.identity: x for x in session.scalars(select(Offline).where(Offline.kind == "external"))}
        candidates, paths, present = {}, {}, set()
        for row in snapshot["rows"]:
            parent = str(row.get("wp_path_id") or "")
            h = str(row.get("info_hash") or row.get("hash") or "").lower()
            if not parent.isdecimal() or not h or h in owned:
                continue
            key = f"{h}:{row.get('add_time', '')}:{parent}"
            identity = "external:" + key
            present.add(identity)
            status = int(row.get("status", 0))
            if key in seen:
                continue
            if saved is None and status == 2:
                seen[key] = now
                continue
            existing = tracked.get(identity)
            if existing and existing.stage == "complete":
                continue
            if not existing and status < 0:
                continue
            if not existing:
                if parent not in paths:
                    if len(paths) >= 20:
                        continue
                    try:
                        paths[parent] = await self.adapter.directory_path(parent)
                    except AdapterError as exc:
                        if exc.code != "CID_NOT_VERIFIED": raise
                        paths[parent] = []
                from app.persistence.models import Rule
                with self.db.session() as session:
                    protected = {self.db.settings().get(k) for k in PROTECTED_KEYS} - {None, "", "0"}
                    protected.update(r.config["archive_cid"] for r in session.scalars(select(Rule).where(Rule.enabled))
                                     if r.config.get("archive_cid") not in (None,"","0"))
                if set(paths[parent]) & protected: continue
                matches = [x for x in sources if x.config.get("cid") in paths[parent]]
                if not matches: continue
                matches.sort(key=lambda x: paths[parent].index(x.config["cid"]), reverse=True)
                with self.db.session() as session:
                    batch = Batch(target_date=str(datetime.fromtimestamp(now).date()), root_cid=matches[0].config["cid"], target_cid=parent)
                    session.add(batch); session.flush()
                    item = Offline(identity=identity, original_link="", name=str(row.get("name") or row.get("file_name") or h),
                                   kind="external", hash_value=h, batch_id=batch.id, target_date=batch.target_date,
                                   root_cid=batch.root_cid, target_cid=parent, status="downloading", stage="offline_download",
                                   next_check=now+15)
                    session.add(item);session.flush()
                    session.add(BatchItem(batch_id=batch.id, task_id=item.id))
                    session.merge(Settings(key="external-source:"+item.id, value={"source_id":matches[0].id}))
                    existing = item
            with self.db.session() as session:
                item = session.get(Offline, existing.id)
                if item.stage == "complete": continue
                if item.status == "failed" and status != 2: continue
                if status == 2:
                    item.status, item.stage, item.next_check = "success", "discover", None
                    candidates[key] = parent
                elif status < 0:
                    item.status, item.stage, item.next_check = "failed", "offline_manual", None
                    item.error = "REMOTE_OFFLINE_FAILED"
                else:
                    item.status, item.stage = "downloading", "offline_download"
                    item.check_count += 1
                    item.next_check = now + {1:180, 2:300}.get(item.check_count, self.db.settings()["offline_fallback_seconds"])
        # Missing is never success. Timeout allows one bounded, exact-name probe.
        with self.db.session() as session:
            waiting = list(session.scalars(select(Offline).where(Offline.kind == "external", Offline.status.in_(["downloading", "unknown"]))))
        for old in waiting[:20]:
            expired = now-old.created_at >= 86400
            if expired:
                marker_key = "external-timeout:"+old.id
                with self.db.session() as session:
                    attempted = session.get(Settings, marker_key)
                if not attempted:
                    with self.db.session() as session:
                        source_marker = session.get(Settings, "external-source:"+old.id)
                        source = session.get(Source, source_marker.value["source_id"]) if source_marker else None
                    if not source or not source.enabled:
                        continue
                    path = await self.adapter.directory_path(old.target_cid)
                    protected = {self.db.settings().get(k) for k in PROTECTED_KEYS} - {None, "", "0"}
                    from app.persistence.models import Rule
                    with self.db.session() as session:
                        protected.update(r.config["archive_cid"] for r in session.scalars(select(Rule).where(Rule.enabled))
                                         if r.config.get("archive_cid") not in (None,"","0"))
                    if source.config.get("cid") not in path or set(path) & protected:
                        continue
                    page = await self.adapter.files(old.target_cid)
                    matches = [f for f in page.files if not f.is_dir and is_archive(f.name) and f.name == old.name]
                    with self.db.session() as session:
                        session.merge(Settings(key=marker_key, value={"checked":now}))
                        if len(matches)==1:
                            source = session.get(Settings,"external-source:"+old.id)
                            # Adopt only the matching file, never unrelated archives.
                            self.archives.adopt(session, matches[0], source_id=source.value["source_id"] if source else "")
                            item=session.get(Offline,old.id);item.stage="complete";item.next_check=None
                            item.error=""
                            record(session,item.id,"EXTERNAL_TIMEOUT_RELEASED","discover","观察满24小时，找到同名压缩包，放行一次云解压")
                            continue
            if expired or (old.identity not in present and not snapshot["complete"]):
                with self.db.session() as session:
                    item=session.get(Offline,old.id)
                    item.status,item.stage,item.next_check="unknown","offline_manual",None
                    record(session,item.id,"EXTERNAL_REVIEW","offline_manual","离线状态待核对：超过24小时或本轮3页未覆盖")
        with self.db.session() as session:
            session.merge(Settings(key="offline-monitor-seen",value=dict(sorted(seen.items(),key=lambda x:x[1])[-10000:])))
            record(session,"system","OFFLINE_MONITOR_READ","cloud_monitor",
                   f"离线任务读取 {snapshot['pages']} 页；{'完整' if snapshot['complete'] else '达到3页上限，覆盖受限'}；成功待扫描 {len(candidates)} 个")
        return candidates

    async def poll_external(self):
        candidates = await self.completed_downloads()
        with self.db.session() as session:
            saved = session.get(Settings, "offline-monitor-seen")
            seen = dict(saved.value) if saved else {}
            for key, cid in candidates.items():
                item = session.scalar(select(Offline).where(Offline.identity == "external:"+key))
                source = session.get(Settings, "external-source:"+item.id) if item else None
                if source:
                    enqueue(session, "scan-cid:"+cid, "cloud_scan", {
                        "cid":cid,"source_id":source.value["source_id"],
                        "offline_only":False,"one_shot":True,"reason":"外部离线下载完成"})
                    seen[key] = time.time()
            session.merge(Settings(key="offline-monitor-seen",value=seen))

    async def run(self, job):
        settings = self.db.settings()
        if not settings["life_monitor_enabled"]:
            return None
        with self.db.session() as session:
            saved = session.get(Settings, "life-monitor-state")
            checkpoint = saved.value if saved else {}
            sources = list(session.scalars(select(Source).where(Source.kind == "115", Source.enabled)))
            offline = list(session.scalars(select(Offline).where(
                Offline.status.not_in(["failed", "submit_failed", "canceled"]),
                Offline.target_cid != "",
            )))
            date_cids = set(session.scalars(select(DateFolder.cid).where(
                DateFolder.root_cid == settings.get("offline_root_cid"))))
        from app.services.archives import is_archive
        now = time.time()
        today = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date()
        cursor = int(checkpoint.get("cursor", 0))
        initial = not checkpoint.get("checked")
        since = checkpoint.get("checked", now)
        first = datetime.fromtimestamp(since, ZoneInfo("Asia/Shanghai")).date()
        if (today - first).days > 7:
            raise AdapterError("LIFE_BACKLOG_REQUIRES_REVIEW")
        with self.db.session() as session:
            pending = session.get(Settings, "life-monitor-pending")
            work = dict(pending.value) if pending else {
                "day": str(today), "offset": 0, "cursor": cursor, "newest": cursor,
                "cutoff": now, "initial": initial, "events": {}, "fetched": 0,
                "fetch_complete": False,
            }
        # Pages overlap by 100 rows to tolerate bounded insertion during paging.
        # Large concurrent changes cannot be proven complete with an offset API.
        pages = 0
        while not work["fetch_complete"] and pages < (1 if work["initial"] else 10):
            if pages:
                await asyncio.sleep(3)
            response = await self.adapter.call("life_behavior_detail", {
                "date": work["day"], "limit": 1000, "offset": work["offset"],
            })
            data = response.get("data", {})
            rows = data.get("list")
            if not isinstance(rows, list):
                raise AdapterError("INVALID_LIFE_RESPONSE")
            pages += 1
            work["fetched"] += len(rows)
            reached = False
            with self.db.session() as session:
                record(session, "system", "LIFE_PAGE_READ", "cloud_monitor",
                       f"生活事件页：日期 {work['day']}；offset {work['offset']}；返回 {len(rows)}；"
                       f"边界ID {work['cursor']}；ID范围 "
                       f"{min((int(r['id']) for r in rows), default=0)}–{max((int(r['id']) for r in rows), default=0)}；"
                       f"类型 {dict(Counter(str(r.get('type', '')) for r in rows))}；"
                       f"压缩包事件示例 " + str([{k: r.get(k) for k in ("id", "type", "parent_id", "file_id", "update_time")}
                                               for r in rows if is_archive(str(r.get("file_name") or r.get("name") or ""))][:5]))
            for event in rows:
                identifier = int(event["id"])
                work["newest"] = max(work["newest"], identifier)
                if identifier <= work["cursor"]:
                    reached = True
                    continue
                # Store only fields needed to filter and locate files; never credentials.
                event_key = str(identifier)
                work["events"][event_key] = {k: event[k] for k in (
                    "id", "type", "file_id", "file_name", "name", "parent_id", "update_time",
                ) if k in event}
            count = int(data.get("count", len(rows)))
            day_finished = reached or not rows or work["offset"] + len(rows) >= count
            if work["initial"] or reached:
                work["fetch_complete"] = True
            elif day_finished:
                day = datetime.fromisoformat(work["day"]).date() - timedelta(days=1)
                if day < first:
                    work["fetch_complete"] = True
                else:
                    work["day"], work["offset"] = str(day), 0
            else:
                work["offset"] += max(1, len(rows) - 100)
            with self.db.session() as session:
                session.merge(Settings(key="life-monitor-pending", value=dict(work)))
        if not work["fetch_complete"]:
            with self.db.session() as session:
                checkpoint = checkpoint | {"next": now + 1800, "backlog": True}
                session.merge(Settings(key="life-monitor-state", value=checkpoint))
                record(session, "system", "LIFE_BACKLOG", "cloud_monitor",
                       f"生活事件本轮获取 {pages} 页，达到页数上限；已保存进度，尚未完整覆盖")
            return None
        # Consolidate all file events before filtering so a later move-out wins.
        latest = {}
        for event in work["events"].values():
            key = str(event.get("file_id") or "event:" + str(event["id"]))
            if key not in latest or int(event["id"]) > int(latest[key]["id"]):
                latest[key] = event
        events = []
        for event in latest.values():
            if int(event.get("type", 0)) not in {1, 2, 5, 6, 14, 23, 24}:
                continue
            name = event.get("file_name") or event.get("name")
            if isinstance(name, str) and name and not is_archive(name):
                continue
            events.append(event)
        # Keep the snapshot with pending life work so interrupted batches resume
        # without fetching the same offline pages again.
        if "offline_candidates" not in work:
            work["offline_candidates"] = await self.completed_downloads()
            with self.db.session() as session:
                session.merge(Settings(key="life-monitor-pending", value=dict(work)))
        events.extend({"id": "0", "type": 2, "parent_id": parent,
                       "offline_completion": True}
                      for parent in set(work["offline_candidates"].values()))
        targets = {}
        protected = {settings[key] for key in PROTECTED_KEYS if settings.get(key) not in (None, "", "0")}
        # Custom rule destinations must not feed archived originals back into sources.
        from app.persistence.models import Rule
        with self.db.session() as session:
            protected.update(rule.config["archive_cid"] for rule in session.scalars(select(Rule).where(Rule.enabled))
                             if rule.config.get("archive_cid") not in (None, "", "0"))
        checked_parents = set(work.get("processed_parents", []))
        decisions = []
        for event in events:
            if int(event.get("type", 0)) not in {1, 2, 5, 6, 14, 23, 24}:
                continue
            parent = str(event.get("parent_id") or "")
            if not parent or parent in protected:
                continue
            if parent in checked_parents:
                continue
            checked_parents.add(parent)
            # Reserve a bounded parent batch; never let all-account noise consume
            # the full remote budget. Remaining work stays durable.
            if len(checked_parents) - len(work.get("processed_parents", [])) > 70:
                checked_parents.remove(parent)
                break
            # Validate ancestry without downloading a file or opening a web session.
            try:
                path = await self.adapter.directory_path(parent)
            except AdapterError as exc:
                if exc.code != "CID_NOT_VERIFIED":
                    raise
                # Deleted/moved historical directories must not block all new events.
                decisions.append(f"{parent}:目录校验失效")
                continue
            ancestors = set(path)
            if ancestors & protected:
                decisions.append(f"{parent}:位于输出或归档目录")
                continue
            matched = [source for source in sources if source.config["cid"] in ancestors]
            if len(matched) > 1:
                matched.sort(key=lambda source: path.index(source.config["cid"]), reverse=True)
            decisions.append(f"{parent}:匹配来源={matched[0].id if matched else '无'}")
            relevant_offline = [item for item in offline if item.target_cid in ancestors]
            offline_root = settings.get("offline_root_cid")
            if offline_root and offline_root != "0" and offline_root in ancestors:
                targets[parent] = {"cid": parent, "offline_only": False,
                                   "source_id": "115-offline", "one_shot": True,
                                   "reason": "生活事件：离线目录变化"}
            elif ancestors & date_cids:
                targets[parent] = {"cid": parent, "offline_only": False,
                                   "source_id": "115-offline", "one_shot": True,
                                   "reason": "生活事件：离线日期目录变化"}
            elif matched:
                targets[parent] = {"cid": parent, "offline_only": False,
                                   "source_id": matched[0].id, "one_shot": True,
                                   "reason": "监测：生活事件或离线下载完成"}
            elif relevant_offline:
                for item in relevant_offline:
                    targets[item.target_cid] = {
                        "cid": item.target_cid, "offline_only": True, "one_shot": True,
                        "reason": "生活事件：离线目录变化",
                    }
        remaining = {str(e.get("parent_id") or "") for e in events} - checked_parents - protected - {""}
        with self.db.session() as session:
            for cid, payload in targets.items():
                enqueue(session, "scan-cid:" + cid, "cloud_scan", payload)
            saved_seen = session.get(Settings, "offline-monitor-seen")
            seen = dict(saved_seen.value) if saved_seen else {}
            for key, parent in work["offline_candidates"].items():
                if parent in checked_parents or parent in protected:
                    seen[key] = now
            # Only identifiers and acknowledgment times; no links or filenames.
            seen = dict(sorted(seen.items(), key=lambda item: item[1])[-10000:])
            session.merge(Settings(key="offline-monitor-seen", value=seen))
            if remaining:
                work["processed_parents"] = list(checked_parents)
                session.merge(Settings(key="life-monitor-pending", value=work))
                session.merge(Settings(key="life-monitor-state", value=checkpoint | {"next": now + 1800, "backlog": True}))
            else:
                session.merge(Settings(key="life-monitor-state", value={
                    "cursor": work["newest"], "checked": work["cutoff"], "next": now + 1800,
                    "backlog": False,
                }))
                pending = session.get(Settings, "life-monitor-pending")
                if pending:
                    session.delete(pending)
            record(session, "system", "LIFE_BACKLOG" if remaining else "LIFE_CHECKED", "cloud_monitor",
                   f"生活事件{'待续查' if remaining else '检查完成'}：本轮读取 {pages} 页；"
                   f"压缩包候选 {len(events)} 条；核对目录 {len(checked_parents)} 个；"
                   f"匹配扫描 {len(targets)} 个；待处理目录 {len(remaining)} 个；"
                   + "；".join(decisions[:10]))
        return None
