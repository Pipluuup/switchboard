"""管理后台 API（/api/*）。"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .config import ClientKey, ModelMapping, Settings, Upstream, store
from .database import db
from .limiter import limiter
from .upstream_pool import pool

router = APIRouter(prefix="/api")


async def require_admin(request: Request, x_admin_token: str = Header(default="")):
    token = store.config.settings.admin_token
    if not token:
        return
    if x_admin_token != token:
        raise HTTPException(status_code=401, detail="管理鉴权失败")


def _merged_upstream(u: Upstream) -> dict[str, Any]:
    d = u.model_dump()
    d.update(u.runtime_state())
    d["api_key_masked"] = (u.api_key[:8] + "*" * 6 + u.api_key[-4:]) if len(u.api_key) > 16 else u.api_key
    return d


# ------------------------------------------------------------------ 概览


@router.get("/overview")
async def overview(_=Depends(require_admin)):
    stats = db.stats_overview()
    ups = store.list_upstreams()
    return {
        "stats": stats,
        "upstream_count": len(ups),
        "enabled_upstream_count": sum(1 for u in ups if u.enabled and u.cooldown_until <= time.time()),
        "mapping_count": len(store.list_mappings()),
        "key_count": len(store.list_keys()),
        "timeline": db.timeline(24),
        "settings": store.config.settings.model_dump(),
    }


# ------------------------------------------------------------------ 用量


@router.get("/usage")
async def usage(_=Depends(require_admin)):
    """滚动窗口用量：5 小时 / 7 天 / 30 天。

    `retention_days` 一并返回，因为日志会被定期清理——当保留天数小于窗口
    长度时，窗口数据是残缺的，前端需要据此提示用户。
    """
    return {
        "windows": db.usage_windows(),
        "retention_days": store.config.settings.log_retention_days,
        "generated_at": time.time(),
    }


@router.get("/opencode-console")
async def opencode_console(force: bool = False, _=Depends(require_admin)):
    """OpenCode Console 官方 Go 配额（见 opencode_console.py；未配置时 ok=False）。"""
    from .opencode_console import fetch_status

    return await fetch_status(force=force)


@router.get("/ark-console")
async def ark_console(force: bool = False, _=Depends(require_admin)):
    """火山方舟编码计划官方配额（见 ark_console.py；未配置时 ok=False）。"""
    from .ark_console import fetch_status

    return await fetch_status(force=force)


# ------------------------------------------------------------------ 上游


@router.get("/upstreams")
async def list_upstreams(_=Depends(require_admin)):
    return [_merged_upstream(u) for u in store.list_upstreams()]


@router.post("/upstreams")
async def create_upstream(data: dict[str, Any], _=Depends(require_admin)):
    data.pop("id", None)
    return _merged_upstream(store.add_upstream(data))


@router.put("/upstreams/{uid}")
async def update_upstream(uid: str, data: dict[str, Any], _=Depends(require_admin)):
    up = store.update_upstream(uid, data)
    if not up:
        raise HTTPException(404, "上游不存在")
    if data.get("api_key") is not None:
        pool.reset_state(uid)
    return _merged_upstream(up)


@router.delete("/upstreams/{uid}")
async def delete_upstream(uid: str, _=Depends(require_admin)):
    if not store.delete_upstream(uid):
        raise HTTPException(404, "上游不存在")
    return {"ok": True}


@router.post("/upstreams/{uid}/test")
async def test_upstream(uid: str, data: dict[str, Any] | None = None, _=Depends(require_admin)):
    up = store.get_upstream(uid)
    if not up:
        raise HTTPException(404, "上游不存在")
    model = (data or {}).get("model") or (up.models[0] if up.models else "")
    if not model:
        model = "claude-sonnet-4-5" if up.protocol == "anthropic" else "gpt-4o-mini"
    res = await pool.test_chat(up, model)
    res["name"] = up.name
    res["model"] = model
    return res


@router.post("/upstreams/{uid}/health")
async def check_upstream(uid: str, _=Depends(require_admin)):
    up = store.get_upstream(uid)
    if not up:
        raise HTTPException(404, "上游不存在")
    return await pool.health_check(up)


@router.post("/upstreams/health-all")
async def check_all(_=Depends(require_admin)):
    return await pool.health_check_all()


@router.post("/upstreams/{uid}/reset")
async def reset_upstream(uid: str, _=Depends(require_admin)):
    pool.reset_state(uid)
    return {"ok": True}


@router.get("/upstreams/{uid}/models")
async def fetch_upstream_models(uid: str, _=Depends(require_admin)):
    up = store.get_upstream(uid)
    if not up:
        raise HTTPException(404, "上游不存在")
    models = await pool.fetch_models(up)
    return {"models": models}


@router.post("/upstreams/probe-models")
async def probe_upstream_models(data: dict[str, Any], _=Depends(require_admin)):
    """按表单里的 base_url/api_key 探测模型清单（新建未保存的上游也能用）。

    api_key 留空且给了 upstream_id 时用已存的 Key（编辑场景不回显）。
    """
    from .config import Upstream

    base_url = (data.get("base_url") or "").strip()
    if not base_url:
        raise HTTPException(400, "base_url 不能为空")
    api_key = (data.get("api_key") or "").strip()
    if not api_key and data.get("upstream_id"):
        stored = store.get_upstream(data["upstream_id"])
        if stored:
            api_key = stored.api_key
    probe = Upstream.model_validate({
        "name": "probe", "protocol": data.get("protocol") or "openai",
        "base_url": base_url, "api_key": api_key,
    })
    models = await pool.fetch_models(probe)
    # 兜底：anthropic 端点（如 api.deepseek.com/anthropic）通常没有 /models，
    # 去掉尾巴（/anthropic 等）用 OpenAI 端再试一次
    if not models and base_url.rstrip("/").count("/") >= 3:
        stem = base_url.rstrip("/")
        alt = stem.rsplit("/", 1)[0]
        if alt != stem:
            probe = Upstream.model_validate({**probe.model_dump(), "base_url": alt})
            models = await pool.fetch_models(probe)
    return {"models": models, "error": "" if models else "未获取到模型，请检查 Base URL / Key / 协议"}


# ------------------------------------------------------------------ 模型映射


@router.get("/mappings")
async def list_mappings(_=Depends(require_admin)):
    ups = {u.id: u.name for u in store.list_upstreams()}
    out = []
    for m in store.list_mappings():
        d = m.model_dump()
        d["upstream_name"] = ups.get(m.upstream_id, "")
        out.append(d)
    return out


@router.post("/mappings")
async def create_mapping(data: dict[str, Any], _=Depends(require_admin)):
    data.pop("id", None)
    return store.add_mapping(data).model_dump()


@router.put("/mappings/{mid}")
async def update_mapping(mid: str, data: dict[str, Any], _=Depends(require_admin)):
    m = store.update_mapping(mid, data)
    if not m:
        raise HTTPException(404, "映射不存在")
    return m.model_dump()


@router.delete("/mappings/{mid}")
async def delete_mapping(mid: str, _=Depends(require_admin)):
    if not store.delete_mapping(mid):
        raise HTTPException(404, "映射不存在")
    return {"ok": True}


# ------------------------------------------------------------------ 客户端 Key


@router.get("/keys")
async def list_keys(_=Depends(require_admin)):
    out = []
    for k in store.list_keys():
        d = k.model_dump()
        d["masked"] = k.masked()
        d["used_today"] = db.today_tokens(k.name)
        out.append(d)
    return out


@router.post("/keys")
async def create_key(data: dict[str, Any], _=Depends(require_admin)):
    data.pop("id", None)
    return store.add_key(data).model_dump()


@router.put("/keys/{kid}")
async def update_key(kid: str, data: dict[str, Any], _=Depends(require_admin)):
    k = store.update_key(kid, data)
    if not k:
        raise HTTPException(404, "Key 不存在")
    return k.model_dump()


@router.delete("/keys/{kid}")
async def delete_key(kid: str, _=Depends(require_admin)):
    if not store.delete_key(kid):
        raise HTTPException(404, "Key 不存在")
    return {"ok": True}


@router.post("/keys/{kid}/regenerate")
async def regenerate_key(kid: str, _=Depends(require_admin)):
    import secrets

    k = store.update_key(kid, {"key": "sk-px-" + secrets.token_hex(16)})
    if not k:
        raise HTTPException(404, "Key 不存在")
    return k.model_dump()


# ------------------------------------------------------------------ 日志


@router.get("/logs")
async def list_logs(limit: int = 100, offset: int = 0, ok: int | None = None,
                    keyword: str = "", _=Depends(require_admin)):
    return {
        "items": db.query(limit=limit, offset=offset, ok=ok, keyword=keyword),
        "total": db.count(ok),
    }


@router.get("/logs/{log_id}")
async def get_log_detail(log_id: int, _=Depends(require_admin)):
    """单条日志完整详情（含请求体 / 响应体）。"""
    row = db.get(log_id)
    if not row:
        raise HTTPException(404, "日志不存在")
    return row


# ------------------------------------------------------------------ 客户端接入


@router.get("/client-setups")
async def list_client_setups(_=Depends(require_admin)):
    """各客户端的当前接入状态 + 已配置的档位。"""
    from . import client_setup

    profiles = [p.model_dump() for p in store.list_profiles()]
    for p in profiles:
        ck = store.get_key(p.get("client_key_id") or "")
        p["client_key_name"] = ck.name if ck else ""
        p["client_key_masked"] = ck.masked() if ck else ""
    return {
        "clients": client_setup.all_status(),
        "profiles": profiles,
        "base_url": client_setup.gateway_base(),
    }


@router.post("/client-setups")
async def create_client_setup(data: dict[str, Any], _=Depends(require_admin)):
    return store.add_profile(data).model_dump()


@router.put("/client-setups/{pid}")
async def update_client_setup(pid: str, data: dict[str, Any], _=Depends(require_admin)):
    p = store.update_profile(pid, data)
    if not p:
        raise HTTPException(404, "档位不存在")
    return p.model_dump()


@router.delete("/client-setups/{pid}")
async def delete_client_setup(pid: str, _=Depends(require_admin)):
    if not store.delete_profile(pid):
        raise HTTPException(404, "档位不存在")
    return {"ok": True}


@router.post("/client-setups/apply")
async def apply_client_setup(data: dict[str, Any], _=Depends(require_admin)):
    """把客户端配置改写为指向本服务（切换前自动备份）。"""
    from . import client_setup

    client = (data.get("client") or "").strip()
    pid = (data.get("profile_id") or "").strip()
    profile = store.get_profile(pid)
    if not profile:
        raise HTTPException(404, "档位不存在")
    try:
        return client_setup.apply_setup(client, profile.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/client-setups/restore")
async def restore_client_setup(data: dict[str, Any], _=Depends(require_admin)):
    """从最近一次备份还原客户端配置。"""
    from . import client_setup

    client = (data.get("client") or "").strip()
    try:
        return client_setup.restore_setup(client)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/rtk/status")
async def rtk_status(_=Depends(require_admin)):
    """检测本机是否安装 RTK（Rust Token Killer，客户端命令输出压缩工具）。

    PATH 找不到时兜底扫 winget 的安装目录——服务进程可能是在安装前启动的，
    继承的还是旧 PATH。
    """
    import glob
    import os
    import shutil
    import subprocess

    path = shutil.which("rtk")
    if not path:
        pattern = os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages", "rtk-ai.rtk*", "rtk.exe")
        hits = sorted(glob.glob(pattern))
        if hits:
            path = hits[-1]
    if not path:
        return {"installed": False, "version": ""}
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        out = (r.stdout or r.stderr or "").strip()
        ver = out.splitlines()[-1].strip() if out else ""
        return {"installed": True, "version": ver, "path": path}
    except Exception as e:  # noqa: BLE001
        return {"installed": True, "version": "", "path": path, "error": str(e)[:200]}


@router.post("/caveman/preview")
async def caveman_preview(data: dict[str, Any], _=Depends(require_admin)):
    """文本验证：对传入的 messages 执行一次压缩并返回压缩后的实际内容。

    不转发、不入库——纯粹让用户肉眼看到「模型实际会收到什么」。
    入参：{"messages": [{role, content}, ...]}，content 支持 str 或 block 列表。
    """
    from . import caveman

    msgs = data.get("messages")
    if not isinstance(msgs, list) or not msgs:
        raise HTTPException(400, "messages 不能为空")
    body = {"model": "preview", "messages": json.loads(json.dumps(msgs, ensure_ascii=False))}
    saved = caveman.compress_body(body)
    before = json.dumps(msgs, ensure_ascii=False)
    after = json.dumps(body["messages"], ensure_ascii=False)
    return {"saved_chars": saved, "before_chars": len(before), "after_chars": len(after),
            "messages": body["messages"]}


@router.get("/extension/download")
async def download_extension():
    """打包浏览器扩展为 zip 供下载（extension/ 随 exe 携带，源码模式直接读仓库目录）。"""
    import io
    import zipfile

    from .paths import resource_dir

    ext_dir = os.path.join(resource_dir(), "extension")
    if not os.path.isdir(ext_dir):
        raise HTTPException(404, "扩展目录不存在（请重新打包以携带 extension/）")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(os.listdir(ext_dir)):
            p = os.path.join(ext_dir, name)
            if os.path.isfile(p):
                zf.write(p, arcname=f"switchboard-cookie-helper/{name}")
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="switchboard-cookie-helper.zip"'},
    )


@router.delete("/logs")
async def clear_logs(_=Depends(require_admin)):
    n = db.clear_all()
    return {"ok": True, "deleted": n}


# ------------------------------------------------------------------ 设置


@router.get("/settings")
async def get_settings(_=Depends(require_admin)):
    return store.config.settings.model_dump()


@router.put("/settings")
async def update_settings(data: dict[str, Any], _=Depends(require_admin)):
    # host/port 不在这里受理：改监听地址必须走 /api/server/restart（绑定预检 +
    # 进程交接），否则会出现「配置已写、进程未跟上」的半生效状态。
    data = {k: v for k, v in data.items() if k not in ("host", "port")}
    # 运行日志：记录实际变更的键（扩展写 Cookie 等管理动作由此可追溯）
    changed = {k: v for k, v in data.items() if getattr(store.config.settings, k, None) != v}
    if changed:
        def _mask(k: str, v: Any) -> Any:
            if isinstance(v, str) and any(s in k for s in ("cookie", "token", "key", "password")):
                return f"<{len(v)} 字符>"
            return v
        shown = ", ".join(f"{k}={_mask(k, v)}" for k, v in changed.items())
        print(f"[settings] {time.strftime('%H:%M:%S')} 更新: {shown}")
    return store.update_settings(data).model_dump()


@router.get("/config/export")
async def export_config(_=Depends(require_admin)):
    return JSONResponse(store.config.model_dump(exclude={
        "upstreams": {"__all__": {
            "healthy", "consecutive_failures", "cooldown_until", "last_error",
            "last_latency_ms", "total_requests", "failed_requests",
        }},
    }))


@router.post("/config/import")
async def import_config(payload: dict[str, Any], _=Depends(require_admin)):
    """整体导入配置（会覆盖现有上游/映射/Key）。"""
    try:
        ups = [Upstream.model_validate(x) for x in payload.get("upstreams", [])]
        maps = [ModelMapping.model_validate(x) for x in payload.get("mappings", [])]
        keys = [ClientKey.model_validate(x) for x in payload.get("client_keys", [])]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"配置格式错误：{e}")
    store.config.upstreams = ups
    store.config.mappings = maps
    store.config.client_keys = keys
    if payload.get("settings"):
        for k, v in payload["settings"].items():
            if k in Settings.model_fields and k not in ("host", "port"):
                setattr(store.config.settings, k, v)
    store.save()
    pool.reset_state()
    return {"ok": True, "upstreams": len(ups), "mappings": len(maps), "keys": len(keys)}


@router.get("/quickstart")
async def quickstart(request: Request):
    """给前端展示接入示例（含当前监听端口与首个可用 Key）。"""
    st = store.config.settings
    keys = [k for k in store.list_keys() if k.enabled]
    key = keys[0].key if keys else "sk-px-xxx（请先在客户端 Key 页面创建）"
    base = f"http://127.0.0.1:{st.port}/v1"
    return {
        "base_url": base,
        "api_key": key,
        "snippets": {
            "env": f'export ANTHROPIC_BASE_URL="{base}"\nexport ANTHROPIC_API_KEY="{key}"',
            "powershell": f'$env:ANTHROPIC_BASE_URL="{base}"\n$env:ANTHROPIC_API_KEY="{key}"',
            "ccswitch": json.dumps({
                "providers": {
                    "switchboard": {
                        "name": "Switchboard",
                        "api_base": base,
                        "api_key": key,
                        "model": store.list_mappings()[0].alias if store.list_mappings() else "claude-sonnet-4-5",
                    }
                }
            }, indent=2, ensure_ascii=False),
        },
    }


# ------------------------------------------------------------------ 服务管理（改监听地址即生效）


def _norm_host(host: str) -> str:
    return (host or "").strip().lower() or "127.0.0.1"


def _bind_error(host: str, port: int) -> str:
    """试绑一次目标地址。返回空串=可用，否则=错误说明。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
        return ""
    except OSError as e:
        return str(e)


