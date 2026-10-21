"""客户端一键接入（client_setup）单元测试。

重点验证 Claude Code 的 settings.json 改写逻辑：

- 档位模型别名要同时写进 ``ANTHROPIC_DEFAULT_{tier}_MODEL`` 和
  ``..._MODEL_NAME``。前者是真正发给网关的模型名，后者是 UI 徽章上的
  显示名（Claude Code 里 label 优先取 _MODEL_NAME）。只写前者的话，
  旧文件里残留的 _MODEL_NAME 会一直挂在徽章上。
- 会绕过档位模型的旁路变量（ANTHROPIC_DEFAULT_MODEL 等）必须清掉，
  否则客户端残留的旧供应商模型名会覆盖档位模型。

运行：python tests/test_client_setup.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import client_setup  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond else ""))


def _write_settings(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _read_env(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return (json.load(f) or {}).get("env") or {}


def _apply_to(tmp: str, existing: dict | None, profile: dict) -> dict:
    """在临时目录里跑一次 _apply_claude，返回写后的 env。"""
    path = os.path.join(tmp, "settings.json")
    if existing is not None:
        _write_settings(path, existing)
    old_path = client_setup.CLIENTS["claude"]["config_path"]
    client_setup.CLIENTS["claude"]["config_path"] = path
    try:
        client_setup._apply_claude(profile, "http://127.0.0.1:8123", "sk-test")
    finally:
        client_setup.CLIENTS["claude"]["config_path"] = old_path
    return _read_env(path)


def test_model_name_synced():
    """_MODEL_NAME 要跟 _MODEL 同步写入，徽章才不会显示旧模型名。"""
    tmp = tempfile.mkdtemp(prefix="ccp_cs_")
    try:
        env = _apply_to(tmp, None, {"model": "Claude"})
        for tier in ("SONNET", "HAIKU", "OPUS", "FABLE"):
            check(f"{tier}_MODEL 写入别名",
                  env.get(f"ANTHROPIC_DEFAULT_{tier}_MODEL") == "Claude",
                  str(env.get(f"ANTHROPIC_DEFAULT_{tier}_MODEL")))
            check(f"{tier}_MODEL_NAME 同步显示名",
                  env.get(f"ANTHROPIC_DEFAULT_{tier}_MODEL_NAME") == "Claude",
                  str(env.get(f"ANTHROPIC_DEFAULT_{tier}_MODEL_NAME")))
        check("ANTHROPIC_MODEL 同步",
              env.get("ANTHROPIC_MODEL") == "Claude", str(env.get("ANTHROPIC_MODEL")))
        check("BASE_URL 写入",
              env.get("ANTHROPIC_BASE_URL") == "http://127.0.0.1:8123")
        check("AUTH_TOKEN 写入",
              env.get("ANTHROPIC_AUTH_TOKEN") == "sk-test")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stale_model_name_overwritten():
    """旧文件里的过期 _MODEL_NAME 必须被当前别名覆盖，不能残留。"""
    tmp = tempfile.mkdtemp(prefix="ccp_cs_")
    try:
        stale = {"env": {
            "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "glm-5-3-flash-260828",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME": "glm-5-3-flash-260828",
            "ANTHROPIC_DEFAULT_OPUS_MODEL_NAME": "glm-5-3-flash-260828",
            "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME": "glm-5-3-flash-260828",
        }}
        env = _apply_to(tmp, stale, {"model": "Claude"})
        leftover = {k: v for k, v in env.items()
                    if k.endswith("_MODEL_NAME") and v == "glm-5-3-flash-260828"}
        check("过期 _MODEL_NAME 已全部覆盖", not leftover, str(leftover))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bypass_vars_removed():
    """旁路模型变量会覆盖档位模型，必须清掉。"""
    tmp = tempfile.mkdtemp(prefix="ccp_cs_")
    try:
        dirty = {"env": {
            "ANTHROPIC_DEFAULT_MODEL": "ark-code-latest",
            "ANTHROPIC_SMALL_FAST_MODEL": "claude-haiku-4-5",
            "CLAUDE_CODE_SUBAGENT_MODEL": "minimax-m3.1-flash-free",
        }}
        env = _apply_to(tmp, dirty, {"model": "Claude"})
        for var in client_setup.CLAUDE_BYPASS_VARS:
            check(f"{var} 已清除", var not in env, str(env.get(var)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unrelated_env_preserved():
    """无关的 env 键要原样保留，不能被一键接入冲掉。"""
    tmp = tempfile.mkdtemp(prefix="ccp_cs_")
    try:
        keep = {"env": {
            "MY_CUSTOM_FLAG": "1",
            "ANTHROPIC_DEFAULT_MODEL": "ark-code-latest",
        }, "permissions": {"allow": ["Bash(ls:*)"]}}
        path = os.path.join(tmp, "settings.json")
        _write_settings(path, keep)
        old_path = client_setup.CLIENTS["claude"]["config_path"]
        client_setup.CLIENTS["claude"]["config_path"] = path
        try:
            client_setup._apply_claude({"model": "Claude"}, "http://127.0.0.1:8123", "sk-test")
        finally:
            client_setup.CLIENTS["claude"]["config_path"] = old_path
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        check("无关 env 保留", data["env"].get("MY_CUSTOM_FLAG") == "1")
        check("其他顶层段保留", data.get("permissions") == {"allow": ["Bash(ls:*)"]})
        check("model 顶层字段同步", data.get("model") == "Claude")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_model_profile_keeps_name():
    """档位没配模型时，不该乱改 _MODEL_NAME（保留用户原值）。"""
    tmp = tempfile.mkdtemp(prefix="ccp_cs_")
    try:
        existing = {"env": {"ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "keep-me"}}
        env = _apply_to(tmp, existing, {"model": ""})
        check("未配模型时保留原显示名",
              env.get("ANTHROPIC_DEFAULT_SONNET_MODEL_NAME") == "keep-me",
              str(env.get("ANTHROPIC_DEFAULT_SONNET_MODEL_NAME")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_model_name_synced()
    test_stale_model_name_overwritten()
    test_bypass_vars_removed()
    test_unrelated_env_preserved()
    test_no_model_profile_keeps_name()
    failed = [r for r in results if not r[1]]
    print(f"\n{'全部通过' if not failed else str(len(failed)) + ' 项失败'}")
    sys.exit(1 if failed else 0)
