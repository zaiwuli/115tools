from sqlalchemy import select
import uuid

from app.persistence.models import Settings, Identity, Transfer, Operation, Task
from app.scheduler.store import enqueue
from app.services.events import record, transition


def can_download_local(task):
    return bool(
        task.source == "115" and task.locator.get("pickcode")
        and not task.cancel_requested and not task.policy.get("fallback_approved")
        and task.state not in {"SUCCEEDED", "CANCELED", "SKIPPED"}
        and (task.stage.startswith("cloud_") or task.stage == "local_fallback")
        and task.output.get("failure_archive_result") not in {"PENDING", "FAILED"}
    )


def can_repeat_local(task):
    return bool(task.source == "115" and task.state == "SUCCEEDED"
                and task.locator.get("pickcode") and not task.output.get("path")
                and not task.policy.get("manual_local_parent"))


def repeat_local(session, task):
    if not can_repeat_local(task):
        raise ValueError("LOCAL_REPEAT_UNAVAILABLE")
    existing = session.scalar(select(Task).where(
        Task.policy["manual_local_parent"].as_string() == task.id,
        Task.state.not_in(["SUCCEEDED", "FAILED", "CANCELED", "SKIPPED"]),
    ))
    if existing:
        raise ValueError("LOCAL_REPEAT_ALREADY_RUNNING")
    child = Task(object_key=task.object_key + ":manual-local", content_key=task.content_key +
                 ":manual-local:" + uuid.uuid4().hex,
                 source="115", name=task.name, size=task.size, stage="cache",
                 locator=dict(task.locator), policy=task.policy | {
                     "manual_local_parent": task.id, "fallback_approved": True,
                     "original_action": "retain", "delete_approved": False,
                     "cloud_remote_unsettled": False,
                 })
    child.output = {"local_final_name": task.name.rsplit(".", 1)[0][:70] + "-local-" + uuid.uuid4().hex[:10]}
    session.add(child)
    session.flush()
    enqueue(session, "local:" + child.id, "local_extract", {"task_id": child.id})
    record(session, task.id, "MANUAL_LOCAL_CREATED", "complete", "已创建独立本地处理：" + child.id)
    record(session, child.id, "MANUAL_LOCAL_CREATED", "cache", "来源云端成功任务：" + task.id)
    return child


def expire_cloud(session, task):
    task.policy = task.policy | {
        "cloud_remote_unsettled": bool(task.remote_id) or task.stage in {
            "cloud_output", "cloud_export_unknown",
        },
    }
    transition(session, task, "WAITING_APPROVAL", "local_fallback",
               error="CLOUD_PROCESSING_TIMEOUT")
    task.wait_reason = "APPROVAL"
    active = session.get(Settings, "cloud_active")
    if active and active.value.get("value") == task.id:
        active.value = {"value": ""}
    record(session, task.id, "CLOUD_TIMEOUT", "local_fallback",
           "云处理超过设定期限，已释放本工具云队列，可选择下载到本地解压；远端操作可能继续")
    if not task.policy.get("cloud_remote_unsettled"):
        approve_automatic(session, task)


def can_fallback(task):
    return bool(task.source == "115" and task.locator.get("pickcode") and (
        (task.state == "WAITING_APPROVAL" and task.stage == "local_fallback") or
        (task.state == "FAILED" and task.stage in {
            "cloud_prepare", "cloud_inspect", "cloud_output", "cloud_progress", "cloud_verify",
        } and task.error in {"REMOTE_REJECTED", "CLOUD_CONTENT_FAILED", "CLOUD_EXPORT_REJECTED"})
    ) and task.output.get("failure_archive_result") not in {"PENDING", "FAILED"})


def start_local(session, task):
    from app.services.execution import begin_execution
    unsettled = task.policy.get("cloud_remote_unsettled") or (
        bool(task.remote_id) and task.stage != "local_fallback"
    ) or task.stage in {"cloud_output", "cloud_export_unknown"}
    begin_execution(session, task)
    for operation in session.scalars(select(Operation).where(
        Operation.task_id == task.id,
        Operation.kind.in_(["cloud_prepare", "cloud_export"]),
        Operation.status.in_(["PREPARED", "UNKNOWN"]),
    )):
        operation.status = "ABANDONED"
    task.policy = task.policy | {"fallback_approved": True}
    if unsettled:
        task.policy = task.policy | {
            "cloud_remote_unsettled": True,
            "previous_cloud_remote_id": task.remote_id or task.policy.get("previous_cloud_remote_id", ""),
        }
    task.remote_id, task.output = "", {}
    task.completed_at, task.wait_reason, task.cancel_requested = None, "", False
    identity = session.get(Identity, task.content_key)
    if identity:
        session.delete(identity)
    transfer = session.get(Transfer, task.id)
    if transfer:
        transfer.attempts, transfer.error, transfer.state = 0, "", "READY"
    transition(session, task, "READY", "cache", manual_retry=task.state == "FAILED")
    active = session.get(Settings, "cloud_active")
    if active and active.value.get("value") == task.id:
        active.value = {"value": ""}
    enqueue(session, "local:" + task.id, "local_extract", {"task_id": task.id})


def approve_automatic(session, task):
    if (task.cancel_requested or not task.policy.get("auto_fallback")
            or task.policy.get("fallback_approved") or not can_fallback(task)):
        return
    start_local(session, task)
    record(session, task.id, "FALLBACK_AUTHORIZED", "cache", "按处理规则批准下载原包并本地解压")


def cloud_retry_status(task):
    from app.services.archives import is_archive
    success = task.policy.get("cloud_succeeded") or task.output.get("verification") in {"remote_manifest", "remote_reported_complete", "remote_completed_and_visible"}
    visible = bool(task.source == "115" and is_archive(task.name) and not success)
    reason = ""
    if task.policy.get("cloud_remote_unsettled") or task.state == "RECONCILING":
        reason = "请先核对上一次云解压结果"
    elif task.state not in {"FAILED", "CANCELED", "SKIPPED", "SUCCEEDED", "WAITING_APPROVAL"}:
        reason = "任务已在处理中"
    elif task.wait_reason == "IDENTITY":
        reason = "请先核对内容身份冲突"
    elif task.output.get("failure_archive_result") in {"PENDING", "FAILED"}:
        reason = "请先核对原包归档位置"
    return {"cloud_retry_visible": visible, "can_retry_cloud": visible and not reason, "cloud_retry_reason": reason}
