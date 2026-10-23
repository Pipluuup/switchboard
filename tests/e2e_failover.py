"""故障转移链的端到端测试。

验证真实 HTTP 路径上的行为：
- 链上某一档 500/429/404 → 自动切下一档，最终成功
- 400/401 → 不切换，直接返回错误
- 每档可以有自己的上游与模型
- 请求日志记录切换轨迹

⚠️ 开头的清场会**删光目标实例的全部上游/映射/Key**，必须跑隔离实例。

前置：
    1. set SWITCHBOARD_DATA_DIR=<临时目录> && python run.py --no-browser --port 8130
    2. python tests/mock_failover_upstream.py --port 8110
运行：
    python tests/e2e_failover.py

地址可用环境变量覆盖：SWITCHBOARD_BASE / SWITCHBOARD_MOCK
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx

BASE = os.environ.get("SWITCHBOARD_BASE", "http://127.0.0.1:8130").rstrip("/")
MOCK = os.environ.get("SWITCHBOARD_MOCK", "http://127.0.0.1:8110/v1").rstrip("/")

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond and detail else ""))


async def main():
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as c:
        await c.put(f"{BASE}/api/settings", json={"use_system_proxy": False})

        # ---------------------------------------------------- 0. 清场
        print("\n[0] 清场")
        for u in (await c.get(f"{BASE}/api/upstreams")).json():
            await c.delete(f"{BASE}/api/upstreams/{u['id']}")
        for m in (await c.get(f"{BASE}/api/mappings")).json():
            await c.delete(f"{BASE}/api/mappings/{m['id']}")
        for k in (await c.get(f"{BASE}/api/keys")).json():
            await c.delete(f"{BASE}/api/keys/{k['id']}")
        await c.delete(f"{BASE}/api/logs")
        check("清场完成", True)

        # ---------------------------------------------------- 1. 建上游
        print("\n[1] 创建上游")
        # 三个上游，同一个 mock 服务，靠模型名区分行为
        up_a = (await c.post(f"{BASE}/api/upstreams", json={
            "name": "Fail-A", "base_url": MOCK, "api_key": "sk-mock",
            "protocol": "anthropic", "priority": 10,
            "models": ["fail500-model", "fail400-model", "fail401-model"],
        })).json()
        up_b = (await c.post(f"{BASE}/api/upstreams", json={
            "name": "Fail-B", "base_url": MOCK, "api_key": "sk-mock",
            "protocol": "anthropic", "priority": 20,
            "models": ["fail429-model", "fail404-model"],
        })).json()
        up_c = (await c.post(f"{BASE}/api/upstreams", json={
            "name": "Good-C", "base_url": MOCK, "api_key": "sk-mock",
            "protocol": "anthropic", "priority": 30,
            "models": ["good-model", "fail500-model", "fail429-model",
                       "fail404-model", "fail400-model", "fail401-model"],
        })).json()
        check("创建 A/B/C 三个上游",
              bool(up_a.get("id")) and bool(up_b.get("id")) and bool(up_c.get("id")))

        ck = (await c.post(f"{BASE}/api/keys", json={"name": "FO客户端"})).json()
        hdr = {"x-api-key": ck["key"]}
        check("创建客户端 Key", bool(ck.get("key")))

        # ---------------------------------------------------- 2. 建链式映射
        print("\n[2] 创建链式映射")
        # A(500) → B(429) → C(正常)
        mp = (await c.post(f"{BASE}/api/mappings", json={
            "alias": "claude-chain", "target": "fail500-model",
            "upstream_id": up_a["id"],
            "chain": [
                {"upstream_id": up_a["id"], "target": "fail500-model"},
                {"upstream_id": up_b["id"], "target": "fail429-model"},
                {"upstream_id": up_c["id"], "target": "good-model"},
            ],
        })).json()
        check("链式映射创建成功", bool(mp.get("id")), str(mp)[:200])
        check("链被保存下来", len(mp.get("chain") or []) == 3, str(mp.get("chain")))

        got = (await c.get(f"{BASE}/api/mappings")).json()
        one = [m for m in got if m.get("id") == mp["id"]]
        check("映射列表里能读回链", one and len(one[0].get("chain") or []) == 3,
              str(one[0].get("chain")) if one else "没找到")
        check("链上每档带 upstream_name",
              one and all(h.get("upstream_name") for h in one[0].get("chain") or []),
              str(one[0].get("chain")) if one else "")

        # ---------------------------------------------------- 3. 500 → 切下一档
        print("\n[3] 500 触发切换")
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "claude-chain", "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}]})
        check("请求最终成功（200）", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        body = r.json() if r.status_code == 200 else {}
        check("响应来自最后一档（good-model）",
              body.get("model") == "good-model", str(body.get("model")))
        check("响应内容标记了到达的档",
              "reached:good-model" in json.dumps(body, ensure_ascii=False),
              json.dumps(body, ensure_ascii=False)[:200])

        # ---------------------------------------------------- 4. 日志轨迹
        print("\n[4] 请求日志记录切换轨迹")
        await asyncio.sleep(0.4)
        logs = (await c.get(f"{BASE}/api/logs?limit=5")).json()
        items = logs.get("items") or []
        check("有日志记录", len(items) >= 1, str(len(items)))
        log = items[0] if items else {}
        check("日志标记为成功", log.get("ok") == 1, str(log.get("ok")))
        check("日志里的上游是最终成功的那档", log.get("upstream_name") == "Good-C",
              str(log.get("upstream_name")))
        check("日志里的实际模型是最后一档的模型",
              log.get("actual_model") == "good-model", str(log.get("actual_model")))
        trail_raw = log.get("failover_trail") or ""
        check("日志带 failover_trail 字段", bool(trail_raw), str(trail_raw)[:200])
        trail = json.loads(trail_raw) if trail_raw else []
        check("轨迹记录了 3 次尝试", len(trail) == 3, str(trail))
        check("轨迹按档号递增", [t.get("hop") for t in trail] == [1, 2, 3],
              str([t.get("hop") for t in trail]))
        check("轨迹第 1 档是 A / 500",
              trail and trail[0].get("upstream") == "Fail-A" and trail[0].get("status") == 500,
              str(trail[0]) if trail else "")
        check("轨迹第 2 档是 B / 429",
              len(trail) > 1 and trail[1].get("upstream") == "Fail-B"
              and trail[1].get("status") == 429, str(trail[1]) if len(trail) > 1 else "")
        check("轨迹第 3 档是 C / 200 成功",
              len(trail) > 2 and trail[2].get("upstream") == "Good-C"
              and trail[2].get("status") == 200, str(trail[2]) if len(trail) > 2 else "")
        check("每档都记了自己的模型名",
              [t.get("model") for t in trail] ==
              ["fail500-model", "fail429-model", "good-model"],
              str([t.get("model") for t in trail]))

        # ---------------------------------------------------- 5. 404 触发切换
        print("\n[5] 404（模型下线）触发切换")
        await c.post(f"{BASE}/api/mappings", json={
            "alias": "claude-404", "target": "fail404-model",
            "upstream_id": up_b["id"],
            "chain": [
                {"upstream_id": up_b["id"], "target": "fail404-model"},
                {"upstream_id": up_c["id"], "target": "good-model"},
            ],
        })
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "claude-404", "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}]})
        check("404 后切到下一档并成功", r.status_code == 200,
              f"{r.status_code} {r.text[:200]}")
        check("404 场景的最终模型是 good-model",
              r.status_code == 200 and r.json().get("model") == "good-model",
              str(r.json().get("model")) if r.status_code == 200 else "")

        # 404 不该把上游熔断（上游本身是好的）
        await asyncio.sleep(0.3)
        ups = {u["id"]: u for u in (await c.get(f"{BASE}/api/upstreams")).json()}
        st = ups.get(up_b["id"], {}).get("circuit_state")
        check("404 没有熔断上游 B", st != "open", str(st))

        # ---------------------------------------------------- 6. 400 不切换
        print("\n[6] 400 不触发切换")
        await c.post(f"{BASE}/api/mappings", json={
            "alias": "claude-400", "target": "fail400-model",
            "upstream_id": up_a["id"],
            "chain": [
                {"upstream_id": up_a["id"], "target": "fail400-model"},
                {"upstream_id": up_c["id"], "target": "good-model"},
            ],
        })
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "claude-400", "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}]})
        check("400 直接返回错误（不切档）", r.status_code == 400,
              f"{r.status_code} {r.text[:200]}")

        # ---------------------------------------------------- 7. 401 不切换
        print("\n[7] 401 不触发切换")
        await c.post(f"{BASE}/api/mappings", json={
            "alias": "claude-401", "target": "fail401-model",
            "upstream_id": up_a["id"],
            "chain": [
                {"upstream_id": up_a["id"], "target": "fail401-model"},
                {"upstream_id": up_c["id"], "target": "good-model"},
            ],
        })
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "claude-401", "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}]})
        check("401 直接返回错误（不切档）", r.status_code == 401,
              f"{r.status_code} {r.text[:200]}")
        # 401 会立即熔断该上游
        await asyncio.sleep(0.3)
        ups = {u["id"]: u for u in (await c.get(f"{BASE}/api/upstreams")).json()}
        check("401 熔断了上游 A", ups.get(up_a["id"], {}).get("circuit_state") == "open",
              str(ups.get(up_a["id"], {}).get("circuit_state")))

        # ---------------------------------------------------- 8. 整链失败
        print("\n[8] 整链都失败")
        await c.post(f"{BASE}/api/mappings", json={
            "alias": "claude-allfail", "target": "fail500-model",
            "upstream_id": up_c["id"],
            "chain": [
                {"upstream_id": up_c["id"], "target": "fail500-model"},
                {"upstream_id": up_c["id"], "target": "fail500-model"},
            ],
        })
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "claude-allfail", "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}]})
        check("整链失败返回错误", r.status_code >= 500, f"{r.status_code} {r.text[:200]}")
        await asyncio.sleep(0.4)
        logs = (await c.get(f"{BASE}/api/logs?limit=3")).json()
        items = logs.get("items") or []
        fail_log = next((i for i in items if i.get("ok") == 0), None)
        check("失败日志存在", fail_log is not None, str(items)[:200])
        if fail_log:
            check("失败日志也带轨迹", bool(fail_log.get("failover_trail")),
                  str(fail_log.get("failover_trail"))[:200])

        # ---------------------------------------------------- 9. 老式映射仍工作
        print("\n[9] 老式映射（无 chain）仍工作")
        await c.post(f"{BASE}/api/mappings", json={
            "alias": "claude-legacy", "target": "good-model", "upstream_id": up_c["id"]})
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "claude-legacy", "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}]})
        check("老式映射正常工作", r.status_code == 200,
              f"{r.status_code} {r.text[:200]}")

        # ---------------------------------------------------- 10. 流式切换
        print("\n[10] 流式请求也能切换")
        await c.post(f"{BASE}/api/mappings", json={
            "alias": "claude-stream", "target": "fail500-model",
            "upstream_id": up_a["id"],
            "chain": [
                {"upstream_id": up_a["id"], "target": "fail500-model"},
                {"upstream_id": up_c["id"], "target": "good-model"},
            ],
        })
        # 先把 A 从熔断里恢复出来
        await c.post(f"{BASE}/api/upstreams/{up_a['id']}/reset")
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "claude-stream", "max_tokens": 32, "stream": True,
            "messages": [{"role": "user", "content": "hi"}]})
        check("流式请求切换后成功", r.status_code == 200, f"{r.status_code}")
        check("流式内容来自最后一档", "reached:good-model" in r.text, r.text[:200])

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("失败项：")
        for name, _ok, detail in failed:
            print(f"  - {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
