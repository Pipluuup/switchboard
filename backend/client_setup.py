"""客户端一键接入：把本地 Agent 客户端的配置改写为指向本服务。

机制与 ccswitch 一致：改写客户端的配置文件（Claude Code 的
`~/.claude/settings.json`、Codex 的 `~/.codex/config.toml`、Gemini CLI 的
`~/.gemini/.env`），切换前先备份到 `data/client_backups/`，
随时可一键还原。

安全约束：
- 只改写**已存在的**配置文件（不凭空创建，避免覆盖用户尚未初始化的目录）
- 备份写在网关自己的 data 目录，不污染客户端目录
- 还原时用最近一次备份
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from typing import Any

from .config import store
from .paths import data_dir


def _home(path: str) -> str:
    return os.path.expanduser(path)


# 客户端定义：配置文件路径 + 写入方式
CLIENTS: dict[str, dict[str, Any]] = {
    "claude": {
        "label": "Claude Code",
        "config_path": "~/.claude/settings.json",
        "format": "json",
        "hint": "Anthropic 协议：Base URL 指向本服务，Key 用网关的客户端 Key",
    },
    "codex": {
        "label": "Codex CLI",
        "config_path": "~/.codex/config.toml",
        "format": "toml",
        "hint": "Responses 协议：base_url 指向本服务 /v1",
    },
    "gemini": {
        "label": "Gemini CLI",
        "config_path": "~/.gemini/.env",
        "format": "env",
        "hint": "Gemini 协议（需上游支持）：GOOGLE_GEMINI_BASE_URL 指向本服务",
    },
}


def backup_dir(client: str) -> str:
    return os.path.join(data_dir(), "client_backups", client)


def _backup(client: str) -> str | None:
    """备份客户端当前配置文件，返回备份路径（原文件不存在则返回 None）。"""
    src = _home(CLIENTS[client]["config_path"])
    if not os.path.isfile(src):
        return None
    d = backup_dir(client)
    os.makedirs(d, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(d, f"{stamp}-{os.path.basename(src)}")
    shutil.copy2(src, dst)
    return dst


def _latest_backup(client: str) -> str | None:
    d = backup_dir(client)
    if not os.path.isdir(d):
        return None
    files = [os.path.join(d, f) for f in os.listdir(d)]
    files = [f for f in files if os.path.isfile(f)]
    return max(files, key=os.path.getmtime) if files else None


def gateway_base() -> str:
    """网关对外地址（客户端要访问的 Base URL）。"""
    st = store.config.settings
    host = st.host if st.host not in ("", "0.0.0.0", "::") else "127.0.0.1"
    return f"http://{host}:{st.port}"


# ------------------------------------------------------------------ 应用


# Claude Code 的档位变量。_MODEL 是真正发给网关的模型名，_MODEL_NAME
# 只是 UI 徽章上的显示名（Claude Code 里 label 优先取 _MODEL_NAME）。
# 两个都要写，否则徽章会一直显示上一次残留的旧模型名。
CLAUDE_TIERS = ("SONNET", "HAIKU", "OPUS", "FABLE")

# 会绕过档位模型、单独决定实际请求模型的旁路变量。
# 留着它们 = 客户端里残留的旧供应商模型名又会把请求带偏，
# 所以一键接入时必须清掉。
CLAUDE_BYPASS_VARS = (
    "ANTHROPIC_DEFAULT_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)


def _apply_claude(profile: dict, base: str, key: str) -> str:
    """写 Claude Code 的 settings.json（只动 env 段，其余原样保留）。"""
    path = _home(CLIENTS["claude"]["config_path"])
    data: dict[str, Any] = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:  # noqa: BLE001
            data = {}
    env = dict(data.get("env") or {})
    model = profile.get("model") or ""
    env["ANTHROPIC_BASE_URL"] = base
    env["ANTHROPIC_AUTH_TOKEN"] = key
    if model:
        for tier in CLAUDE_TIERS:
            env[f"ANTHROPIC_DEFAULT_{tier}_MODEL"] = model
            # 显示名跟着别名走：徽章显示的就是这个，而不是网关侧的真实上游名。
            # 不写的话，settings.json 里旧的 _MODEL_NAME 会一直留在徽章上。
            env[f"ANTHROPIC_DEFAULT_{tier}_MODEL_NAME"] = model
        # 主模型也要同步：否则客户端残留的其他供应商模型名（如方舟的
        # ark-code-latest）会覆盖档位模型，请求到不了我们想去的上游。
        env["ANTHROPIC_MODEL"] = model
        data["model"] = model
        # 旁路变量优先级高于档位变量，残留值必须清掉。
        for var in CLAUDE_BYPASS_VARS:
            env.pop(var, None)
    env.setdefault("API_TIMEOUT_MS", "3000000")
    env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
    data["env"] = env
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _apply_codex(profile: dict, base: str, key: str) -> str:
    """写 Codex 的 config.toml（Responses 协议）。"""
    path = _home(CLIENTS["codex"]["config_path"])
    text = ""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()

    model = profile.get("model") or "gpt-5"
    block = (
        f'\n[model_providers.switchboard]\n'
        f'name = "Switchboard"\n'
        f'base_url = "{base}/v1"\n'
        f'wire_api = "responses"\n'
        f'requires_openai_auth = false\n'
        f'\n[profiles.switchboard]\n'
        f'model = "{model}"\n'
        f'model_provider = "switchboard"\n'
    )
    # 去掉旧的同名段，避免重复堆积
    text = re.sub(r"\n\[model_providers\.switchboard\].*?(?=\n\[|\Z)", "", text, flags=re.S)
    text = re.sub(r"\n\[profiles\.switchboard\].*?(?=\n\[|\Z)", "", text, flags=re.S)
    text = text.rstrip() + "\n" + block
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _apply_gemini(profile: dict, base: str, key: str) -> str:
    """写 Gemini CLI 的 .env。"""
    path = _home(CLIENTS["gemini"]["config_path"])
    lines: list[str] = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    wanted = {
        "GOOGLE_GEMINI_BASE_URL": base,
        "GEMINI_API_KEY": key,
    }
    out: list[str] = []
    replaced: set[str] = set()
    for ln in lines:
        m = re.match(r"^\s*([A-Z_]+)\s*=", ln)
        if m and m.group(1) in wanted:
            k = m.group(1)
            if k not in replaced:
                out.append(f"{k}={wanted[k]}")
                replaced.add(k)
            continue
        out.append(ln)
    for k, v in wanted.items():
        if k not in replaced:
            out.append(f"{k}={v}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")
    return path


_APPLY = {"claude": _apply_claude, "codex": _apply_codex, "gemini": _apply_gemini}


def apply_setup(client: str, profile: dict) -> dict:
    """应用档位：备份 → 改写客户端配置。"""
    if client not in CLIENTS:
        raise ValueError(f"不支持的客户端：{client}")
    ck = store.get_key(profile.get("client_key_id") or "")
    if not ck:
        raise ValueError("档位未绑定有效的客户端 Key")
    if not ck.enabled:
        raise ValueError(f"客户端 Key「{ck.name}」已停用")

    bak = _backup(client)
    base = gateway_base()
    path = _APPLY[client](profile, base, ck.key)
    return {
        "ok": True,
        "client": client,
        "config_path": path,
        "backup": bak or "",
        "base_url": base,
        "key_name": ck.name,
        "model": profile.get("model") or "",
        "message": f"已写入 {path}" + (f"（原文件备份：{bak}）" if bak else "（原文件不存在，未备份）"),
    }


def restore_setup(client: str) -> dict:
    """从最近一次备份还原客户端配置。"""
    if client not in CLIENTS:
        raise ValueError(f"不支持的客户端：{client}")
    bak = _latest_backup(client)
    if not bak:
        return {"ok": False, "client": client, "message": "没有可用的备份，无法还原"}
    dst = _home(CLIENTS[client]["config_path"])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(bak, dst)
    return {"ok": True, "client": client, "config_path": dst, "backup": bak,
            "message": f"已从 {os.path.basename(bak)} 还原"}


# ------------------------------------------------------------------ 状态


def status(client: str) -> dict:
    """报告客户端当前配置状态：是否已指向本服务。"""
    if client not in CLIENTS:
        raise ValueError(f"不支持的客户端：{client}")
    path = _home(CLIENTS[client]["config_path"])
    info: dict[str, Any] = {
        "client": client,
        "label": CLIENTS[client]["label"],
        "config_path": path,
        "exists": os.path.isfile(path),
        "hint": CLIENTS[client]["hint"],
        "base_url": "",
        "model": "",
        "attached": False,
        "has_backup": _latest_backup(client) is not None,
    }
    if not info["exists"]:
        return info

    base = gateway_base()
    try:
        if client == "claude":
            with open(path, "r", encoding="utf-8") as f:
                env = (json.load(f) or {}).get("env") or {}
            info["base_url"] = env.get("ANTHROPIC_BASE_URL", "")
            info["model"] = env.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "")
            info["attached"] = info["base_url"].rstrip("/") == base.rstrip("/")
        elif client == "codex":
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            m = re.search(r'base_url\s*=\s*"([^"]+)"', text)
            info["base_url"] = m.group(1) if m else ""
            m2 = re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.M)
            info["model"] = m2.group(1) if m2 else ""
            info["attached"] = base in info["base_url"]
        else:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            m = re.search(r"^GOOGLE_GEMINI_BASE_URL=(.*)$", text, re.M)
            info["base_url"] = (m.group(1).strip() if m else "")
            info["attached"] = base in info["base_url"]
    except Exception as e:  # noqa: BLE001
        info["error"] = str(e)[:200]
    return info


def all_status() -> list[dict]:
    return [status(c) for c in CLIENTS]