def _spawn_replacement(host: str, port: int):
    """拉起接管监听的新进程：脱离当前进程树（本进程退出后它继续活着）。

    stdout/stderr 追加写入 data/server.log，替代无人可看的 detached 控制台。
    """
    from .paths import data_dir, is_frozen, source_root

    if is_frozen():
        cmd = [sys.executable, "--host", host, "--port", str(port), "--no-browser"]
        cwd = os.path.dirname(os.path.abspath(sys.executable))
    else:
        cmd = [sys.executable, os.path.join(source_root(), "run.py"),
               "--host", host, "--port", str(port), "--no-browser"]
        cwd = source_root()
    extra: dict[str, Any] = {}
    if os.name == "nt":
        extra["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        extra["start_new_session"] = True
    try:
        log = open(os.path.join(data_dir(), "server.log"), "ab")
        return subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=log,
                                stdin=subprocess.DEVNULL, **extra)
    except OSError as e:
        print(f"[restart] 拉起新进程失败: {e}")
        return None


async def _wait_healthy(host: str, port: int, timeout_s: float) -> tuple[bool, str]:
    """轮询新进程的 /healthz。trust_env=False：绝不能让系统代理劫持本机探测。"""
    probe = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    url = f"http://{probe}:{port}/healthz"
    deadline = time.monotonic() + timeout_s
    last = ""
    async with httpx.AsyncClient(trust_env=False, timeout=2.0) as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(url)
                if r.status_code < 500:
                    return True, ""
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
            await asyncio.sleep(0.4)
    return False, last


