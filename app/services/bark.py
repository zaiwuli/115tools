import time
from sqlalchemy import select
from app.persistence.models import Settings, Job
from app.scheduler.store import enqueue
from app.services.events import record


def normalize_path(value):
    from urllib.parse import unquote
    value = unquote(value.strip()).replace("\\", "/")
    parts = [x for x in value.split("/") if x]
    if any(x in {".", ".."} for x in parts):
        return ""
    return "/" + "/".join(parts)


def match_signal(session, fields):
    import re
    from app.persistence.models import Source
    from app.domain.links import extract_links
    text = fields.get("body", "")
    match = re.search(r"(?:路径|path)\s*[：:]\s*([^\r\n]+)", text, re.I)
    path = normalize_path(match[1]) if match else ""
    links, _ = extract_links(text)
    hashes = list({x.hash_value.lower() for x in links if x.hash_value})
    matches = []
    for source in session.scalars(select(Source).where(Source.kind == "115", Source.enabled)):
        prefix = normalize_path(source.config.get("bark_path", "")) if source.config.get("bark_enabled") else ""
        if prefix and prefix != "/" and (path == prefix or path.startswith(prefix + "/")):
            matches.append((len(prefix), source, prefix))
    matches.sort(key=lambda x:x[0], reverse=True)
    if not matches or (len(matches)>1 and matches[0][0]==matches[1][0]):
        return {"result":"未匹配来源或映射不唯一，未触发检查", "path":path}
    _, source, prefix = matches[0]
    if not hashes:
        return {"result":"未识别下载链接，未触发检查", "path":path}
    relative = path[len(prefix):].strip("/")
    if len(relative.split("/")) > 5:
        return {"result":"路径层级超过上限，未触发检查", "path":path}
    return {"result":"等待90秒窗口", "path":path,"source_id":source.id,
            "relative":relative,"hashes":hashes}


def dispatch(session, value):
    signals = value.get("signals", [])
    if signals:
        import hashlib, json
        key = "bark-check:"+hashlib.sha256(json.dumps(signals,sort_keys=True).encode()).hexdigest()
        enqueue(session,key,"bark_check",{"signals":signals})
    value["dispatched"] = True
    record(session,"system","BARK_WINDOW_DISPATCHED","cloud_monitor",
           f"外部触发窗口结束，合并 {value['count']} 条信号；定向核对 {len(signals)} 组目标")


async def run(db, job):
    with db.session() as session:
        saved = session.get(Settings,"bark-window")
        if not saved or saved.value.get("dispatched"): return None
        value = dict(saved.value)
        if time.time() < value["due"]: return value["due"]
        dispatch(session,value)
        saved.value = value
    return None


async def check(engine, job):
    from app.persistence.models import Source, Offline
    from app.adapters.p115 import AdapterError
    signals = job.payload["signals"]
    # One shared page updates all known tasks; discovery is limited by signals.
    await engine.offline.poll(manual=True)
    snapshot = await engine.adapter.offline_task_snapshot()
    validated = []
    resolved = {}
    for signal in signals:
        with engine.db.session() as session:
            source = session.get(Source,signal["source_id"])
        if not source or not source.enabled or not source.config.get("bark_enabled"): continue
        try:
            key = (source.id,signal["relative"])
            if key not in resolved:
                cid = source.config["cid"]
                for component in filter(None,signal["relative"].split("/")):
                    page = await engine.adapter.files(cid)
                    choices = [f for f in page.files if f.is_dir and f.name == component]
                    if page.next_offset is not None or len(choices)!=1:
                        raise AdapterError("BARK_PATH_NOT_UNIQUE")
                    cid = choices[0].fid
                resolved[key] = cid
            cid = resolved[key]
            allowed = [r for r in snapshot["rows"] if str(r.get("info_hash") or r.get("hash") or "").lower() in signal["hashes"]
                       and str(r.get("wp_path_id") or "") == cid]
            if allowed:
                validated.append(signal)
            else:
                with engine.db.session() as session:
                    record(session,"system","BARK_TARGET_PENDING","cloud_monitor",
                           f"来源 {source.name}；目标目录 {cid}；第一页未找到同哈希同目标任务，待核对")
        except AdapterError as exc:
            with engine.db.session() as session:
                record(session,"system","BARK_PATH_FAILED","cloud_monitor",f"来源 {source.name}；路径解析失败：{exc.code}")
    await engine.monitor.poll_external(signals=validated)
    for signal in signals:
        with engine.db.session() as session:
            source = session.get(Source,signal["source_id"])
        if not source or not source.enabled or not source.config.get("bark_enabled"):
            continue
        hashes = set(signal["hashes"])
        rows = [r for r in snapshot["rows"] if str(r.get("info_hash") or r.get("hash") or "").lower() in hashes]
        result = "第一页未找到对应下载，待核对"
        if signal in validated:
            result = "已核对离线任务，按实际状态继续处理"
        elif rows:
            result = "路径未确认或目标不一致，未发现新任务"
        with engine.db.session() as session:
            record(session,"system","BARK_TARGET_CHECKED","cloud_monitor",
                   f"来源 {source.name}；相对目录 {signal['relative'] or '/'}；{result}")
            saved = session.get(Settings,"bark-messages")
            if saved:
                messages = list(saved.value.get("items", []))
                for message in messages:
                    if message.get("match",{}).get("path")==signal["path"]:
                        message = message.setdefault("match",{})
                        message["result"] = result
                saved.value = {"items":messages}
    return None


def message_fields(body, message_path, device_key):
    import re
    parts = message_path.split("/", 2) if message_path else []
    fields = {"title":str(body.get("title") or (parts[0] if parts else "")),
              "body":str(body.get("body") or ("/".join(parts[1:]) if len(parts)>1 else "")),
              "group":str(body.get("group") or "")}
    for key, text in fields.items():
        text = text.replace(device_key,"[令牌已隐藏]")
        text = re.sub(r"(?i)(bearer\s+)[^\s]+",r"\1[已隐藏]",text)
        fields[key] = text[:4096]
    return fields
