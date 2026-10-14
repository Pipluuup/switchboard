"""端到端测试：验证协议转换、鉴权、映射、故障切换、限流。

前置：
    1. 启动主服务   python run.py --no-browser --port 8000
    2. 启动模拟上游 python tests/mock_upstream.py --port 8100
运行：
    python tests/test_e2e.py
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"
MOCK_OPENAI = "http://127.0.0.1:8100/v1"
MOCK_ANTHROPIC = "http://127.0.0.1:8100/v1"

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond and detail else ""))


async def main():
    # trust_env=False：绕过系统 HTTP_PROXY，确保直连本地服务
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as c:
        # 关闭「使用系统代理」，确保后端直连本地模拟上游
        await c.put(f"{BASE}/api/settings", json={"use_system_proxy": False})

        # ---------------------------------------------------- 1. 管理 API
        print("\n[1] 管理 API")
        r = await c.get(f"{BASE}/api/overview")
        check("GET /api/overview", r.status_code == 200, str(r.status_code))
        r = await c.get(f"{BASE}/api/upstreams")
        check("GET /api/upstreams", r.status_code == 200, str(r.status_code))
        for u in r.json():
            await c.delete(f"{BASE}/api/upstreams/{u['id']}")

        # 清空旧映射与 Key，重建干净的测试环境
        for m in (await c.get(f"{BASE}/api/mappings")).json():
            await c.delete(f"{BASE}/api/mappings/{m['id']}")
        for k in (await c.get(f"{BASE}/api/keys")).json():
            await c.delete(f"{BASE}/api/keys/{k['id']}")
        await c.delete(f"{BASE}/api/logs")

        # ---------------------------------------------------- 2. 建上游
        print("\n[2] 创建测试上游")
        up_oai = (await c.post(f"{BASE}/api/upstreams", json={
            "name": "Mock-OpenAI", "base_url": MOCK_OPENAI, "api_key": "sk-mock",
            "protocol": "openai", "priority": 10, "models": ["mock-gpt-4o"],
        })).json()
        up_anth = (await c.post(f"{BASE}/api/upstreams", json={
            "name": "Mock-Anthropic", "base_url": MOCK_ANTHROPIC, "api_key": "sk-mock",
            "protocol": "anthropic", "priority": 20, "models": ["mock-claude"],
        })).json()
        check("创建 OpenAI 上游", bool(up_oai.get("id")))
        check("创建 Anthropic 上游", bool(up_anth.get("id")))

        # 绑定：claude-test -> mock-gpt-4o (走 OpenAI 上游)
        await c.post(f"{BASE}/api/mappings", json={
            "alias": "claude-test-oai", "target": "mock-gpt-4o", "upstream_id": up_oai["id"]})
        await c.post(f"{BASE}/api/mappings", json={
            "alias": "claude-test-anth", "target": "mock-claude", "upstream_id": up_anth["id"]})

        # 客户端 Key：带限流
        ck = (await c.post(f"{BASE}/api/keys", json={
            "name": "测试客户端", "rate_limit_rpm": 300, "daily_token_limit": 0})).json()
        key = ck["key"]
        check("创建客户端 Key", bool(key), str(ck)[:120])

        # ---------------------------------------------------- 3. 鉴权
        print("\n[3] 鉴权")
        r = await c.post(f"{BASE}/v1/messages", json={
            "model": "claude-test-oai", "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}]})
        check("无 Key 应被拒绝 401", r.status_code == 401, str(r.status_code))

        r = await c.post(f"{BASE}/v1/messages",
                         headers={"x-api-key": "wrong-key"},
                         json={"model": "claude-test-oai", "max_tokens": 64,
                               "messages": [{"role": "user", "content": "hi"}]})
        check("错误 Key 应被拒绝 401", r.status_code == 401, str(r.status_code))

        # count_tokens 不以 messages 结尾，错误必须仍是 Anthropic 格式
        r = await c.post(f"{BASE}/v1/messages/count_tokens",
                         json={"model": "claude-test-oai",
                               "messages": [{"role": "user", "content": "hi"}]})
        check("count_tokens 鉴权失败 401", r.status_code == 401, str(r.status_code))
        check("count_tokens 错误为 Anthropic 格式",
              r.json().get("type") == "error" and "error" in r.json(), r.text[:160])

        r = await c.get(f"{BASE}/v1/models",
                        headers={"anthropic-version": "2023-06-01"})
        check("带 anthropic-version 的 /v1/models 错误为 Anthropic 格式",
              r.status_code == 401 and r.json().get("type") == "error", r.text[:160])

        # ---------------------------------------------------- 4. Anthropic 入 -> OpenAI 上游（非流式）
        print("\n[4] Anthropic 客户端 -> OpenAI 上游（非流式）")
        hdr = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "claude-test-oai", "max_tokens": 64,
            "messages": [{"role": "user", "content": "你好"}]})
        d = r.json()
        check("返回 200", r.status_code == 200, r.text[:200])
        check("符合 Anthropic 结构 (type=message)", d.get("type") == "message", str(d)[:200])
        check("content 为块数组", isinstance(d.get("content"), list) and
              d["content"][0].get("type") == "text", str(d.get("content"))[:200])
        check("包含 usage", "input_tokens" in d.get("usage", {}), str(d.get("usage")))
        check("stop_reason 正确", d.get("stop_reason") == "end_turn", str(d.get("stop_reason")))

        # ---------------------------------------------------- 5. Anthropic 入 -> OpenAI 上游（流式 + 工具）
        print("\n[5] Anthropic 客户端 -> OpenAI 上游（流式 + 工具调用）")
        body = {
            "model": "claude-test-oai", "max_tokens": 128, "stream": True,
            "system": "你是助手",
            "tools": [{"name": "get_weather", "description": "查天气",
                       "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}],
            "messages": [{"role": "user", "content": "北京天气"}],
        }
        events: list[tuple[str, dict]] = []
        async with c.stream("POST", f"{BASE}/v1/messages", headers=hdr, json=body) as resp:
            check("流式返回 200", resp.status_code == 200, str(resp.status_code))
            buf = ""
            async for chunk in resp.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    ev_name = ""
                    data_str = ""
                    for line in block.splitlines():
                        if line.startswith("event: "):
                            ev_name = line[7:].strip()
                        elif line.startswith("data: "):
                            data_str = line[6:]
                    if ev_name and data_str:
                        try:
                            events.append((ev_name, json.loads(data_str)))
                        except Exception:
                            pass

        names = [e for e, _ in events]
        check("首事件为 message_start", names[0] == "message_start", str(names[:3]))
        check("末事件为 message_stop", names[-1] == "message_stop", str(names[-3:]))
        check("含 content_block_start", "content_block_start" in names, str(names))
        check("含 content_block_delta", "content_block_delta" in names, str(names))
        check("含 message_delta", "message_delta" in names, str(names))

        tool_block = None
        for ev, data in events:
            if ev == "content_block_start":
                cb = data.get("content_block", {})
                if cb.get("type") == "tool_use":
                    tool_block = cb
        check("工具调用被转为 tool_use 块", tool_block is not None and
              tool_block.get("name") == "get_weather", str(tool_block))

        json_delta = "".join(
            d.get("delta", {}).get("partial_json", "")
            for ev, d in events
            if ev == "content_block_delta" and d.get("delta", {}).get("type") == "input_json_delta")
        check("input_json_delta 拼接为合法 JSON", json_delta == '{"city":"北京"}', repr(json_delta))

        stop = [d.get("delta", {}).get("stop_reason") for ev, d in events if ev == "message_delta"]
        check("流式 stop_reason = tool_use", stop and stop[-1] == "tool_use", str(stop))

        # ---------------------------------------------------- 6. Anthropic 入 -> Anthropic 上游（直通）
        print("\n[6] Anthropic 客户端 -> Anthropic 上游（原生直通）")
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "claude-test-anth", "max_tokens": 64,
            "messages": [{"role": "user", "content": "你好"}]})
        d = r.json()
        check("直通返回 200", r.status_code == 200, r.text[:200])
        check("直通保留 Anthropic 结构", d.get("type") == "message" and
              d.get("content")[0]["type"] == "text", str(d)[:200])

        # ---------------------------------------------------- 7. OpenAI 入 -> Anthropic 上游（反向转换）
        print("\n[7] OpenAI 客户端 -> Anthropic 上游（反向转换）")
        r = await c.post(f"{BASE}/v1/chat/completions",
                         headers={"Authorization": f"Bearer {key}"},
                         json={"model": "claude-test-anth", "max_tokens": 64,
                               "messages": [{"role": "user", "content": "你好"}]})
        d = r.json()
        check("反向转换返回 200", r.status_code == 200, r.text[:200])
        check("符合 OpenAI 结构 (object=chat.completion)",
              d.get("object") == "chat.completion", str(d)[:200])
        check("choices[0].message.content 有值",
              (d.get("choices") or [{}])[0].get("message", {}).get("content"), str(d)[:250])

        # OpenAI 入 -> Anthropic 上游（流式）
        ev_text = ""
        async with c.stream("POST", f"{BASE}/v1/chat/completions",
                            headers={"Authorization": f"Bearer {key}"},
                            json={"model": "claude-test-anth", "max_tokens": 64, "stream": True,
                                  "messages": [{"role": "user", "content": "你好"}]}) as resp:
            check("反向流式返回 200", resp.status_code == 200, str(resp.status_code))
            async for chunk in resp.aiter_text():
                ev_text += chunk
        check("反向流式以 [DONE] 结束", ev_text.rstrip().endswith("[DONE]"), ev_text[-80:])
        check("反向流式含 chat.completion.chunk", "chat.completion.chunk" in ev_text)

        # ---------------------------------------------------- 8. 工具结果回传（多轮）
        print("\n[8] 多轮工具结果回传")
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "claude-test-oai", "max_tokens": 128,
            "tools": [{"name": "get_weather", "description": "查天气",
                       "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}],
            "messages": [
                {"role": "user", "content": "北京天气"},
                {"role": "assistant", "content": [
                    {"type": "text", "text": "我来查一下"},
                    {"type": "tool_use", "id": "toolu_x", "name": "get_weather", "input": {"city": "北京"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_x", "content": "晴天 25℃"}]},
            ]})
        check("多轮工具结果请求成功", r.status_code == 200, r.text[:250])
        check("多轮响应结构正确", r.json().get("type") == "message", str(r.json())[:200])

        # ---------------------------------------------------- 9. 模型映射
        print("\n[9] 模型映射")
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "claude-test-oai", "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}]})
        check("别名请求走映射成功", r.status_code == 200, r.text[:200])
        check("响应模型为映射目标", r.json().get("model") == "mock-gpt-4o",
              str(r.json().get("model")))

        # ---------------------------------------------------- 10. count_tokens
        print("\n[10] count_tokens 端点")
        r = await c.post(f"{BASE}/v1/messages/count_tokens", headers=hdr, json={
            "model": "claude-test-oai",
            "messages": [{"role": "user", "content": "hello world"}]})
        check("count_tokens 返回 input_tokens", r.status_code == 200 and
              "input_tokens" in r.json(), r.text[:150])

        # ---------------------------------------------------- 11. 故障切换
        print("\n[11] 故障切换")
        bad = (await c.post(f"{BASE}/api/upstreams", json={
            "name": "Bad-Upstream", "base_url": "http://127.0.0.1:9/v1",
            "api_key": "x", "protocol": "openai", "priority": 1,
            "models": ["mock-gpt-4o"], "max_retries": 1})).json()
        # 让坏节点优先级最高，验证会自动切到好节点
        r = await c.post(f"{BASE}/v1/messages", headers=hdr, json={
            "model": "mock-gpt-4o", "max_tokens": 32,
            "messages": [{"role": "user", "content": "hi"}]})
        check("坏节点自动切换到备用节点", r.status_code == 200, r.text[:200])
        used = r.json().get("model")
        check("切换后仍得到正确响应", r.json().get("type") == "message", str(r.json())[:200])
        await c.delete(f"{BASE}/api/upstreams/{bad['id']}")

        # ---------------------------------------------------- 12. 日志与统计
        print("\n[12] 日志与统计")
        await asyncio.sleep(0.4)
        logs = (await c.get(f"{BASE}/api/logs?limit=10")).json()
        check("日志记录已产生", logs.get("total", 0) > 0, str(logs.get("total")))
        ov = (await c.get(f"{BASE}/api/overview")).json()
        check("统计接口有数据", ov["stats"]["total_requests"] > 0, str(ov["stats"]["total_requests"]))
        check("按上游聚合有数据", len(ov["stats"]["by_upstream"]) > 0)

        # ---------------------------------------------------- 13. 限流
        print("\n[13] 限流")
        limited = (await c.post(f"{BASE}/api/keys", json={
            "name": "限流测试", "rate_limit_rpm": 2})).json()
        lk = {"x-api-key": limited["key"], "anthropic-version": "2023-06-01"}
        codes = []
        for _ in range(4):
            rr = await c.post(f"{BASE}/v1/messages", headers=lk, json={
                "model": "claude-test-oai", "max_tokens": 16,
                "messages": [{"role": "user", "content": "hi"}]})
            codes.append(rr.status_code)
        check("超出速率限制返回 429", 429 in codes, str(codes))
        await c.delete(f"{BASE}/api/keys/{limited['id']}")

        # ---------------------------------------------------- 14. 模型白名单
        print("\n[14] Key 模型白名单")
        wl = (await c.post(f"{BASE}/api/keys", json={
            "name": "白名单测试", "allowed_models": ["only-this-model"]})).json()
        r = await c.post(f"{BASE}/v1/messages",
                         headers={"x-api-key": wl["key"]}, json={
                             "model": "claude-test-oai", "max_tokens": 16,
                             "messages": [{"role": "user", "content": "hi"}]})
        check("白名单外模型被拒绝 403", r.status_code == 403, str(r.status_code))
        await c.delete(f"{BASE}/api/keys/{wl['id']}")

        await c.delete(f"{BASE}/api/keys/{ck['id']}")

    # ------------------------------------------------------------ 汇总
    ok = sum(1 for _, s, _ in results if s)
    total = len(results)
    print("\n" + "=" * 56)
    print(f"  测试结果：{ok}/{total} 通过")
    print("=" * 56)
    if ok != total:
        print("\n未通过的用例：")
        for n, s, d in results:
            if not s:
                print(f"  - {n}: {d}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