@router.post("/server/restart")
async def restart_server(data: dict[str, Any] | None = None, _=Depends(require_admin)):
    """按指定（或缺省为配置中的）监听地址重启服务。

    流程：绑定预检 -> 保存地址 -> 拉起脱离进程树的新进程 -> 轮询其 /healthz ->
    就绪后本进程退出、完成交接。任何一步失败都保持旧服务继续运行，
    并把配置回滚到旧地址，绝不出现「配置指向新端口而没有任何服务在跑」。
    """
    from .main import runtime_addr

    data = data or {}
    try:
        port = int(data.get("port") or store.config.settings.port)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="端口必须是 1-65535 的整数")
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=400, detail="端口必须在 1-65535 之间")
    host = _norm_host(str(data.get("host") or store.config.settings.host))

    cur_host = _norm_host(str(runtime_addr.get("host")))
    cur_port = int(runtime_addr.get("port") or 0)
    base_url = f"http://{host}:{port}"

    if (host, port) == (cur_host, cur_port):
        return {"ok": True, "restarted": False, "base_url": base_url,
                "message": "监听地址未变化，无需重启"}

    # 旧进程退出有 ~1 秒宽限（call_later + os._exit），Windows 释放端口还会再
    # 延迟片刻：对目标地址做最多 ~4 秒试绑重试，持续被占才判定失败。
    err = ""
    for _ in range(8):
        err = _bind_error(host, port)
        if not err:
            break
        await asyncio.sleep(0.5)
    if err:
        raise HTTPException(
            status_code=400,
            detail=(f"{host}:{port} 无法绑定：{err}。"
                    "若端口被占用请换一个；若只改监听地址不改端口，请先停止服务再启动。"),
        )

    store.update_settings({"host": host, "port": port})

    if _spawn_replacement(host, port) is None:
        store.update_settings({"host": cur_host, "port": cur_port})
        raise HTTPException(status_code=500, detail="无法拉起新的服务进程，请查看 data/server.log")

    ok, detail = await _wait_healthy(host, port, 30)
    if not ok:
        store.update_settings({"host": cur_host, "port": cur_port})
        raise HTTPException(
            status_code=502,
            detail=f"新进程未在 30 秒内就绪（{detail}）。旧服务仍在运行，请查看 data/server.log",
        )

    # 留 1 秒让本响应送达客户端，然后旧进程退出，端口交接完成
    asyncio.get_running_loop().call_later(1.0, lambda: os._exit(0))
    return {"ok": True, "restarted": True, "base_url": base_url,
            "message": "新进程已接管监听，旧进程即将退出"}
