"""Ephemeral, factual progress events for the ordinary chat SSE stream."""
from time import monotonic

import orjson


STAGES = {
    "route": "查找相关数据表",
    "connect": "连接数据源",
    "generate": "生成查询语句",
    "permission": "校验查询权限",
    "query": "查询数据",
    "result": "整理结果与图表",
}


class ChatProgress:
    def __init__(self, enabled=True, clock=monotonic):
        self.enabled = enabled
        self.clock = clock
        self.started = {}
        self.statuses = {}
        self.active = None
        self.detail_started = {}
        self.detail_statuses = {}
        self.active_detail = None

    def update(self, stage, status="running", detail=""):
        if not self.enabled:
            return []
        now = self.clock()
        if self.statuses.get(stage) in ("completed", "failed", "skipped"):
            return []
        self.started.setdefault(stage, now)
        self.statuses[stage] = status
        if status == "running":
            self.active = stage
        elif self.active == stage:
            self.active = None
        payload = dict(type="progress", stage=stage, status=status,
                       elapsedMs=round((now - self.started[stage]) * 1000),
                       detail=detail, message=("正在" if status == "running" else "") + STAGES[stage])
        return ["data:" + orjson.dumps(payload).decode() + "\n\n"]

    def update_detail(self, stage, detail_stage, status="running"):
        if not self.enabled or self.statuses.get(stage) in ("completed", "failed", "skipped"):
            return []
        key = (stage, detail_stage)
        if self.detail_statuses.get(key) in ("completed", "failed", "skipped"):
            return []
        now = self.clock()
        self.started.setdefault(stage, now)
        self.detail_started.setdefault(key, now)
        self.detail_statuses[key] = status
        self.statuses[stage] = "running"
        self.active = stage
        if status == "running":
            self.active_detail = key
        elif self.active_detail == key:
            self.active_detail = None
        payload = dict(type="progress", stage=stage, status="running",
                       elapsedMs=round((now - self.started[stage]) * 1000),
                       detail="", message="正在" + STAGES[stage],
                       detailStage=detail_stage, detailStatus=status,
                       detailElapsedMs=round((now - self.detail_started[key]) * 1000))
        return ["data:" + orjson.dumps(payload).decode() + "\n\n"]

    def fail(self):
        events = []
        if self.active_detail:
            events.extend(self.update_detail(*self.active_detail, "failed"))
        if self.active:
            events.extend(self.update(self.active, "failed"))
        return events
