"""Switchboard 启动脚本。

用法：
    python run.py                      # 按配置启动（默认开桌面窗口）
    python run.py --port 9000          # 覆盖端口
    python run.py --host 0.0.0.0       # 允许局域网访问（请同时设置管理口令）
    python run.py --no-browser         # 不自动打开浏览器
    python run.py --no-gui             # 不开桌面窗口（纯后台）
    python run.py --browser            # 强制用浏览器打开（不开桌面窗口）

打包成 exe 后同样支持以上参数：Switchboard.exe --port 9000
"""
from __future__ import annotations

import argparse
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _safe_stdout():
    """打包为控制台程序时，避免非 UTF-8 代码页导致中文输出崩溃。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def _probe_running(host: str, port: int) -> bool | None:
    """探测端口状态。None=空闲；True=本代理实例已在运行；False=被其他程序占用。"""
    import json
    import socket
    import urllib.request

    probe_host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        if s.connect_ex((probe_host, port)) != 0:
            return None
    try:
        # 绕过系统代理（127.0.0.1 常被 Clash 等劫持）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://{probe_host}:{port}/healthz", timeout=3) as r:
            return bool(json.load(r).get("ok"))
    except Exception:  # noqa: BLE001
        return False


def _minimize_console() -> None:
    """exe 启动后把控制台窗口自动最小化到任务栏（点开仍可看运行日志）。
    仅对打包版生效，源码 python run.py 运行时保持前台。"""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes  # noqa: PLC0415
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 6)  # 6 = SW_MINIMIZE
    except Exception:  # noqa: BLE001
        pass


def main():
    _safe_stdout()
    _minimize_console()

    parser = argparse.ArgumentParser(description="Switchboard")
    parser.add_argument("--host", default=None, help="监听地址")
    parser.add_argument("--port", type=int, default=None, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    parser.add_argument("--no-gui", action="store_true", help="不开桌面窗口（纯后台运行）")
    parser.add_argument("--browser", action="store_true", help="用浏览器打开（不开桌面窗口）")
    args = parser.parse_args()

    from backend.config import store  # noqa: PLC0415
    from backend.paths import data_dir, is_frozen, web_dir  # noqa: PLC0415

    st = store.config.settings
    if args.host:
        st.host = args.host
    if args.port:
        st.port = args.port

    # 桌面窗口优先：默认开 GUI（不开浏览器）；--browser 强制浏览器；--no-gui 纯后台
    from backend import gui  # noqa: PLC0415

    # 网页版 exe（Switchboard-Web.exe）不带 GUI 依赖，靠文件名识别为纯浏览器形态
    is_web_build = bool(os.environ.get("SWITCHBOARD_WEB_BUILD")) or (
        getattr(sys, "frozen", False)
        and os.path.basename(sys.executable).lower().startswith("switchboard-web")
    )

    use_gui = not args.no_gui and not args.browser and not is_web_build
    if use_gui and not gui.available():
        use_gui = False
        print("  [提示] 未安装 pywebview，改用浏览器打开（pip install pywebview 可启用桌面窗口）")
    if use_gui:
        st.auto_open_browser = False   # 桌面窗口取代浏览器
    elif is_web_build:
        st.auto_open_browser = True    # 网页版始终自动开浏览器控制台
    elif args.no_browser:
        st.auto_open_browser = False

    # 端口已有实例在跑：直接弹网页退出，不二次绑定（exe 双击场景的核心路径）
    state = _probe_running(st.host, st.port)
    if state is True:
        url = f"http://{'127.0.0.1' if st.host in ('', '0.0.0.0', '::') else st.host}:{st.port}/"
        print(f"  检测到服务已在运行：{url}")
        if use_gui:
            # 已有实例：直接把它开成桌面窗口（不再二次绑定端口）
            print("  正在打开桌面窗口…")
            gui.open_console(url)
            return 0
        if not args.no_browser and st.auto_open_browser:
            import webbrowser  # noqa: PLC0415
            webbrowser.open(url)
        return 0
    if state is False:
        print(f"  [错误] 端口 {st.port} 已被其他程序占用。")
        print("  可换端口启动：Switchboard.exe --port 9000")
        return 1

    # 传对象而非 "backend.main:app" 字符串：打包后导入字符串解析更脆弱
    from backend.main import app  # noqa: PLC0415

    import uvicorn  # noqa: PLC0415

    print("=" * 58)
    print("  Switchboard" + ("   [exe]" if is_frozen() else ""))
    print("=" * 58)
    print(f"  管理界面 : http://{st.host}:{st.port}")
    print(f"  代理端点 : http://{st.host}:{st.port}/v1/messages")
    print(f"            http://{st.host}:{st.port}/v1/chat/completions")
    print(f"  上游数量 : {len(store.config.upstreams)}")
    print(f"  数据目录 : {data_dir()}")
    print(f"  资源目录 : {web_dir()}")
    if not st.admin_token:
        print("  [提示] 未设置管理口令，仅建议在本机 127.0.0.1 使用")
    print("=" * 58)

    host = st.host if st.host not in ("", "0.0.0.0", "::") else "127.0.0.1"
    ui_url = f"http://{host}:{st.port}/"

    if not use_gui:
        uvicorn.run(app, host=st.host, port=st.port, log_level="info")
        return 0

    # 桌面模式：uvicorn 放后台线程，GUI 线程跑窗口事件循环
    server = uvicorn.Server(uvicorn.Config(app, host=st.host, port=st.port, log_level="info"))
    t = threading.Thread(target=server.run, name="switchboard-server", daemon=True)
    t.start()
    if not gui.wait_ready(ui_url, timeout_s=25.0):
        print(f"  [错误] 服务未能在 25 秒内就绪，放弃打开窗口")
        return 1
    print("  正在打开桌面窗口…")
    try:
        gui.open_console(ui_url)
    except Exception as e:  # noqa: BLE001
        print(f"  [错误] 打开窗口失败：{e}")
        import webbrowser  # noqa: PLC0415
        webbrowser.open(ui_url)
        return 0
    # 窗口关闭 = 退出整个程序（服务随之停止）
    server.should_exit = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
