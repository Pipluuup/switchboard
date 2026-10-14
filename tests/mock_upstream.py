"""模拟上游服务，用于端到端验证协议转换是否正确。

同时提供 OpenAI 与 Anthropic 两种协议端点，均支持流式与非流式、工具调用。
启动：python tests/mock_upstream.py --port 8100
"""
from __future__ import annotations

import argparse
import json
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

# 通过环境变量或全局变量控制是否返回工具调用
RETURN_TOOL = True


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [
        {"id": "mock-gpt-4o", "object": "model", "created": int(time.time()), "owned_by": "mock"},
        {"id": "mock-claude", "object": "model", "created": int(time.time()), "owned_by": "mock"},
    ]}


@app.get("/models")
async def models_no_v1():
    return await models()


# ---------------------------------------------------------------- OpenAI 协议
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    stream = body.get("stream", False)
    has_tools = bool(body.get("tools"))

    if stream:
        async def gen():
            if has_tools and RETURN_TOOL:
                yield "data: " + json.dumps({"choices": [{"delta": {"content": "我来查一下"}, "index": 0}], "id": "c1", "model": body.get("model")}) + "\n\n"
                yield "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_abc", "type": "function", "function": {"name": "get_weather", "arguments": ""}}]}, "index": 0}]}) + "\n\n"
                yield "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"city\":"}}]}, "index": 0}]}) + "\n\n"
                yield "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"北京\"}"}}]}, "index": 0}]}) + "\n\n"
                yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls", "index": 0}], "usage": {"prompt_tokens": 42, "completion_tokens": 18}}) + "\n\n"
            else:
                for ch in ["你好", "，这是", "模拟上游"]:
                    yield "data: " + json.dumps({"choices": [{"delta": {"content": ch}, "index": 0}]}) + "\n\n"
                yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}], "usage": {"prompt_tokens": 15, "completion_tokens": 9}}) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    if has_tools and RETURN_TOOL:
        msg = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_abc", "type": "function",
             "function": {"name": "get_weather", "arguments": json.dumps({"city": "北京"})}}]}
        finish = "tool_calls"
    else:
        msg = {"role": "assistant", "content": "你好，这是模拟上游"}
        finish = "stop"
    return JSONResponse({
        "id": "chatcmpl-mock", "object": "chat.completion", "created": int(time.time()),
        "model": body.get("model"),
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": {"prompt_tokens": 15, "completion_tokens": 9, "total_tokens": 24},
    })


# ---------------------------------------------------------------- Anthropic 协议
@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    stream = body.get("stream", False)
    has_tools = bool(body.get("tools"))
    msg_id = "msg_mock_001"

    if stream:
        async def gen():
            def sse(ev, data):
                return f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            yield sse("message_start", {"type": "message_start", "message": {
                "id": msg_id, "type": "message", "role": "assistant", "content": [],
                "model": body.get("model"), "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 33, "output_tokens": 1}}})
            yield sse("ping", {"type": "ping"})
            idx = 0
            if has_tools and RETURN_TOOL:
                yield sse("content_block_start", {"type": "content_block_start", "index": idx,
                                                  "content_block": {"type": "tool_use", "id": "toolu_1",
                                                                   "name": "get_weather", "input": {}}})
                for frag in ['{"city":', '"上海"}']:
                    yield sse("content_block_delta", {"type": "content_block_delta", "index": idx,
                                                      "delta": {"type": "input_json_delta", "partial_json": frag}})
                yield sse("content_block_stop", {"type": "content_block_stop", "index": idx})
                stop = "tool_use"
            else:
                yield sse("content_block_start", {"type": "content_block_start", "index": idx,
                                                  "content_block": {"type": "text", "text": ""}})
                for ch in ["你好", "，来自", "Anthropic 模拟上游"]:
                    yield sse("content_block_delta", {"type": "content_block_delta", "index": idx,
                                                      "delta": {"type": "text_delta", "text": ch}})
                yield sse("content_block_stop", {"type": "content_block_stop", "index": idx})
                stop = "end_turn"
            yield sse("message_delta", {"type": "message_delta",
                                        "delta": {"stop_reason": stop, "stop_sequence": None},
                                        "usage": {"output_tokens": 21}})
            yield sse("message_stop", {"type": "message_stop"})

        return StreamingResponse(gen(), media_type="text/event-stream")

    if has_tools and RETURN_TOOL:
        content = [{"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "上海"}}]
        stop = "tool_use"
    else:
        content = [{"type": "text", "text": "你好，来自 Anthropic 模拟上游"}]
        stop = "end_turn"
    return JSONResponse({
        "id": msg_id, "type": "message", "role": "assistant", "model": body.get("model"),
        "content": content, "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 33, "output_tokens": 21},
    })


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8100)
    a = p.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
