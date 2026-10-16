"""把 Switchboard 打包为 Windows exe，输出到 release/ 目录。

用法：
    python scripts/build_exe.py                # onefile（单文件，默认）
    python scripts/build_exe.py --mode onedir  # 单目录（启动更快，便于排查）
    python scripts/build_exe.py --no-clean     # 复用上次构建缓存，速度更快

onefile 适合分发（只有一个 exe）；onedir 启动更快（不用每次解压到临时目录）。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE = os.path.join(ROOT, "release")
NAME = "Switchboard"

# uvicorn 用 importlib 动态加载这些实现，静态分析抓不到，需显式声明。
# 只对环境中真实存在的模块声明，缺失的会被 PyInstaller 判为错误。
UVICORN_OPTIONAL = [
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.loops.asyncio",
    "uvicorn.loops.auto",
    "uvicorn.loops.uvloop",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.supervisors.basereload",
    "uvicorn.supervisors.statreload",
    "uvicorn.supervisors.watchfilesreload",
]


def exists(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:  # noqa: BLE001
        return False


def build_frontend() -> bool:
    """若存在 frontend/ 则先构建前端（产物输出到 web/），供打包携带。"""
    fe = os.path.join(ROOT, "frontend")
    if not os.path.isdir(os.path.join(fe, "src")):
        return True  # 无前端源码，视为已预构建
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("  错误：检测到 frontend/ 但未安装 Node.js（需要 ≥ 18）。\n"
              "  请安装 Node 后重试，或先手动执行 cd frontend && npm run build")
        return False
    print("  构建前端（npm run build）…")
    npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    r = subprocess.run([npm, "run", "build"], cwd=fe, shell=os.name == "nt")
    if r.returncode != 0:
        print("  前端构建失败，中止打包")
        return False
    return True


def build(mode: str, clean: bool) -> int:
    if not os.path.isfile(os.path.join(ROOT, "run.py")):
        print("  错误：未在项目根目录找到 run.py")
        return 1
    if not build_frontend():
        return 1
    try:
        import PyInstaller  # noqa: F401,PLC0415
    except ImportError:
        print("  未安装 PyInstaller，正在安装…")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("  安装失败：" + ((r.stdout or "") + (r.stderr or ""))[-500:])
            return 1

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile" if mode == "onefile" else "--onedir",
        "--console",                       # 保留控制台：能直接看到运行日志
        "--name", NAME,
        "--distpath", RELEASE,
        "--workpath", os.path.join(ROOT, "build", "pyinstaller"),
        "--specpath", os.path.join(ROOT, "build"),
        "--add-data", os.path.join(ROOT, "web") + os.pathsep + "web",
        "--add-data", os.path.join(ROOT, "extension") + os.pathsep + "extension",
        "--collect-submodules", "backend",
        "--collect-submodules", "anyio",
        "--exclude-module", "pytest",
        "--exclude-module", "tkinter",
    ]
    if clean:
        cmd.append("--clean")
    for m in UVICORN_OPTIONAL:
        if exists(m):
            cmd += ["--hidden-import", m]
    cmd.append(os.path.join(ROOT, "run.py"))

    print(f"  模式：{mode}（{'单文件' if mode == 'onefile' else '单目录'}）")
    print("  开始打包，首次构建需要 1~3 分钟…\n")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print(f"\n  打包失败（exit {r.returncode}）")
        return r.returncode

    exe = os.path.join(RELEASE, NAME + ".exe")
    if mode == "onefile":
        ok = os.path.isfile(exe)
    else:
        ok = os.path.isfile(os.path.join(RELEASE, NAME, NAME + ".exe"))
    if not ok:
        print("\n  打包结束但未找到预期的 exe")
        return 1

    size = os.path.getsize(exe) if mode == "onefile" else _dir_size(os.path.join(RELEASE, NAME))
    print(f"\n  打包完成，用时 {time.time() - t0:.0f}s")
    if mode == "onefile":
        print(f"  产物：{exe}")
    else:
        print(f"  产物：{os.path.join(RELEASE, NAME, NAME + '.exe')}")
    print(f"  体积：{size / 1024 / 1024:.1f} MB")
    return 0


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def clean_artifacts() -> None:
    shutil.rmtree(os.path.join(ROOT, "build"), ignore_errors=True)
    print("  已清理 build/ 中间产物")


if __name__ == "__main__":
    # 非 UTF-8 代码页（如英文 Windows / CI）下打印中文横幅会崩
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    p = argparse.ArgumentParser(description="打包 Switchboard 为 exe")
    p.add_argument("--mode", choices=["onefile", "onedir"], default="onefile")
    p.add_argument("--no-clean", action="store_true", help="跳过 --clean，复用构建缓存")
    p.add_argument("--purge", action="store_true", help="仅清理 build/ 中间产物")
    a = p.parse_args()

    print("=" * 58)
    print("  Switchboard —— 打包 exe")
    print("=" * 58)
    if a.purge:
        clean_artifacts()
        sys.exit(0)
    sys.exit(build(a.mode, clean=not a.no_clean))
