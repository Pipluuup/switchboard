"""协议转换层单元测试（纯函数，无需启动服务）。

运行：python tests/test_converter.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import converter as cv  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond else ""))


async def alines(items):
    for x in items:
        yield x


async def collect(agen):
    return [x async for x in agen]


def parse(ev: str):
    """从 'event: X\\ndata: {...}\\n\\n' 解析出 (事件名, 数据)。"""
    name, data = "", None
    for line in ev.splitlines():
        if line.startswith("event: "):
            name = line[7:].strip()
        elif line.startswith("data: "):
            try:
                data = json.loads(line[6:])
            except Exception:
                data = None
    return name, data


async def main():
    # ---------------------------------------------------------- 请求：Anthropic -> OpenAI
    print("\n[1] 请求转换 Anthropic -> OpenAI")
    body = {
        "model": "alias", "max_tokens": 100, "temperature": 0.3,
        "system": "你是助手",
        "messages": [{"role": "user", "content": "你好"}],
        "tools": [{"name": "get_weather", "description": "查天气",
                   "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}],
        "tool_choice": {"type": "tool", "name": "get_weather"},
    }
    out = cv.anthropic_request_to_openai(body, "real-model")
    check("model 被替换为目标模型", out["model"] == "real-model", str(out["model"]))
    check("system 转为 system 消息",
          out["messages"][0] == {"role": "system", "content": "你是助手"}, str(out["messages"][0]))
    check("user 文本保持", out["messages"][1]["content"] == "你好")
    check("tools 转为 function 结构",
          out["tools"][0]["function"]["name"] == "get_weather" and
          out["tools"][0]["function"]["parameters"]["type"] == "object")
    check("tool_choice=tool 转为指定函数",
          out["tool_choice"] == {"type": "function", "function": {"name": "get_weather"}},
          str(out["tool_choice"]))

    # tool_choice 必须与 tools 配套出现（OpenAI 要求），单独给 tool_choice 应被忽略
    out2 = cv.anthropic_request_to_openai(
        {"messages": [], "tools": [{"name": "f", "input_schema": {}}],
         "tool_choice": {"type": "any"}}, "m")
    check("tool_choice=any 转为 required", out2.get("tool_choice") == "required", str(out2.get("tool_choice")))
    out3 = cv.anthropic_request_to_openai({"messages": [], "tool_choice": {"type": "any"}}, "m")
    check("无 tools 时忽略 tool_choice", "tool_choice" not in out3, str(out3))

    # ---------------------------------------------------------- 多轮工具结果
    print("\n[2] 多轮工具结果与 tool_result 转换")
    msgs = [
        {"role": "user", "content": "北京天气"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "我来查一下"},
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "北京"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "晴天 25℃"}]},
    ]
    out = cv.anthropic_messages_to_openai(msgs)
    check("assistant 文本与工具调用合并为一条",
          out[1]["role"] == "assistant" and out[1]["content"] == "我来查一下" and len(out[1]["tool_calls"]) == 1,
          str(out[1]))
    check("tool_use 参数序列化为 JSON 字符串",
          json.loads(out[1]["tool_calls"][0]["function"]["arguments"]) == {"city": "北京"},
          out[1]["tool_calls"][0]["function"]["arguments"])
    check("tool_result 转为 role=tool 且 id 对应",
          out[2]["role"] == "tool" and out[2]["tool_call_id"] == "toolu_1" and out[2]["content"] == "晴天 25℃",
          str(out[2]))

    # ---------------------------------------------------------- 响应：OpenAI -> Anthropic
    print("\n[3] 响应转换 OpenAI -> Anthropic")
    oai = {
        "id": "c1",
        "choices": [{"message": {
            "content": "你好",
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "f", "arguments": '{"a":1}'}}]},
            "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7},
    }
    out = cv.openai_response_to_anthropic(oai, "m")
    check("type 为 message", out["type"] == "message")
    check("文本块在前", out["content"][0] == {"type": "text", "text": "你好"}, str(out["content"][0]))
    check("tool_use 块参数解析为 dict",
          out["content"][1]["type"] == "tool_use" and out["content"][1]["input"] == {"a": 1},
          str(out["content"][1]))
    check("stop_reason 映射 tool_use", out["stop_reason"] == "tool_use", str(out["stop_reason"]))
    check("usage 字段映射", out["usage"] == {"input_tokens": 5, "output_tokens": 7}, str(out["usage"]))

    # ---- 非法 JSON 参数不应崩溃
    oai_bad = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "c2", "function": {"name": "f", "arguments": "not-json"}}]}, "finish_reason": "tool_calls"}]}
    out_bad = cv.openai_response_to_anthropic(oai_bad, "m")
    check("非法 arguments 回退为空对象不报错",
          out_bad["content"][0]["input"] == {}, str(out_bad["content"][0]))

    # ---------------------------------------------------------- 流：OpenAI -> Anthropic
    print("\n[4] 流式转换 OpenAI -> Anthropic")
    lines = [
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_9","function":{"name":"f","arguments":""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"a\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"1}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":3,"completion_tokens":9}}',
        "data: [DONE]",
    ]
    events = [parse(e) for e in await collect(cv.openai_stream_to_anthropic_sse(alines(lines), "m", 3))]
    names = [n for n, _ in events]
    check("首事件 message_start", names[0] == "message_start", str(names[:2]))
    check("末事件 message_stop", names[-1] == "message_stop", str(names[-2:]))
    check("文本块与工具块各有 start/stop",
          names.count("content_block_start") == 2 and names.count("content_block_stop") == 2, str(names))

    starts = [d for n, d in events if n == "content_block_start"]
    check("文本块 index=0，工具块 index=1",
          starts[0]["index"] == 0 and starts[1]["index"] == 1 and
          starts[1]["content_block"]["type"] == "tool_use", str([s["index"] for s in starts]))

    text = "".join(d["delta"]["text"] for n, d in events
                   if n == "content_block_delta" and d["delta"].get("type") == "text_delta")
    check("文本增量拼接完整", text == "你好", repr(text))

    pj = "".join(d["delta"].get("partial_json", "") for n, d in events
                 if n == "content_block_delta" and d["delta"].get("type") == "input_json_delta")
    check("工具参数增量拼接为合法 JSON", pj == '{"a":1}', repr(pj))

    md = [d for n, d in events if n == "message_delta"][-1]
    check("message_delta 带 stop_reason=tool_use", md["delta"]["stop_reason"] == "tool_use", str(md))
    check("message_delta 带输出 token", md["usage"]["output_tokens"] == 9, str(md))

    # ---- 空响应不应缺块
    empty = await collect(cv.openai_stream_to_anthropic_sse(alines(["data: [DONE]"]), "m", 1))
    en = [n for n, _ in (parse(e) for e in empty)]
    check("空响应仍补齐空文本块", "content_block_start" in en and "message_stop" in en, str(en))

    # ---------------------------------------------------------- 请求：OpenAI -> Anthropic
    print("\n[5] 请求转换 OpenAI -> Anthropic")
    body = {
        "model": "m", "max_tokens": 50,
        "messages": [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "北京天气"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "function": {"name": "f", "arguments": '{"city":"北京"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "晴天"},
        ],
        "tools": [{"type": "function", "function": {"name": "f", "description": "d",
                                                    "parameters": {"type": "object"}}}],
    }
    system, msgs = cv.openai_messages_to_anthropic(body["messages"])
    check("system 被提取", system == "你是助手", system)
    check("assistant 的 tool_calls 转为 tool_use 块",
          msgs[1]["content"][0]["type"] == "tool_use" and msgs[1]["content"][0]["input"] == {"city": "北京"},
          str(msgs[1]))
    check("role=tool 合并为 user 的 tool_result",
          msgs[2]["role"] == "user" and msgs[2]["content"][0]["type"] == "tool_result" and
          msgs[2]["content"][0]["tool_use_id"] == "call_1", str(msgs[2]))
    payload = cv.openai_request_to_anthropic(body, "m")
    check("tools 转为 Anthropic 结构",
          payload["tools"][0]["name"] == "f" and payload["tools"][0]["input_schema"]["type"] == "object")

    # ---------------------------------------------------------- 响应/流：Anthropic -> OpenAI
    print("\n[6] 反向转换 Anthropic -> OpenAI")
    anth = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
        "content": [{"type": "text", "text": "你好"},
                    {"type": "tool_use", "id": "toolu_1", "name": "f", "input": {"a": 1}}],
        "stop_reason": "tool_use", "usage": {"input_tokens": 4, "output_tokens": 6},
    }
    out = cv.anthropic_response_to_openai(anth, "m")
    check("object 为 chat.completion", out["object"] == "chat.completion")
    check("tool_calls 参数序列化",
          json.loads(out["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]) == {"a": 1})
    check("finish_reason 映射 tool_calls",
          out["choices"][0]["finish_reason"] == "tool_calls", str(out["choices"][0]["finish_reason"]))
    check("usage 合并为 total",
          out["usage"]["total_tokens"] == 10, str(out["usage"]))

    alines_in = [
        'data: {"type":"message_start","message":{"usage":{"input_tokens":4,"output_tokens":1}}}',
        'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"你"}}',
        'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"t1","name":"f","input":{}}}',
        'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"a\\":1}"}}',
        'data: {"type":"content_block_stop","index":1}',
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":6}}',
        'data: {"type":"message_stop"}',
    ]
    chunks = await collect(cv.anthropic_stream_to_openai_sse(alines(alines_in), "m"))
    joined = "".join(chunks)
    check("反向流式以 [DONE] 结束", joined.rstrip().endswith("[DONE]"), joined[-40:])
    check("反向流式输出 chat.completion.chunk", "chat.completion.chunk" in joined)
    check("反向流式工具调用带函数名", '"name": "f"' in joined or '"name":"f"' in joined)
    check("反向流式 stop_reason 为 tool_calls", '"finish_reason": "tool_calls"' in joined or '"finish_reason":"tool_calls"' in joined)

    # ---------------------------------------------------------- 图片
    print("\n[7] 图片内容转换")
    img_msg = [{"role": "user", "content": [
        {"type": "text", "text": "看图"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAABBBB"}}]}]
    parts, _, _ = cv.anthropic_content_to_openai_parts(img_msg[0]["content"])
    check("base64 图片转 data URL",
          any(p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:image/png;base64,")
              for p in parts), str(parts))

    ok = sum(1 for _, s, _ in results if s)
    print("\n" + "=" * 56)
    print(f"  转换层测试：{ok}/{len(results)} 通过")
    print("=" * 56)
    if ok != len(results):
        for n, s, d in results:
            if not s:
                print(f"  - {n}: {d}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
