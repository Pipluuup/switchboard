"""服务管理：状态查看、停止、开机自启的安装与卸载。

用法：
    python scripts/service.py              # 交互式菜单
    python scripts/service.py status       # 查看运行状态
    python scripts/service.py stop         # 停止服务
    python scripts/service.py autostart on    # 设为开机自启
    python scripts/service.py autostart off   # 取消开机自启
    python scripts/service.py autostart       # 查看自启状态
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "data", "config.json")
VBS_PATH = os.path.join(ROOT, "start-hidden.vbs")
TASK_NAME = "Switchboard"

# 查询本地服务时必须绕过系统代理，否则会被 HTTP_PROXY 拦截
_opener = build_opener(ProxyHandler({}))


def get_port() -> int:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return int(json.load(f).get("settings", {}).get("port", 8000))
    except Exception:
        return 8000


def find_pids(port: int) -> list[str]:
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
    except Exception:
        return []
    pids = []
    for line in r.stdout.splitlines():
        if "LISTENING" in line and f":{port} " in line:
            parts = line.split()
            if parts and parts[-1].isdigit() and parts[-1] not in pids:
                pids.append(parts[-1])
    return pids


def is_running(port: int) -> bool:
    try:
        with _opener.open(f"http://127.0.0.1:{port}/healthz", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def cmd_status(port: int) -> None:
    if is_running(port):
        print(f"  状态：运行中   http://127.0.0.1:{port}")
    else:
        pids = find_pids(port)
        if pids:
            print(f"  状态：端口 {port} 被占用（PID {','.join(pids)}）但服务无响应")
        else:
            print("  状态：未运行")
    print(f"  自启：{autostart_label()}")


def cmd_stop(port: int) -> None:
    pids = find_pids(port)
    if not pids:
        print(f"  端口 {port} 上没有运行中的服务")
        return
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
        print(f"  已停止进程 PID {pid}")
    time.sleep(1)
    print("  服务已停止" if not is_running(port) else "  仍在运行，请手动检查")


def _schtasks(args: list[str]):
    """执行 schtasks。某些环境（沙箱、受限账户）可能禁用该命令，返回 None 表示不可用。"""
    try:
        return subprocess.run(["schtasks"] + args, capture_output=True, text=True,
                              encoding="utf-8", errors="ignore")
    except (PermissionError, FileNotFoundError, OSError):
        return None


def autostart_enabled() -> bool | None:
    """返回 True/False，无法查询时返回 None。"""
    r = _schtasks(["/query", "/tn", TASK_NAME])
    if r is None:
        return None
    return r.returncode == 0


def autostart_label() -> str:
    v = autostart_enabled()
    return "未知（当前环境无法查询计划任务）" if v is None else ("已开启" if v else "未开启")


def cmd_autostart(arg: str) -> None:
    if arg == "on":
        if not os.path.exists(VBS_PATH):
            print(f"  缺少启动脚本：{VBS_PATH}")
            return
        r = _schtasks(["/create", "/tn", TASK_NAME, "/tr",
                       f'wscript.exe "{VBS_PATH}"', "/sc", "onlogon", "/f"])
        if r is None:
            print("  当前环境不允许执行 schtasks，请手动添加开机启动项：")
            print(f"  把 start-hidden.vbs 的快捷方式放入 shell:startup 目录")
            return
        if r.returncode == 0:
            print("  已设为开机自启：登录后自动在后台启动，无窗口")
        else:
            print("  设置失败：" + ((r.stdout or "") + (r.stderr or "")).strip()[:300])
    elif arg == "off":
        cur = autostart_enabled()
        if cur is False:
            print("  当前未开启开机自启")
            return
        r = _schtasks(["/delete", "/tn", TASK_NAME, "/f"])
        if r is None:
            print("  当前环境不允许执行 schtasks，无法自动取消")
            return
        print("  已取消开机自启" if r.returncode == 0
              else "  取消失败：" + ((r.stdout or "") + (r.stderr or "")).strip()[:300])
    else:
        print(f"  开机自启：{autostart_label()}")
        print(f"  计划任务名：{TASK_NAME}")


def menu(port: int) -> None:
    while True:
        print()
        print("  ============ Switchboard 服务管理 ============")
        print(f"  端口 {port}   状态：{'运行中' if is_running(port) else '未运行'}"
              f"   开机自启：{autostart_label()}")
        print("  ---------------------------------------------------")
        print("   1  查看状态")
        print("   2  停止服务")
        print("   3  设为开机自启（后台无窗口）")
        print("   4  取消开机自启")
        print("   0  退出")
        print("  ---------------------------------------------------")
        choice = input("  请选择 [0-4]: ").strip()
        if choice == "1":
            cmd_status(get_port())
        elif choice == "2":
            cmd_stop(get_port())
        elif choice == "3":
            cmd_autostart("on")
        elif choice == "4":
            cmd_autostart("off")
        elif choice in ("0", "", "q"):
            break
        else:
            print("  无效选项")


def main() -> int:
    port = get_port()
    args = sys.argv[1:]
    if not args:
        try:
            menu(port)
        except (EOFError, KeyboardInterrupt):
            print()
        return 0
    cmd = args[0].lower()
    if cmd == "status":
        cmd_status(port)
    elif cmd == "stop":
        cmd_stop(port)
    elif cmd == "autostart":
        cmd_autostart(args[1].lower() if len(args) > 1 else "")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
