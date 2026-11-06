"""Low-frequency checks of an existing remote extraction; never submit new work."""
import time
from sqlalchemy import select
from app.persistence.models import Task, Identity, Settings
from app.scheduler.store import enqueue
from app.services.events import record, transition
from app.adapters.p115 import AdapterError


def schedule(session, settings, now):
    for task in session.scalars(select(Task).where(Task.state == "WAITING_APPROVAL", Task.stage == "local_fallback")):
        check = task.policy.get("timeout_check", {})
        if check.get("state") != "scheduled":
            continue
        if not settings["cloud_timeout_check_enabled"] or task.cancel_requested or task.policy.get("fallback_approved"):
            task.policy = task.policy | {"timeout_check": check | {"state": "stopped"}}
            continue
        if check["used"] < check["limit"] and check["due"] <= now:
            enqueue(session, "timeout-check:" + task.id, "cloud_timeout_check", {"task_id": task.id})


async def run(engine, job):
    with engine.db.session() as session:
        task = session.get(Task, job.payload["task_id"])
        check = task.policy.get("timeout_check", {}) if task else {}
        if not task or task.state != "WAITING_APPROVAL" or task.stage != "local_fallback" or task.cancel_requested or task.policy.get("fallback_approved") or check.get("state") != "scheduled":
            return
        if not engine.db.settings()["cloud_timeout_check_enabled"] or check["used"] >= check["limit"] or check["due"] > time.time():
            return
        remote_id = check["remote_id"]
        check = check | {"used": check["used"] + 1}
        # Persist the next delay before I/O: crashes consume an attempt as well.
        check["due"] = time.time() + engine.db.settings()["cloud_timeout_check_interval_seconds"]
        task.policy = task.policy | {"timeout_check": check}
        generation = task.policy.get("execution_generation", 0)
    try:
        result = await engine.adapter.extract_status(remote_id)
        error = ""
    except AdapterError as exc:
        result, error = "unknown", exc.code
    with engine.db.session() as session:
        task = session.get(Task, job.payload["task_id"])
        if task.policy.get("timeout_check", {}).get("state") != "scheduled" or not engine.db.settings()["cloud_timeout_check_enabled"]:
            return
        if task.policy.get("execution_generation", 0) != generation or task.policy.get("fallback_approved") or task.cancel_requested or task.stage != "local_fallback":
            return
        check = task.policy.get("timeout_check", {})
        stopped = error in {"AUTH_OR_CHALLENGE_REQUIRED", "REMOTE_RATE_LIMIT", "REMOTE_CIRCUIT_OPEN"}
        check = check | {"last_result": result, "error": error,
                        "state": "finished" if result in {"success", "failed"} else "stopped" if stopped or check["used"] >= check["limit"] else "scheduled"}
        task.policy = task.policy | {"timeout_check": check}
        record(session, task.id, "TIMEOUT_CHECK", "local_fallback",
               f"超时后核对 {check['used']}/{check['limit']}：{result}" + (f"；{error}" if error else ""))
        if result == "success":
            task.policy = task.policy | {"cloud_succeeded": True, "cloud_remote_unsettled": False}
            task.output = task.output | {"verification": "remote_reported_complete"}
            transition(session, task, "SUCCEEDED", "complete")
            task.wait_reason = ""
            session.merge(Identity(key=task.content_key, task_id=task.id, result="success"))
            session.merge(Settings(key="cloud_cooldown", value={"value": time.time() + engine.db.settings()["cloud_cooldown_seconds"]}))
            if (task.policy.get("original_action", "retain") != "retain" or task.policy.get("cleanup_enabled") or task.policy.get("squash_root", True)):
                enqueue(session, "postprocess:" + task.id, "postprocess", {"task_id": task.id})
        elif result == "failed":
            task.policy = task.policy | {"cloud_remote_unsettled": False}
            task.error = "CLOUD_CONTENT_FAILED"
