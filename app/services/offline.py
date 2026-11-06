import asyncio
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, func

from app.adapters.p115 import AdapterError
from app.domain.links import extract_links, match_name
from app.persistence.models import (
    Batch, BatchItem, DateFolder, Offline, Operation, ResourceLink,
    OfflineExecution, Settings, Event, Job,
)
from app.scheduler.store import enqueue
from app.services.events import record

logger = logging.getLogger("115work")


POLLABLE = {"submitted", "downloading", "unknown", "submission_unknown"}
RETRYABLE = {"failed", "submit_failed"}
WINDOW_SECONDS = 90
SCAN_DELAYS = (120, 300)
POLL_DEADLINE_SECONDS = 86400


class OfflineService:
    def __init__(self, db, adapter, job_store):
        self.db, self.adapter, self.job_store = db, adapter, job_store
        self.import_lock = asyncio.Lock()
        self.poll_lock = asyncio.Lock()
        self.date_lock = asyncio.Lock()
        self.snapshot = None

    def register(self, session, text: str, auto_extract: bool = True, now: float | None = None, *, links=None):
        links, errors = extract_links(text) if links is None else (links, [])
        if not links and not errors:
            raise ValueError("NO_LINKS")
        now = time.time() if now is None else now
        date = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        root = self.db.settings()["offline_root_cid"]
        batch = Batch(target_date=date, root_cid=root, auto_extract=auto_extract,
                      invalid_count=len(errors), created_at=now)
        session.add(batch)
        session.flush()
        created = duplicates = 0
        for link in links:
            existing = session.scalar(select(Offline).where(
                Offline.account_id == "115-main", Offline.identity == link.identity
            ))
            duplicate = existing is not None
            if existing is None:
                existing = Offline(
                    identity=link.identity, original_link=link.original, name=link.name,
                    kind=link.kind, size=link.size, hash_value=link.hash_value,
                    batch_id=batch.id, target_date=date, root_cid=root,
                    auto_extract=auto_extract, created_at=now, updated_at=now,
                )
                session.add(existing)
                session.flush()
                created += 1
                record(session, existing.id, "REGISTERED", "offline_submit", "Link registered")
            else:
                duplicates += 1
            session.add(BatchItem(batch_id=batch.id, task_id=existing.id, duplicate=duplicate))
        if created:
            enqueue(session, "offline-submit:" + batch.id, "offline_submit", {"batch_id": batch.id})
            window = next((x for x in session.scalars(select(Settings).where(
                Settings.key.startswith("offline-window:"),
            )) if x.value["root"] == root and x.value["date"] == date
                and now < x.value["start"] + WINDOW_SECONDS), None)
            ids = list(session.scalars(select(Offline.id).where(Offline.batch_id == batch.id)))
            if window:
                window.value = window.value | {"ids": window.value["ids"] + ids}
            else:
                session.add(Settings(key="offline-window:" + batch.id, value={
                    "root": root, "date": date, "start": now, "ids": ids, "round": 0,
                }))
        record(session, batch.id, "IMPORT", "offline_submit",
               f"Queued {created}; duplicates {duplicates}; invalid {len(errors)}")
        return {"batch_id": batch.id, "queued": created, "duplicates": duplicates,
                "invalid": len(errors), "message": f"Queued {created}; duplicates {duplicates}; invalid {len(errors)}"}

    async def import_text(self, text: str, auto_extract: bool = True, now: float | None = None):
        async with self.import_lock:
            with self.db.session() as session:
                return self.register(session, text, auto_extract, now)

    def register_torrent(self, session, raw, auto_extract=True):
        from app.domain.torrents import parse_torrent
        link = parse_torrent(raw)
        from app.domain.rules import evaluate
        policy = evaluate(session, "115-torrent", link.name, 0)
        result = self.register(session, "", False, links=[link])
        if result["queued"]:
            item = session.scalar(select(Offline).where(Offline.batch_id == result["batch_id"]))
            session.add(Settings(key="seed-policy:" + item.id, value={"policy": policy, "result": "WAITING_DOWNLOAD"}))
        return result

    def schedule_discovery(self, session, now):
        self.schedule_completed_discovery(session, now)
        for window in session.scalars(select(Settings).where(Settings.key.startswith("offline-window:"))):
            value = window.value
            number = value["round"]
            if number >= 2 or now < value["start"] + SCAN_DELAYS[number]:
                continue
            grouped = {}
            for identifier in value["ids"]:
                item = session.get(Offline, identifier)
                if not item or not item.target_cid or item.status in {"failed", "submit_failed", "canceled", "success"}:
                    continue
                if session.get(Settings, "seed-policy:" + identifier) or item.stage in {"complete", "discover_review"}:
                    continue
                if session.scalar(select(ResourceLink.id).where(ResourceLink.offline_id == identifier)):
                    continue
                grouped.setdefault(item.target_cid, []).append(identifier)
            for cid, ids in grouped.items():
                enqueue(session, "scan-cid:" + cid, "cloud_scan", {
                    "cid": cid, "offline_only": True, "offline_ids": ids, "one_shot": True,
                    "reason": "离线窗口首扫" if number == 0 else "离线窗口唯一补扫",
                })
                record(session, cid, "DISCOVERY_SCHEDULED", "discover",
                       f"目标 CID {cid}，本轮 {len(ids)} 条，第 {number + 1} 次扫描")
            window.value = value | {"round": number + 1}

    def schedule_completed_discovery(self, session, now):
        for item in session.scalars(select(Offline).where(
            Offline.status == "success", Offline.stage == "discover",
        )):
            if not item.target_cid or session.get(Settings, "seed-policy:" + item.id):
                continue
            if session.scalar(select(ResourceLink.id).where(ResourceLink.offline_id == item.id)):
                continue
            marker = session.get(Settings, "discovery-success:" + item.id)
            if marker is None:
                marker = Settings(key="discovery-success:" + item.id, value={"rounds": 0, "due": now, "observed_at": now})
                session.add(marker)
            value = marker.value
            if value.get("rounds", 0) >= 2:
                item.stage, item.error = "discover_review", "DISCOVERY_NOT_FOUND_AFTER_TWO_SCANS"
                continue
            if now < value.get("due", 0):
                continue
            existing = session.scalar(select(Job).where(Job.key == "scan-cid:" + item.target_cid))
            # An in-flight scan is reused; completion will count it only if it
            # read the directory after this item was observed successful.
            if existing and existing.state in {"RUNNING", "LEASED", "RECONCILING"}:
                continue
            enqueue(session, "scan-cid:" + item.target_cid, "cloud_scan", {
                "cid": item.target_cid, "offline_only": True, "offline_ids": [item.id],
                "one_shot": True, "reason": "下载成功后产物扫描",
            })

    async def target_folder(self, root: str, date: str) -> str:
        async with self.date_lock:
            with self.db.session() as session:
                cached = session.scalar(select(DateFolder).where(
                    DateFolder.root_cid == root, DateFolder.target_date == date,
                    DateFolder.account_id == "115-main",
                ))
                cached_cid = cached.cid if cached else None
            if cached_cid and not hasattr(self.adapter, "directory_path"):
                return cached_cid
            if cached_cid:
                try:
                    path = await self.adapter.directory_path(cached_cid)
                    if len(path) >= 2 and path[-2:] == [root, cached_cid]:
                        return cached_cid
                except AdapterError as exc:
                    if exc.code != "CID_NOT_VERIFIED":
                        raise
                # Re-resolve only this date under the configured parent. Never
                # redirect historical downloads or scan a silently returned root.
                with self.db.session() as session:
                    old = session.scalar(select(DateFolder).where(
                        DateFolder.root_cid == root, DateFolder.target_date == date,
                        DateFolder.account_id == "115-main",
                    ))
                    if old:
                        session.delete(old)
                    operation = session.get(Operation, f"date-folder:115-main:{root}:{date}")
                    if operation:
                        session.delete(operation)
                    record(session, root, "DATE_FOLDER_REVALIDATED", "offline_submit",
                           f"日期目录 {date} 的旧 CID 已失效或移动，重新解析目标目录")
            from app.services.folders import durable_folder
            cid = await durable_folder(self.db, self.adapter, root, date,
                                       f"date-folder:115-main:{root}:{date}")
            with self.db.session() as session:
                session.add(DateFolder(root_cid=root, target_date=date, cid=cid))
            return cid

    async def submit_job(self, job) -> float | None:
        with self.db.session() as session:
            self.job_store.owned(session, job)
            tasks = list(session.scalars(select(Offline).where(
                Offline.batch_id == job.payload["batch_id"], Offline.status == "queued"
            ).order_by(Offline.created_at).limit(10)))
        if not tasks:
            return None
        logger.info("正在向 115 提交 %d 条离线下载链接。", len(tasks))
        cid = await self.target_folder(tasks[0].root_cid, tasks[0].target_date)
        with self.db.session() as session:
            self.job_store.owned(session, job)
            batch = session.get(Batch, job.payload["batch_id"])
            batch.target_cid = cid
            for item in tasks:
                current = session.get(Offline, item.id)
                key = f"offline:{current.id}:{current.generation}"
                operation = session.get(Operation, key)
                if operation and operation.status in {"UNKNOWN", "PREPARED", "ACCEPTED"}:
                    raise RuntimeError("SUBMISSION_MUST_RECONCILE")
                session.merge(Operation(key=key, task_id=current.id, kind="offline_submit"))
                current.target_cid, current.status = cid, "submitting"
                session.add(OfflineExecution(offline_id=current.id, generation=current.generation,
                            target_date=current.target_date, target_cid=cid,
                            auto_extract=current.auto_extract, status="submitting"))
        try:
            await self.adapter.submit([t.original_link for t in tasks], cid)
        except AdapterError as exc:
            logger.error("离线下载提交失败：%s", exc.code)
            with self.db.session() as session:
                self.job_store.owned(session, job)
                for item in tasks:
                    current = session.get(Offline, item.id)
                    operation = session.get(Operation, f"offline:{current.id}:{current.generation}")
                    operation.status = "UNKNOWN" if exc.uncertain else "REJECTED"
                    current.status = "submission_unknown" if exc.uncertain else "submit_failed"
                    current.error = exc.code
                    execution = session.scalar(select(OfflineExecution).where(
                        OfflineExecution.offline_id == current.id, OfflineExecution.generation == current.generation))
                    execution.status, execution.error = current.status, current.error
                    current.next_check = time.time() + 15 if exc.uncertain else None
                    record(session, current.id, current.status.upper(), "offline_submit", exc.code, "offline")
            return time.time() + 2 if len(tasks) == 10 else None
        with self.db.session() as session:
            self.job_store.owned(session, job)
            for item in tasks:
                current = session.get(Offline, item.id)
                session.get(Operation, f"offline:{current.id}:{current.generation}").status = "ACCEPTED"
                current.status, current.next_check = "submitted", time.time() + 15
                current.error = ""
                execution = session.scalar(select(OfflineExecution).where(
                    OfflineExecution.offline_id == current.id, OfflineExecution.generation == current.generation))
                execution.status = current.status
                record(session, current.id, "SUBMITTED", "offline_download", "成功发送链接，等待下载确认", "offline")
                record(session, current.id, "DOWNLOAD_STARTED", "offline_download", "已开始下载", "offline")
                logger.info("成功发送链接，等待下载确认。")
                logger.info("已开始下载：%s", current.name)
        self.snapshot = None
        return time.time() + 2 if len(tasks) == 10 else None

    def schedule_poll(self, session, now: float, manual: bool = False):
        if not manual:
            self.pause_expired(session, now)
        query = select(Offline).where(Offline.status.in_(POLLABLE), Offline.stage != "handed_off")
        if not manual:
            query = query.where(Offline.next_check <= now)
        if session.scalar(query.limit(1)):
            enqueue(session, "offline-poll:115-main", "offline_poll", {"manual": manual})

    def pause_expired(self, session, now):
        # Observation continues only through the shared monitor after quick checks.
        return

    async def poll(self, job=None, now: float | None = None, manual: bool = False):
        now = time.time() if now is None else now
        async with self.poll_lock:
            with self.db.session() as session:
                if not manual:
                    self.pause_expired(session, now)
                query = select(Offline).where(Offline.status.in_(POLLABLE), Offline.stage != "handed_off")
                if not manual:
                    query = query.where(Offline.next_check <= now)
                tasks = list(session.scalars(query.where(Offline.kind != "external")))
            if not tasks:
                return
            if self.snapshot and now - self.snapshot[0] < 20:
                statuses = self.snapshot[1]
            else:
                statuses = await self.adapter.offline_snapshot()
                self.snapshot = (now, statuses)
            with self.db.session() as session:
                if job:
                    self.job_store.owned(session, job)
                for item in tasks:
                    current = session.get(Offline, item.id)
                    if current is None or current.status not in POLLABLE:
                        continue
                    matches = [
                        remote for remote in statuses
                        if (current.hash_value and current.hash_value == remote.hash_value)
                        or current.original_link == remote.link
                    ]
                    if not matches:
                        matches = [remote for remote in statuses
                                   if remote.name and match_name(remote.name) == match_name(current.name)]
                    status = matches[0].status if len(matches) == 1 else "unknown"
                    if status != current.status:
                        current.status, current.updated_at = status, now
                        current.error = "REMOTE_OFFLINE_FAILED" if status == "failed" else ""
                        record(session, current.id, status.upper(), "offline_download",
                               ("下载成功" if status == "success" else
                                "下载失败：" + current.error if status == "failed" else
                                "下载状态：" + status), "offline")
                        if status == "success":
                            logger.info("下载成功：%s", current.name)
                        elif status == "failed":
                            logger.error("下载失败：%s（%s）", current.name, current.error)
                    advance = current.next_check is not None and current.next_check <= now
                    if advance:
                        current.check_count += 1
                    execution = session.scalar(select(OfflineExecution).where(
                        OfflineExecution.offline_id == current.id, OfflineExecution.generation == current.generation))
                    if execution:
                        execution.status, execution.error = current.status, current.error
                    interval = {1: 180, 2: 300}.get(current.check_count)
                    if advance or status not in POLLABLE:
                        current.next_check = now + interval if status in POLLABLE and interval else None
                    if status in POLLABLE:
                        current.stage = "offline_download"
                    if not matches:
                        current.status, current.stage, current.next_check = "unknown", "offline_manual", None
                    if status == "success":
                        current.stage = "discover"
                        seed = session.get(Settings, "seed-policy:" + current.id)
                        if seed:
                            enqueue(session, "seed-cleanup:" + current.id, "seed_cleanup",
                                    {"offline_id": current.id})
                            current.stage = "seed_cleanup"

    def bind_candidates(self, session, cid: str, candidates: list, task_for_file, offline_ids=None):
        def completed(item):
            item.updated_at = time.time()
            execution = session.scalar(select(OfflineExecution).where(
                OfflineExecution.offline_id == item.id,
                OfflineExecution.generation == item.generation,
            ))
            if execution:
                execution.status, execution.error = item.status, item.error

        tasks = list(session.scalars(select(Offline).where(
            Offline.target_cid == cid,
            Offline.status.in_(list(POLLABLE | {"success"})),
        )))
        if offline_ids is not None:
            tasks = [item for item in tasks if item.id in offline_ids]
        for item in tasks:
            if item.kind == "external" and (item.status != "success" or item.stage in {"complete", "handed_off"}):
                continue
            seed = session.get(Settings, "seed-policy:" + item.id)
            if seed:
                # Only a remote completion observation may authorize seed cleanup.
                continue
            if session.scalar(select(ResourceLink).where(ResourceLink.offline_id == item.id)):
                continue
            if item.kind in {"magnet", "torrent"}:
                directories = [file for file in candidates if file.is_dir and
                               match_name(file.name) == match_name(item.name)]
                if len(directories) == 1:
                    item.name = directories[0].name
                    parents = {directories[0].fid}
                    while True:
                        expanded = parents | {file.fid for file in candidates if file.is_dir and file.cid in parents}
                        if expanded == parents:
                            break
                        parents = expanded
                    children = [file for file in candidates if not file.is_dir and file.cid in parents]
                    if children:
                        archive_count = 0
                        for file in children:
                            from app.services.archives import is_archive
                            if not is_archive(file.name):
                                continue
                            task = task_for_file(session, file, item.auto_extract, offline_id=item.id)
                            if task is None:
                                continue
                            session.add(ResourceLink(offline_id=item.id, task_id=task.id,
                                                     evidence="magnet-directory"))
                            from app.services.archives import is_archive
                            if is_archive(file.name):
                                archive_count += 1
                            if item.auto_extract and is_archive(file.name):
                                record(session, task.id, "CLOUD_TAKEOVER", task.stage,
                                       "离线产物已关联，云解压队列接管", "cloud")
                        item.status = "success"
                        item.stage = "join_review" if archive_count else "complete"
                        item.error = "MAGNET_SCOPE_COMPLETENESS_REQUIRES_REVIEW" if archive_count else ""
                        item.next_check = None
                        completed(item)
                        continue
            scored = []
            for file in candidates:
                if file.is_dir:
                    continue
                same_name = match_name(file.name) == match_name(item.name)
                same_size = bool(item.size) and item.size == file.size
                score = 30 if same_name and same_size else 20 if same_name else 10 if same_size else 0
                if score:
                    scored.append((score, file))
            if not scored:
                continue
            best_score = max(x[0] for x in scored)
            best = [f for score, f in scored if score == best_score]
            if len(best) != 1:
                item.stage, item.error = "discover", "AMBIGUOUS_RESOURCE_MATCH"
                continue
            file = best[0]
            task = task_for_file(session, file, item.auto_extract, offline_id=item.id)
            if task is None:
                if item.status != "success":
                    record(session, item.id, "SUCCESS", "complete",
                           "离线产物已发现，非压缩包不进入解压队列", "offline")
                item.status, item.stage, item.error, item.next_check = "success", "complete", "", None
                completed(item)
                continue
            session.add(ResourceLink(offline_id=item.id, task_id=task.id, evidence=str(best_score)))
            from app.services.archives import is_archive
            if item.auto_extract and is_archive(file.name):
                record(session, task.id, "CLOUD_TAKEOVER", task.stage,
                       "离线完成，原包已关联并交给云解压队列", "cloud")
            if item.status != "success":
                record(session, item.id, "SUCCESS", "discover", "File discovered", "offline")
            item.status, item.stage, item.error, item.next_check = "success", task.stage, "", None
            completed(item)

    def retry(self, task_id: str | None = None) -> int:
        with self.db.session() as session:
            query = select(Offline).where(Offline.status.in_(RETRYABLE))
            if task_id:
                query = query.where(Offline.id == task_id)
            tasks = list(session.scalars(query))
            if task_id and not tasks:
                raise ValueError("ONLY_TERMINAL_FAILURE_CAN_RETRY")
            for item in tasks:
                if item.kind == "external":
                    continue
                cutoff = session.scalar(select(func.max(Event.id)).where(Event.task_id == item.id)) or 0
                session.merge(Settings(key="offline-notification-cutoff:" + item.id, value={"id": cutoff}))
                item.status, item.error, item.check_count = "queued", "", 0
                item.retry_count += 1
                item.generation += 1
                enqueue(session, "offline-submit:" + item.batch_id, "offline_submit",
                        {"batch_id": item.batch_id})
                key = f"offline-window:retry:{item.id}:{item.generation}"
                session.add(Settings(key=key, value={
                    "root": item.root_cid, "date": item.target_date,
                    "start": time.time(), "ids": [item.id], "round": 0,
                }))
                record(session, item.id, "MANUAL_RETRY", "offline_submit", "Retry authorized")
        return len(tasks)

    def recover(self, session):
        for item in session.scalars(select(Offline).where(Offline.status == "submitting")):
            item.status, item.next_check = "submission_unknown", time.time()
            operation = session.get(Operation, f"offline:{item.id}:{item.generation}")
            if operation:
                operation.status = "UNKNOWN"
        for item in session.scalars(select(Offline).where(Offline.status == "queued")):
            enqueue(session, "offline-submit:" + item.batch_id, "offline_submit",
                    {"batch_id": item.batch_id})
