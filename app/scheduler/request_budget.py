"""Persistent request budgets shared by every adapter call in a scheduled job."""
from contextvars import ContextVar

current_budget = ContextVar("remote_request_budget", default=None)
LIMITS = {"cloud_extract": 60, "cloud_scan": 200, "life_monitor": 100,
          "offline_poll": 100, "offline_submit": 200, "postprocess": 200,
          "failure_archive": 200, "seed_cleanup": 200, "document_copy": 200}


def consume():
    budget = current_budget.get()
    if budget is None:
        return
    db, identifier, limit = budget
    from app.persistence.models import Job
    from app.adapters.p115 import AdapterError
    with db.session() as session:
        job = session.get(Job, identifier)
        count = job.payload.get("remote_requests", 0)
        if count >= limit:
            raise AdapterError("REMOTE_QUERY_BUDGET_EXHAUSTED")
        job.payload = job.payload | {"remote_requests": count + 1, "remote_request_limit": limit}
