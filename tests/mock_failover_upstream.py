"""可编程的故障模拟上游，用于验证故障转移链。

每个模型名决定这个上游的行为：
- 模型名以 `fail500` / `fail429` / `fail404` / `failnet` 开头 → 返回对应故障
- 其他模型名 → 正常返回

这样一条链「A(fail500) → B(fail404) → C(正常)」就能被完整验证：
每档的失败原因不同，且 C 上能拿到正确响应。

启动：python tests/mock_failover_upstream.py --port 8110
"""
from __future__ import annotations

import argparse
import json
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

# 被请求过的模型（用于测试断言「走到了哪一档」）
HITS: list[str] = []


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [
        {"id": "ok-model", "object": "model", "created": int(time.time()), "owned_by": "mock"},
    ]}


@app.get("/models")
async def models_no_v1():
    return await models()


def _model_of(body: dict) -> str:
    m = str(body.get("model") or "")
    HITS.append(m)
    return m


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = _model_of(body)
    stream = bool(body.get("stream", False))

    if model.startswith("fail500"):
        return JSONResponse({"error": {"message": "upstream exploded",
                                      "type": "server_error"}}, status_code=500)
    if model.startswith("fail429"):
        return JSONResponse({"error": {"message": "rate limited",
                                      "type": "rate_limit_error"}}, status_code=429)
    if model.startswith("fail404"):
        return JSONResponse({"error": {"message": f"model {model} not found",
                                      "type": "invalid_request_error"}}, status_code=404)
    if model.startswith("fail400"):
        return JSONResponse({"error": {"message": "bad request",
                                      "type": "invalid_request_error"}}, status_code=400)
    if model.startswith("fail401"):
        return JSONResponse({"error": {"message": "invalid api key",
                                      "type": "authentication_error"}}, status_code=401)

    if stream:
        async def gen():
            yield "data: " + json.dumps(
                {"choices": [{"delta": {"content": "ok"}, "index": 0}],
                 "model": model}) + "\n\n"
            yield "data: " + json.dumps(
                {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}],
                 "usage": {"prompt_tokens": 7, "completion_tokens": 3}}) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    return JSONResponse({
        "id": "chatcmpl-fo", "object": "chat.completion", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": f"reached:{model}"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
    })


@app.post("/v1/messages")
async def messages(request: Request):
    """Anthropic 协议端点，行为与 chat 端点一致。"""
    body = await request.json()
    model = _model_of(body)
    stream = bool(body.get("stream", False))

    if model.startswith("fail500"):
        return JSONResponse({"type": "error", "error": {"type": "api_error",
                                      "message": "upstream exploded"}}, status_code=500)
    if model.startswith("fail429"):
        return JSONResponse({"type": "error", "error": {"type": "rate_limit_error",
                                      "message": "rate limited"}}, status_code=429)
    if model.startswith("fail404"):
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error",
                                      "message": f"model {model} not found"}}, status_code=404)
    if model.startswith("fail400"):
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error",
                                      "message": "bad request"}}, status_code=400)
    if model.startswith("fail401"):
        return JSONResponse({"type": "error", "error": {"type": "authentication_error",
                                      "message": "invalid api key"}}, status_code=401)

    if stream:
        async def gen():
            def sse(ev, data):
                return f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            yield sse("message_start", {"type": "message_start", "message": {
                "id": "msg_fo", "type": "message", "role": "assistant", "content": [],
                "model": model, "stop_reason": None,
                "usage": {"input_tokens": 9, "output_tokens": 1}}})
            yield sse("content_block_start", {"type": "content_block_start", "index": 0,
                                              "content_block": {"type": "text", "text": ""}})
            yield sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                                              "delta": {"type": "text_delta",
                                                        "text": f"reached:{model}"}})
            yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield sse("message_delta", {"type": "message_delta",
                                        "delta": {"stop_reason": "end_turn"},
                                        "usage": {"output_tokens": 4}})
            yield sse("message_stop", {"type": "message_stop"})

        return StreamingResponse(gen(), media_type="text/event-stream")

    return JSONResponse({
        "id": "msg_fo", "type": "message", "role": "assistant", "model": model,
        "content": [{"type": "text", "text": f"reached:{model}"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 9, "output_tokens": 4},
    })


@app.get("/_hits")
async def hits():
    """测试用：返回被请求过的模型序列。"""
    return {"hits": HITS}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8110)
    a = p.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
