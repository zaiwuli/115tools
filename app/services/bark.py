import time
from sqlalchemy import select
from app.persistence.models import Settings, Job
from app.scheduler.store import enqueue
from app.services.events import record


def dispatch(session, value):
    running = session.scalar(select(Job).where(Job.key == "life-monitor"))
    if not running or running.state not in {"READY", "RUNNING", "LEASED", "RECONCILING"}:
        enqueue(session, "life-monitor", "life_monitor", {"trigger":"bark"})
    value["dispatched"] = True
    record(session,"system","BARK_WINDOW_DISPATCHED","cloud_monitor",
           f"外部触发窗口结束，合并 {value['count']} 条信号；已提交或合并云端变化检查")


async def run(db, job):
    with db.session() as session:
        saved = session.get(Settings,"bark-window")
        if not saved or saved.value.get("dispatched"): return None
        value = dict(saved.value)
        if time.time() < value["due"]: return value["due"]
        if not db.settings()["life_monitor_enabled"]:
            value["dispatched"] = True
            record(session,"system","BARK_WINDOW_SKIPPED","cloud_monitor","监测已关闭，跳过外部触发")
        else:
            dispatch(session,value)
        saved.value = value
    return None
