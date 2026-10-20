"""桌面 GUI：把 Web 控制台嵌进独立的原生窗口（WebView2 / pywebview）。

设计要点：
- 窗口只是「壳」：内容仍是本地 FastAPI 服务提供的 React 控制台，
  所以 Web 与桌面两种形态共用同一套后端与前端，不存在两套逻辑。
- 服务跑在 uvicorn 的后台线程里，GUI 线程只负责开窗与事件循环。
- pywebview 缺失时优雅降级（提示改用浏览器），不因缺依赖而崩溃。
"""
from __future__ import annotations

import threading
import time
import urllib.request
from typing import Any


def _probe(url: str, timeout: float = 1.0) -> bool:
    """探测服务是否已就绪。"""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as r:
            return r.status < 500
    except Exception:  # noqa: BLE001
        return False


def wait_ready(url: str, timeout_s: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _probe(url):
            return True
        time.sleep(0.25)
    return False


def available() -> bool:
    """pywebview 是否可用（动态导入，网页版打包时排除该依赖）。"""
    import importlib  # noqa: PLC0415

    try:
        importlib.import_module("webview")
    except Exception:  # noqa: BLE001
        return False
    return True


def open_console(url: str, title: str = "Switchboard", width: int = 1280, height: int = 860) -> int:
    """打开桌面窗口并进入事件循环（阻塞直到窗口关闭）。返回进程退出码。"""
    # 动态导入：让 PyInstaller 静态分析抓不到，网页版才能排除 pywebview 依赖
    import importlib  # noqa: PLC0415

    webview = importlib.import_module("webview")

    webview.create_window(
        title,
        url,
        width=width,
        height=height,
        min_size=(960, 640),
        text_select=True,
    )
    webview.start(debug=False)
    return 0


def open_console_async(url: str, title: str = "Switchboard",
                      width: int = 1280, height: int = 860) -> threading.Thread:
    """在后台线程里开窗（供主进程继续跑服务）。"""
    t = threading.Thread(
        target=open_console, args=(url, title, width, height),
        name="switchboard-gui", daemon=True,
    )
    t.start()
    return t
