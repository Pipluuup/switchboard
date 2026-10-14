"""运行时路径解析：兼容源码运行与 PyInstaller 打包（frozen）。

打包后 ``__file__`` 指向临时解压目录（sys._MEIPASS），直接基于它推算路径
会导致：配置文件重启即丢失、静态资源找不到。这里集中处理：

- 只读资源（web/）：frozen 时取 sys._MEIPASS，源码运行时取项目根
- 可写数据（config.json / proxy.db）：优先 exe 同级 data/，不可写则退回
  %LOCALAPPDATA%，保证「拷贝即用」又不因权限失败
- 环境变量 SWITCHBOARD_DATA_DIR 可强制指定数据目录（兼容旧名 CCP_DATA_DIR）
"""
from __future__ import annotations

import os
import sys
import tempfile

APP_NAME = "Switchboard"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出的 exe 中。"""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def source_root() -> str:
    """源码模式下的项目根目录。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_dir() -> str:
    """只读资源根目录（web/ 静态文件所在）。"""
    return str(sys._MEIPASS) if is_frozen() else source_root()


def web_dir() -> str:
    return os.path.join(resource_dir(), "web")


def _writable(path: str) -> bool:
    """目录可创建且可写入（实际落一次文件，避免权限误判）。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        return True
    except Exception:  # noqa: BLE001
        return False


def data_dir() -> str:
    """可写数据目录（config.json / proxy.db 所在）。"""
    env = (os.environ.get("SWITCHBOARD_DATA_DIR")
           or os.environ.get("CCP_DATA_DIR", "")).strip()  # CCP_DATA_DIR: 旧名，兼容保留
    if env:
        p = os.path.abspath(env)
        os.makedirs(p, exist_ok=True)
        return p

    if is_frozen():
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates = [
            os.path.join(exe_dir, "data"),
            os.path.join(
                os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                APP_NAME,
                "data",
            ),
        ]
    else:
        candidates = [os.path.join(source_root(), "data")]

    for c in candidates:
        if _writable(c):
            return c

    # 极端情况：候选目录全部不可写，退回临时目录（至少不崩）
    fallback = os.path.join(tempfile.gettempdir(), APP_NAME, "data")
    os.makedirs(fallback, exist_ok=True)
    return fallback
