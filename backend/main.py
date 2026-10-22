from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import webbrowser
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .admin_routes import router as admin_router
from .config import store
from .database import db
from .paths import web_dir
from .proxy_routes import router as proxy_router
from .upstream_pool import pool

WEB_DIR = web_dir()

# 本进程实际监听的地址：仅在 lifespan 启动时捕获一次。
# 之后用户在设置页改 host/port 不会影响它 —— admin_routes 的重启流程据此
# 判断「地址是否真的变了」、并在交接失败时回滚到这个仍在运行的地址。
runtime_addr: dict[str, str | int] = {"host": "127.0.0.1", "port": 8000}


async def _health_loop():
    """后台周期性健康检查 + 日志清理。"""
    while True:
        try:
            st = store.config.settings
            if st.health_check_enabled:
                for up in store.list_upstreams():
                    if up.enabled:
                        await pool.health_check(up)
            if st.log_retention_days > 0:
                db.cleanup(st.log_retention_days)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(max(30, store.config.settings.health_check_interval))


@asynccontextmanager
async def lifespan(app: FastAPI):
    st = store.config.settings
    runtime_addr.update(host=(st.host or "127.0.0.1").strip().lower(), port=int(st.port))
    task = asyncio.create_task(_health_loop())
    if st.auto_open_browser:
        asyncio.create_task(_open_browser_later())
    yield
    task.cancel()
    with contextlib.suppress(Exception):
        await task
    with contextlib.suppress(Exception):
        await pool.aclose()


async def _open_browser_later():
    await asyncio.sleep(1.2)
    try:
        webbrowser.open(f"http://{store.config.settings.host}:{store.config.settings.port}")
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(
    title="Switchboard",
    description="Anthropic / OpenAI 中转网关，带 Web 管理界面",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(proxy_router)
app.include_router(admin_router)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": int(time.time())}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


class _RobustStaticFiles(StaticFiles):
    """StaticFiles 加固：畸形 URL（含 * ? | 等非法文件名字符）会让 Windows 的
    os.stat 抛 WinError 123，2026-10-21 曾把整个服务进程带崩。这里兜底为 404，
    垃圾路径只影响那一个请求，不再影响进程。

    两种异常都要捕：
    - OSError：Windows 的非法文件名字符（WinError 123）等
    - ValueError：路径里含 NUL 字节时 os.path.realpath 抛的
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except (OSError, ValueError):
            from starlette.exceptions import HTTPException as _HTTPException
            raise _HTTPException(status_code=404)


app.mount("/", _RobustStaticFiles(directory=WEB_DIR, html=True), name="web")
