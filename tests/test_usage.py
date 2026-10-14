"""用量滚动窗口统计的单元测试（临时 SQLite，无需启动服务）。

重点验证窗口边界：5 小时 / 7 天 / 30 天必须各自只统计窗口内的请求，
且模型聚合取的是**映射后**的模型名（actual_model）——那才是消耗上游额度的名字。

运行：python tests/test_usage.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import LogDB  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond else ""))


H = 3600
D = 86400


def build_db() -> LogDB:
    tmp = os.path.join(tempfile.mkdtemp(prefix="ccp_usage_"), "usage.db")
    return LogDB(tmp)


def seed(db: LogDB) -> None:
    now = time.time()
    # (距今秒数, 输入, 输出, ok, actual_model, request_model, upstream)
    rows = [
        (1 * H, 100, 50, 1, "glm-5.3", "go-glm", "OpenCode Go (Chat)"),
        (4 * H, 200, 80, 1, "glm-5.3", "go-glm", "OpenCode Go (Chat)"),
        (6 * H, 300, 100, 1, "kimi-k2.7-code", "go-kimi", "OpenCode Go (Chat)"),   # 仅 7d / 30d
        (2 * D, 400, 150, 1, "claude-sonnet-4-5", "claude-sonnet-4-5", "CommandCode Goat"),
        (6 * D, 500, 200, 0, "claude-sonnet-4-5", "claude-sonnet-4-5", "CommandCode Goat"),  # 失败
        (10 * D, 600, 250, 1, "glm-5.3", "go-glm", "OpenCode Go (Chat)"),          # 仅 30d
        (40 * D, 700, 300, 1, "glm-5.3", "go-glm", "OpenCode Go (Chat)"),          # 全部窗口外
    ]
    for age, tin, tout, ok, actual, req, up in rows:
        db.add(ts=now - age, ok=ok, input_tokens=tin, output_tokens=tout,
               actual_model=actual, request_model=req, upstream_name=up,
               client_name="测试", latency_ms=100, status=200 if ok else 500)


def main() -> int:
    db = build_db()
    empty = db.usage_windows()
    check("空库也返回三个窗口", len(empty) == 3)
    check("空库窗口顺序为 5h/7d/30d", [w["key"] for w in empty] == ["5h", "7d", "30d"])
    check("空库成功率为 100.0", all(w["success_rate"] == 100.0 for w in empty))
    check("空库合计 token 为 0", all(w["total_tokens"] == 0 for w in empty))

    seed(db)
    wins = {w["key"]: w for w in db.usage_windows()}

    print("\n[1] 窗口边界")
    check("5h 只统计 5 小时内的 2 条请求", wins["5h"]["requests"] == 2,
          f"实际 {wins['5h']['requests']}")
    check("7d 统计 7 天内的 5 条请求", wins["7d"]["requests"] == 5,
          f"实际 {wins['7d']['requests']}")
    check("30d 统计 30 天内的 6 条请求", wins["30d"]["requests"] == 6,
          f"实际 {wins['30d']['requests']}")
    check("40 天前的请求不出现在任何窗口",
          all(w["requests"] < 7 for w in wins.values()))

    print("\n[2] Token 汇总")
    check("5h 输入 token = 300", wins["5h"]["input_tokens"] == 300,
          f"实际 {wins['5h']['input_tokens']}")
    check("5h 输出 token = 130", wins["5h"]["output_tokens"] == 130,
          f"实际 {wins['5h']['output_tokens']}")
    check("5h 合计 = 输入 + 输出", wins["5h"]["total_tokens"] == 430,
          f"实际 {wins['5h']['total_tokens']}")
    check("30d 输入 = 100+200+300+400+500+600",
          wins["30d"]["input_tokens"] == 2100, f"实际 {wins['30d']['input_tokens']}")

    print("\n[3] 成功率")
    check("5h 全部成功，成功率 100", wins["5h"]["success_rate"] == 100.0)
    check("7d 有 1 条失败", wins["7d"]["failed_requests"] == 1,
          f"实际 {wins['7d']['failed_requests']}")
    check("7d 成功率 = 4/5 = 80", wins["7d"]["success_rate"] == 80.0,
          f"实际 {wins['7d']['success_rate']}")
    check("30d 成功数 = 5", wins["30d"]["ok_requests"] == 5,
          f"实际 {wins['30d']['ok_requests']}")

    print("\n[4] 模型聚合用映射后的名字")
    m5 = {m["model"] for m in wins["5h"]["by_model"]}
    check("5h 模型名是 actual_model（glm-5.3）而非别名 go-glm", m5 == {"glm-5.3"}, str(m5))
    m7 = {m["model"] for m in wins["7d"]["by_model"]}
    check("7d 含 glm-5.3 / kimi-k2.7-code / claude-sonnet-4-5",
          m7 == {"glm-5.3", "kimi-k2.7-code", "claude-sonnet-4-5"}, str(m7))
    top = wins["30d"]["by_model"][0]
    # glm-5.3 在 30 天窗口内只有 1h/4h/10d 三条：150+280+850 = 1280
    # （40 天前那条 1000 已正确排除）
    check("30d 榜首是 token 最多的 glm-5.3（1280）",
          top["model"] == "glm-5.3" and top["tin"] + top["tout"] == 1280,
          f"实际 {top['model']} {top['tin'] + top['tout']}")

    print("\n[5] 上游聚合与时间戳")
    ups = {u["upstream_name"] for u in wins["30d"]["by_upstream"]}
    check("30d 覆盖两个上游", ups == {"OpenCode Go (Chat)", "CommandCode Goat"}, str(ups))
    check("5h 的 last_ts 在窗口内",
          wins["5h"]["last_ts"] > time.time() - 5 * H)
    check("5h 的 first_ts 落在约 4 小时前",
          abs((time.time() - wins["5h"]["first_ts"]) - 4 * H) < 120,
          f"实际 {(time.time() - wins['5h']['first_ts']) / H:.2f} 小时前")

    ok = sum(1 for _, s, _ in results if s)
    print("\n" + "=" * 56)
    print(f"  用量窗口测试：{ok}/{len(results)} 通过")
    print("=" * 56)
    for n, s, d in results:
        if not s:
            print(f"  - {n}: {d}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
