"""OpenCode Console 官方配额拉取。

Console 前端（/console）使用 cookie 会话（__Host-console_session，约 30 天有效）
+ x-org-id 工作区头调用 https://opencode.ai/console/api/go/status，返回
Rolling(5h) / Weekly / Monthly 三个窗口的官方计量数据（usedMicroCents /
limitMicroCents / resetsAt）。本模块用保存的凭证代为拉取，供配额页显示
官方精确数字；未配置或凭证失效时由本地复算兜底。

金额单位换算：microcents / 1e7 = 美元。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from .config import store

CONSOLE_ORIGIN = "https://opencode.ai"
STATUS_URL = f"{CONSOLE_ORIGIN}/console/api/go/status"

# 官方面板 30s 轮询一次；代理侧缓存 60s，避免多个前端标签页叠加请求
CACHE_TTL = 60.0

_cache: dict[str, Any] = {"ts": 0.0, "payload": None}


def _parse(data: dict[str, Any]) -> dict[str, Any]:
    """把官方响应整理成前端友好的结构。"""
    access = data.get("access") or {}
    meters = access.get("meters") or {}
    rows = []
    for key, label in (("fiveHour", "Rolling · 5h"), ("week", "Weekly"), ("month", "Monthly")):
        m = meters.get(key)
        if not isinstance(m, dict):
            continue
        limit = int(m.get("limitMicroCents") or 0)
        used = int(m.get("usedMicroCents") or 0)
        pct = round(used / limit * 100, 1) if limit > 0 else 0.0
        rows.append({
            "key": key,
            "label": label,
            "used_usd": used / 1e7,
            "limit_usd": limit / 1e7,
            "pct": pct,
            "resets_at": m.get("resetsAt"),
            "resets_in": max(0, _iso_remaining(m.get("resetsAt"))),
        })
    return {
        "product": data.get("product") or "",
        "payment_kind": data.get("paymentMethodKind") or "",
        "access_starts_at": access.get("startsAt"),
        "access_ends_at": access.get("endsAt"),
        "cancel_at_period_end": bool(access.get("cancelAtPeriodEnd")),
        "paused": access is None,
        "meters": rows,
    }


def _iso_remaining(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.timestamp() - time.time()
    except Exception:  # noqa: BLE001
        return 0.0


async def fetch_status(force: bool = False) -> dict[str, Any]:
    """拉取官方 Go 配额（带缓存）。返回值不含任何凭证。"""
    now = time.time()
    if not force and _cache["payload"] is not None and now - _cache["ts"] < CACHE_TTL:
        return {**_cache["payload"], "cached": True}

    st = store.config.settings
    org = (st.opencode_org_id or "").strip()
    cookie = (st.opencode_console_cookie or "").strip()
    if not org or not cookie:
        payload = {"configured": False, "ok": False, "error": "",
                   "data": None, "fetched_at": now}
        _cache.update(ts=now, payload=payload)
        return payload

    headers = {
        "Accept": "application/json",
        "x-org-id": org,
        # cookie 允许只粘贴会话值，也可以整串粘贴后由这里拼装
        "Cookie": cookie if "__Host-console_session=" in cookie
                  else f"__Host-console_session={cookie}",
        "User-Agent": "Switchboard/1.0",
    }
    try:
        async with httpx.AsyncClient(trust_env=bool(st.use_system_proxy),
                                     timeout=20.0, follow_redirects=True) as cl:
            r = await cl.get(STATUS_URL, headers=headers)
        if r.status_code != 200:
            hint = "凭证可能已过期（约 30 天有效），请到设置页重新复制 cookie"
            if r.status_code in (400, 401, 403):
                try:
                    j = r.json()
                    hint = j.get("message") or hint
                except Exception:  # noqa: BLE001
                    pass
            payload = {"configured": True, "ok": False,
                       "error": f"HTTP {r.status_code}: {hint}",
                       "data": None, "fetched_at": now}
        else:
            payload = {"configured": True, "ok": True, "error": "",
                       "data": _parse(r.json()), "fetched_at": now}
    except Exception as e:  # noqa: BLE001
        payload = {"configured": True, "ok": False,
                   "error": f"{type(e).__name__}: {e}", "data": None, "fetched_at": now}

    _cache.update(ts=now, payload=payload)
    return payload
