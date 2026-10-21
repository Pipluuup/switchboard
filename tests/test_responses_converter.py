"""Responses API 转换层单元测试（纯函数，无需启动服务）。

运行：python tests/test_responses_converter.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import responses_converter as rc  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond else ""))


async def alines(items):
    for x in items:
        yield x


async def collect(agen):
    return [x async for x in agen]


def test_request_text():
    body = {
        "model": "gpt-x",
        "instructions": "你是助手",
        "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "你好"}]},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "在"}]},
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "继续"}]},
        ],
        "max_output_tokens": 100,
    }
    out = rc.responses_request_to_chat(body, "m1")
    check("instructions → system", out["messages"][0] == {"role": "system", "content": "你是助手"},
          str(out["messages"][0]))
    check("input → messages 3 条", len(out["messages"]) == 4, str(len(out["messages"])))
    check("user 文本正确", out["messages"][1]["content"] == "你好", str(out["messages"][1]))
    check("assistant 文本正确", out["messages"][2]["content"] == "在", str(out["messages"][2]))
    check("max_output_tokens → max_tokens", out.get("max_tokens") == 100, str(out.get("max_tokens")))


def test_request_tools():
    body = {
        "model": "gpt-x",
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": "查天气"}]}],
        "tools": [{"type": "function", "name": "get_weather",
                   "description": "查天气",
                   "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}],
    }
    out = rc.responses_request_to_chat(body, "m1")
    t = out["tools"][0]
    check("工具 → chat function 形状",
          t["type"] == "function" and t["function"]["name"] == "get_weather"
          and t["function"]["parameters"]["properties"]["city"]["type"] == "string",
          json.dumps(t, ensure_ascii=False))


def test_request_function_call_roundtrip():
    """function_call / function_call_output 必须保留 call_id 关联。"""
    body = {
        "model": "gpt-x",
        "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "查天气"}]},
            {"type": "function_call", "call_id": "call_abc", "name": "get_weather",
             "arguments": '{"city":"北京"}'},
            {"type": "function_call_output", "call_id": "call_abc", "output": "晴 25度"},
        ],
    }
    out = rc.responses_request_to_chat(body, "m1")
    assistant = [m for m in out["messages"] if m.get("tool_calls")]
    tool = [m for m in out["messages"] if m.get("role") == "tool"]
    check("function_call → assistant.tool_calls", len(assistant) == 1, str(out["messages"]))
    check("tool_call id 保留",
          assistant and assistant[0]["tool_calls"][0]["id"] == "call_abc",
          str(assistant[0]["tool_calls"][0]["id"]) if assistant else "无")
    check("arguments JSON 字符串化",
          assistant and assistant[0]["tool_calls"][0]["function"]["arguments"] == '{"city": "北京"}',
          assistant[0]["tool_calls"][0]["function"]["arguments"] if assistant else "无")
    check("function_call_output → role=tool", len(tool) == 1, str(tool))
    check("tool_call_id 关联保留",
          tool and tool[0]["tool_call_id"] == "call_abc", str(tool[0]) if tool else "无")


def test_chat_to_responses():
    body = {
        "model": "gpt-x",
        "messages": [
            {"role": "system", "content": "系统提示"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "f", "arguments": '{"a":1}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "结果"},
        ],
        "max_tokens": 50,
    }
    out = rc.chat_request_to_responses(body, "m1")
    check("system → instructions", out.get("instructions") == "系统提示", str(out.get("instructions")))
    check("user → input message", out["input"][0]["role"] == "user", str(out["input"][0]))
    check("function_call → 独立元素",
          out["input"][1]["type"] == "function_call" and out["input"][1]["call_id"] == "call_1",
          str(out["input"][1]))
    check("function_call_output → 独立元素",
          out["input"][2]["type"] == "function_call_output" and out["input"][2]["call_id"] == "call_1",
          str(out["input"][2]))
    check("max_tokens → max_output_tokens", out.get("max_output_tokens") == 50, str(out.get("max_output_tokens")))


def test_response_to_chat():
    data = {
        "id": "resp_1", "object": "response", "model": "gpt-x",
        "output": [
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "你好呀"}]},
            {"type": "function_call", "call_id": "call_z", "name": "f", "arguments": '{"x":2}'},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    out = rc.responses_response_to_chat(data, "m1")
    ch = out["choices"][0]
    check("文本提取", ch["message"]["content"] == "你好呀", str(ch["message"]))
    check("function_call → tool_calls",
          ch["message"]["tool_calls"][0]["id"] == "call_z"
          and ch["message"]["tool_calls"][0]["function"]["name"] == "f",
          str(ch["message"].get("tool_calls")))
    check("finish_reason=tool_calls", ch["finish_reason"] == "tool_calls", ch["finish_reason"])
    check("usage 映射", out["usage"]["prompt_tokens"] == 10 and out["usage"]["completion_tokens"] == 5,
          str(out["usage"]))


def test_chat_to_responses_response():
    data = {
        "id": "chatcmpl_1", "object": "chat.completion", "model": "gpt-x",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "答案"}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    out = rc.chat_response_to_responses(data, "m1")
    check("object=response", out["object"] == "response", out["object"])
    check("output message 文本",
          out["output"][0]["content"][0]["text"] == "答案", str(out["output"][0]))
    check("usage 映射",
          out["usage"]["input_tokens"] == 3 and out["usage"]["output_tokens"] == 2, str(out["usage"]))


def test_stream_responses_to_chat():
    # 上游 SSE 按行送入（event: 行会被跳过，data: 行自带 type 字段）
    sse = [
        'data: {"type":"response.created","response":{"id":"r1"}}',
        'data: {"type":"response.output_text.delta","delta":"你"}',
        'data: {"type":"response.output_text.delta","delta":"好"}',
        'data: {"type":"response.completed","response":{"usage":{"input_tokens":7,"output_tokens":2}}}',
    ]
    out = asyncio_run(rc.responses_stream_to_chat_sse(alines(sse), "m1"))
    text = "".join(
        json.loads(l[5:].strip())["choices"][0]["delta"].get("content", "")
        for l in out if l.startswith("data:") and l[5:].strip() not in ("[DONE]",)
        and json.loads(l[5:].strip()).get("choices")
    )
    check("流式文本拼接", text == "你好", repr(text))
    check("流式以 [DONE] 收尾", out[-1].strip() == "data: [DONE]", repr(out[-1]))
    check("流式带 usage",
          any("usage" in l for l in out), "无 usage 块")


def test_stream_chat_to_responses():
    sse = [
        'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"你"}}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"好"}}]}',
        'data: {"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":2}}',
        "data: [DONE]",
    ]
    out = asyncio_run(rc.chat_stream_to_responses_sse(alines(sse), "m1"))
    joined = "".join(out)
    check("流式含 output_text.delta", "response.output_text.delta" in joined, "缺")
    check("流式含 response.completed", "response.completed" in joined, "缺")
    deltas = []
    for l in out:
        for line in l.splitlines():
            if line.startswith("data: ") and "output_text.delta" in line:
                deltas.append(json.loads(line[6:])["delta"])
    check("流式文本拼接", "".join(deltas) == "你好", repr("".join(deltas)))


def asyncio_run(agen):
    """跑异步生成器并收集全部输出。"""
    import asyncio

    async def _drive():
        return [x async for x in agen]

    return asyncio.run(_drive())


if __name__ == "__main__":
    print("Responses 协议转换测试：")
    test_request_text()
    test_request_tools()
    test_request_function_call_roundtrip()
    test_chat_to_responses()
    test_response_to_chat()
    test_chat_to_responses_response()
    test_stream_responses_to_chat()
    test_stream_chat_to_responses()

    failed = [r for r in results if not r[1]]
    print(f"\n通过 {len(results) - len(failed)}/{len(results)}")
    if failed:
        for name, _ok, detail in failed:
            print(f"  FAIL: {name} -> {detail}")
        sys.exit(1)
