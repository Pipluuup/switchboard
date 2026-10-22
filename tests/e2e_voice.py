"""端到端验证 Say less 三态：请求 → 日志「优化」列的判定依据。

⚠️ 开头的清场会**删光目标实例的全部上游/映射/Key**，必须跑隔离实例，
   否则真实配置零接触（同 test_e2e.py 的规约，见 docs/03）。

前置：
    1. 启动隔离实例
       set SWITCHBOARD_DATA_DIR=<临时目录> && python run.py --no-browser --port 8124
    2. 启动模拟上游 python tests/mock_upstream.py --port 8100
运行：
    python tests/e2e_voice.py
"""
from __future__ import annotations

import json
import os
import sys

import httpx

# 地址可用环境变量覆盖：SWITCHBOARD_BASE / SWITCHBOARD_MOCK
BASE = os.environ.get("SWITCHBOARD_BASE", "http://127.0.0.1:8124").rstrip("/")
MOCK = os.environ.get("SWITCHBOARD_MOCK", "http://127.0.0.1:8100/v1").rstrip("/")

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond else ""))


def main() -> int:
    c = httpx.Client(timeout=30, trust_env=False)

    # 干净环境
    for u in c.get(f"{BASE}/api/upstreams").json():
        c.delete(f"{BASE}/api/upstreams/{u['id']}")
    for k in c.get(f"{BASE}/api/keys").json():
        c.delete(f"{BASE}/api/keys/{k['id']}")
    for m in c.get(f"{BASE}/api/mappings").json():
        c.delete(f"{BASE}/api/mappings/{m['id']}")
    c.delete(f"{BASE}/api/logs")

    up = c.post(f"{BASE}/api/upstreams", json={
        "name": "mock", "base_url": MOCK, "api_key": "x", "protocol": "openai",
        "enabled": True, "priority": 10, "weight": 1, "timeout": 30, "max_retries": 1,
        "models": ["mock-gpt-4o"],
    }).json()
    c.post(f"{BASE}/api/keys", json={"name": "k", "key": "sk-px-test", "enabled": True})
    c.post(f"{BASE}/api/mappings", json={"alias": "mock-gpt-4o", "target": "mock-gpt-4o",
                                        "enabled": True})
    c.put(f"{BASE}/api/settings", json={"use_system_proxy": False, "caveman_enabled": False})

    def send(n_msgs: int, filler: str = ""):
        msgs = [{"role": "user", "content": filler or f"第{i}条问题"} for i in range(n_msgs)]
        msgs.append({"role": "assistant", "content": "上一轮回答"})
        r = c.post(f"{BASE}/v1/chat/completions",
                   headers={"Authorization": "Bearer sk-px-test"},
                   json={"model": "mock-gpt-4o", "messages": msgs})
        if r.status_code != 200:
            raise RuntimeError(f"请求失败 {r.status_code}: {r.text[:200]}")

    def latest() -> dict:
        """最新一条日志的统计（含 DB 里的 voice_injected 列）。"""
        items = c.get(f"{BASE}/api/logs?limit=1").json()["items"]
        if not items:
            return {}
        it = items[0]
        st = json.loads(it.get("caveman_stats") or "{}")
        st["_injected"] = bool(it.get("voice_injected"))
        st["_ok"] = it.get("ok")
        return st

    # ---- 1. 未启动 ----
    c.put(f"{BASE}/api/settings", json={"voice_mode": "off"})
    send(10, "很长的输入" * 2000)
    st = latest()
    v = st.get("voice", {})
    check("未启动：未注入", not st["_injected"], str(st))
    check("未启动：决策 mode=off", v.get("mode") == "off", str(v))
    check("未启动：reason 非空", bool(v.get("reason")), str(v))

    # ---- 2. 启动 ----
    c.put(f"{BASE}/api/settings", json={"voice_mode": "on"})
    send(2, "短问题")
    st = latest()
    v = st.get("voice", {})
    check("启动：已注入", st["_injected"], str(st))
    check("启动：决策 mode=on", v.get("mode") == "on", str(v))
    check("启动：reason 非空", bool(v.get("reason")), str(v))

    # ---- 3. 自适应：小请求跳过 ----
    c.put(f"{BASE}/api/settings", json={"voice_mode": "auto", "voice_auto_min_tokens": 2000,
                                        "voice_auto_min_messages": 4,
                                        "voice_ignore_cache": False})
    send(2, "短问题")
    st = latest()
    v = st.get("voice", {})
    check("自适应·小请求：未注入", not st["_injected"], str(st))
    check("自适应·小请求：mode=auto", v.get("mode") == "auto", str(v))
    check("自适应·小请求：reason 说明跳过", "跳过" in v.get("reason", ""), str(v))

    # ---- 4. 自适应：长会话注入 ----
    send(12, "短问题")
    st = latest()
    v = st.get("voice", {})
    check("自适应·长会话：已注入", st["_injected"], str(st))
    check("自适应·长会话：reason 说明注入", "注入" in v.get("reason", ""), str(v))
    check("自适应·长会话：记录了消息数", v.get("messages", 0) >= 4, str(v))

    # ---- 5. 自适应：大输入注入 ----
    send(2, "很长的输入内容" * 3000)
    st = latest()
    v = st.get("voice", {})
    check("自适应·大输入：已注入", st["_injected"], str(st))
    check("自适应·大输入：记录了 input token", v.get("in_tokens", 0) > 2000, str(v))

    # ---- 6. 规则开销不算进「节省率」 ----
    check("规则开销不产生负节省率", st.get("saved_pct", 0) >= 0,
          f"saved_pct={st.get('saved_pct')}")
    check("规则开销单独记录", st.get("voice_added_chars", 0) > 0,
          f"voice_added_chars={st.get('voice_added_chars')}")

    print("\n最新一条日志的 voice 决策：")
    print(json.dumps(latest().get("voice", {}), ensure_ascii=False, indent=2))

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("失败项：")
        for name, _ok, detail in failed:
            print(f"  - {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
