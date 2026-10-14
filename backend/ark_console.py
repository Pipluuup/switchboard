"""火山方舟（Volcano Ark）编码计划官方配额拉取。

方舟控制台（console.volcengine.com/ark → 订阅管理）有两类套餐，各自有用量接口：

1. Agent Plan（AFP 点数计量）
   POST /api/top/ark/cn-beijing/2024-01-01/GetAgentPlanAFPUsage? body {}
   → Result.PlanType + AFP{FiveHour,Daily,Weekly,Monthly}{Quota,Used,ResetTime}
2. Coding Plan（百分比计量，即编码套餐）
   POST /api/top/ark/cn-beijing/2024-01-01/GetCodingPlanUsage? body {}
   → Result.QuotaUsage[]: {Level: session/weekly/monthly, Percent, Cap,
     ResetTimestamp(秒)}

鉴权 = 控制台会话 Cookie 整串 + X-Csrf-Token（值等于 csrfToken cookie，
double-submit），无时效签名。Cookie 从浏览器整体复制（F12 → Network →
任意 api/top/ark 请求 → Request Headers → Cookie），会话失效后重新复制。
"""
from __future__ import annotations

import re
import time
from typing import Any

import httpx

from .config import store

_API = "https://console.volcengine.com/api/top/ark/cn-beijing/2024-01-01"
AGENT_URL = f"{_API}/GetAgentPlanAFPUsage?"
CODING_URL = f"{_API}/GetCodingPlanUsage?"

# 官方面板切页即刷新；代理侧缓存 60s
CACHE_TTL = 60.0

_cache: dict[str, Any] = {"ts": 0.0, "payload": None}

# Agent Plan 窗口字段 → 展示名
_AGENT_WINDOWS = [
    ("AFPFiveHour", "Rolling · 5h"),
    ("AFPDaily", "Daily"),
    ("AFPWeekly", "Weekly"),
    ("AFPMonthly", "Monthly"),
]

# Coding Plan 窗口顺序
_CODING_LEVELS = ["session", "weekly", "monthly"]
_CODING_LABELS = {"session": "Session", "weekly": "Weekly", "monthly": "Monthly"}


def _csrf_from_cookie(cookie: str) -> str:
    m = re.search(r"(?:^|;\s*)csrfToken=([^;]+)", cookie)
    return m.group(1) if m else ""


def _headers(cookie: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Cookie": cookie,
        "X-Csrf-Token": _csrf_from_cookie(cookie),
        "User-Agent": "Switchboard/1.0",
        "Referer": "https://console.volcengine.com/ark/region:cn-beijing/subscription/coding-plan",
        "Origin": "https://console.volcengine.com",
    }


async def _post(cl: httpx.AsyncClient, url: str, cookie: str, body: dict) -> dict:
    r = await cl.post(url, headers=_headers(cookie), json=body)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: 会话可能已失效，请到设置页重新复制 Cookie")
    data = r.json()
    err = (data.get("ResponseMetadata") or {}).get("Error")
    if err:
        raise RuntimeError(f"{err.get('Code')}: 会话可能已失效，请重新复制 Cookie")
    return data.get("Result") or {}


def _parse_agent(res: dict) -> dict:
    meters = []
    for field, label in _AGENT_WINDOWS:
        w = res.get(field)
        if not isinstance(w, dict):
            continue
        quota = int(w.get("Quota") or 0)
        used = int(w.get("Used") or 0)
        reset_ms = int(w.get("ResetTime") or -1)
        meters.append({
            "key": field,
            "label": label,
            "used": used,
            "quota": quota,
            "pct": round(used / quota * 100, 1) if quota > 0 else 0.0,
            "resets_in": max(0.0, reset_ms / 1000.0 - time.time()) if reset_ms > 0 else 0.0,
        })
    return {"plan_type": res.get("PlanType") or "", "meters": meters}


def _parse_coding(res: dict) -> dict:
    by_level = {q.get("Level"): q for q in res.get("QuotaUsage") or [] if isinstance(q, dict)}
    meters = []
    for level in _CODING_LEVELS:
        q = by_level.get(level)
        if not q:
            continue
        reset_s = int(q.get("ResetTimestamp") or 0)
        meters.append({
            "key": level,
            "label": _CODING_LABELS[level],
            "pct": round(float(q.get("Percent") or 0.0), 1),
            "cap": q.get("Cap"),
            "resets_in": max(0.0, float(reset_s - time.time())) if reset_s > 0 else 0.0,
        })
    return {"status": res.get("Status") or "", "meters": meters}


async def fetch_status(force: bool = False) -> dict[str, Any]:
    """拉取方舟两类套餐的官方配额（带缓存）。返回值不含任何凭证。"""
    now = time.time()
    if not force and _cache["payload"] is not None and now - _cache["ts"] < CACHE_TTL:
        return {**_cache["payload"], "cached": True}

    cookie = (store.config.settings.ark_console_cookie or "").strip()
    if not cookie:
        payload = {"configured": False, "ok": False, "error": "",
                   "agent": None, "coding": None, "fetched_at": now}
        _cache.update(ts=now, payload=payload)
        return payload

    agent: dict[str, Any] | None = None
    coding: dict[str, Any] | None = None
    error = ""
    try:
        async with httpx.AsyncClient(trust_env=bool(store.config.settings.use_system_proxy),
                                     timeout=20.0, follow_redirects=True) as cl:
            try:
                agent = _parse_agent(await _post(cl, AGENT_URL, cookie, {}))
            except Exception as e:  # noqa: BLE001
                error = f"Agent Plan: {e}"
            try:
                coding = _parse_coding(await _post(cl, CODING_URL, cookie, {}))
            except Exception as e:  # noqa: BLE001
                error = (error + "；" if error else "") + f"Coding Plan: {e}"
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    ok = agent is not None or coding is not None
    payload = {"configured": True, "ok": ok, "error": error,
               "agent": agent, "coding": coding, "fetched_at": now}
    _cache.update(ts=now, payload=payload)
    return payload
