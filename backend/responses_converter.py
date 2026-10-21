"""OpenAI Responses API <-> OpenAI Chat Completions API 双向转换。

Responses API（`/v1/responses`）是 OpenAI 的新一代协议，Codex / OpenAI 新 SDK
以及 OpenCode Go 的部分模型都走这个端点。它与 Chat Completions 的主要差异：

- 输入是 `input` 数组（元素是 message / function_call / function_call_output），
  而不是 `messages`
- `instructions` 单独承载 system 提示词
- 工具调用与结果是**平铺的独立元素**，靠 `call_id` 关联（不是嵌套在 assistant 消息里）
- `function_call.arguments` 是 **JSON 字符串**（Chat 里是对象）
- 响应是 `output` 数组；流式是语义化事件（`response.output_text.delta` 等）
  而不是 `choices[].delta`

本模块与 `converter.py` 保持同样的函数划分：请求 / 响应 / 流式三件套，
方便阅读与对照。
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncIterator

# ---------------------------------------------------------------- 工具函数


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _now_int() -> int:
    return int(time.time())


def _text_of(content: Any) -> str:
    """把 Responses 的 content（str 或 parts 数组）压成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if not isinstance(p, dict):
                continue
            # Responses 用 input_text / output_text；Chat/Anthropic 用 text
            if p.get("type") in ("input_text", "output_text", "text"):
                parts.append(p.get("text", ""))
        return "".join(parts)
    return ""


def _parts_to_text(parts: Any) -> str:
    return _text_of(parts)


# ---------------------------------------------- Responses 请求 -> Chat 请求


def responses_tools_to_chat(tools: list[dict] | None) -> list[dict] | None:
    """Responses 工具定义 → Chat 工具定义。"""
    if not tools:
        return None
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function" and t.get("name"):
            out.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters") or {"type": "object", "properties": {}},
                },
            })
    return out or None


def responses_input_to_chat_messages(items: Any) -> list[dict]:
    """Responses 的 `input` 数组 → Chat 的 `messages` 数组。

    处理三种元素：
    - `{"type":"message", role, content}` → 普通消息
    - `{"type":"function_call", call_id, name, arguments}` → assistant 的 tool_calls
    - `{"type":"function_call_output", call_id, output}` → role=tool 的结果消息
    """
    msgs: list[dict] = []
    if not isinstance(items, list):
        return msgs

    # 连续的 function_call 需要合并到同一条 assistant 消息里（Chat 的形状）
    pending_calls: list[dict] = []

    def flush_calls():
        nonlocal pending_calls
        if pending_calls:
            msgs.append({"role": "assistant", "content": None, "tool_calls": pending_calls})
            pending_calls = []

    for it in items:
        if isinstance(it, str):
            msgs.append({"role": "user", "content": it})
            continue
        if not isinstance(it, dict):
            continue
        itype = it.get("type")

        if itype == "function_call":
            args = it.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except Exception:  # noqa: BLE001
                    args = {"_raw": args}
            if not isinstance(args, dict):
                args = {"_raw": args}
            pending_calls.append({
                "id": it.get("call_id") or it.get("id") or _new_id("call"),
                "type": "function",
                "function": {"name": it.get("name", ""), "arguments": json.dumps(args, ensure_ascii=False)},
            })
            continue

        if itype == "function_call_output":
            flush_calls()
            out = it.get("output")
            if not isinstance(out, str):
                out = json.dumps(out, ensure_ascii=False)
            msgs.append({
                "role": "tool",
                "tool_call_id": it.get("call_id") or "",
                "content": out,
            })
            continue

        # 普通 message（type 可能是 "message" 或缺省）
        flush_calls()
        role = it.get("role") or "user"
        content = it.get("content")
        if isinstance(content, list):
            # 多模态 parts：Responses 的 input_image → Chat 的 image_url
            has_image = any(isinstance(p, dict) and p.get("type") == "input_image" for p in content)
            if has_image:
                parts: list[dict] = []
                for p in content:
                    if not isinstance(p, dict):
                        continue
                    ptype = p.get("type")
                    if ptype in ("input_text", "output_text", "text"):
                        parts.append({"type": "text", "text": p.get("text", "")})
                    elif ptype == "input_image":
                        url = p.get("image_url")
                        if isinstance(url, dict):
                            url = url.get("url")
                        if url:
                            parts.append({"type": "image_url", "image_url": {"url": url}})
                msgs.append({"role": role, "content": parts})
            else:
                # 纯文本：压成字符串（Chat 的常规形状）
                msgs.append({"role": role, "content": _text_of(content)})
        else:
            msgs.append({"role": role, "content": content if content is not None else ""})
    flush_calls()
    return msgs


def responses_request_to_chat(body: dict, model: str) -> dict:
    """Responses 请求体 → Chat Completions 请求体。"""
    msgs: list[dict] = []
    instructions = body.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        msgs.append({"role": "system", "content": instructions})
    msgs.extend(responses_input_to_chat_messages(body.get("input")))

    payload: dict[str, Any] = {
        "model": model,
        "messages": msgs,
        "stream": bool(body.get("stream", False)),
    }
    if body.get("max_output_tokens") is not None:
        payload["max_tokens"] = body["max_output_tokens"]
    if body.get("temperature") is not None:
        payload["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        payload["top_p"] = body["top_p"]

    tools = responses_tools_to_chat(body.get("tools"))
    if tools:
        payload["tools"] = tools
        tc = body.get("tool_choice")
        if isinstance(tc, dict) and tc.get("type") == "function":
            payload["tool_choice"] = {"type": "function", "function": {"name": tc.get("name", "")}}

    if payload["stream"]:
        payload["stream_options"] = {"include_usage": True}
    return payload


# ---------------------------------------------- Chat 请求 -> Responses 请求


def chat_tools_to_responses(tools: list[dict] | None) -> list[dict] | None:
    """Chat 工具定义 → Responses 工具定义。"""
    if not tools:
        return None
    out = []
    for t in tools:
        if not isinstance(t, dict) or t.get("type") != "function":
            continue
        f = t.get("function") or {}
        if not f.get("name"):
            continue
        out.append({
            "type": "function",
            "name": f["name"],
            "description": f.get("description", ""),
            "parameters": f.get("parameters") or {"type": "object", "properties": {}},
        })
    return out or None


def chat_messages_to_responses_input(msgs: list[dict]) -> tuple[list[dict], str]:
    """Chat 的 messages → (Responses 的 input 数组, instructions)。

    system 消息被抽出来放进 `instructions`（Responses 的惯例）。
    """
    input_items: list[dict] = []
    instructions: list[str] = []

    for m in msgs or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")

        if role == "system" or role == "developer":
            text = _text_of(content)
            if text:
                instructions.append(text)
            continue

        if role == "tool":
            input_items.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id", ""),
                "output": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
            })
            continue

        if role == "assistant" and m.get("tool_calls"):
            # 文本部分
            text = _text_of(content)
            if text:
                input_items.append({
                    "type": "message", "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                })
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if not isinstance(args, str):
                    args = json.dumps(args or {}, ensure_ascii=False)
                input_items.append({
                    "type": "function_call",
                    "call_id": tc.get("id") or _new_id("call"),
                    "name": fn.get("name", ""),
                    "arguments": args,
                })
            continue

        # user / assistant 普通消息
        if isinstance(content, list):
            parts = []
            for p in content:
                if not isinstance(p, dict):
                    continue
                if p.get("type") == "text":
                    parts.append({"type": "input_text", "text": p.get("text", "")})
                elif p.get("type") == "image_url":
                    url = p.get("image_url")
                    if isinstance(url, dict):
                        url = url.get("url")
                    if url:
                        parts.append({"type": "input_image", "image_url": url})
            input_items.append({
                "type": "message", "role": role or "user", "content": parts,
            })
        else:
            text = content if isinstance(content, str) else _text_of(content)
            input_items.append({
                "type": "message", "role": role or "user",
                "content": [{"type": "input_text" if role != "assistant" else "output_text",
                             "text": text or ""}],
            })

    return input_items, "\n\n".join(instructions)


def chat_request_to_responses(body: dict, model: str) -> dict:
    """Chat Completions 请求体 → Responses 请求体。"""
    input_items, instructions = chat_messages_to_responses_input(body.get("messages") or [])

    payload: dict[str, Any] = {
        "model": model,
        "input": input_items,
        "stream": bool(body.get("stream", False)),
    }
    if instructions:
        payload["instructions"] = instructions
    if body.get("max_tokens") is not None:
        payload["max_output_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        payload["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        payload["top_p"] = body["top_p"]

    tools = chat_tools_to_responses(body.get("tools"))
    if tools:
        payload["tools"] = tools
    return payload


# ---------------------------------------------- Responses 响应 -> Chat 响应


def responses_response_to_chat(data: dict, model: str, input_tokens: int = 0) -> dict:
    """Responses 响应体 → Chat Completions 响应体。"""
    text_parts: list[str] = []
    tool_calls: list[dict] = []

    for item in (data.get("output") or []):
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "message":
            t = _text_of(item.get("content"))
            if t:
                text_parts.append(t)
        elif itype == "function_call":
            args = item.get("arguments")
            if not isinstance(args, str):
                args = json.dumps(args or {}, ensure_ascii=False)
            tool_calls.append({
                "id": item.get("call_id") or item.get("id") or _new_id("call"),
                "type": "function",
                "function": {"name": item.get("name", ""), "arguments": args},
            })

    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage = data.get("usage") or {}
    out_tokens = usage.get("output_tokens") or 0
    in_tokens = usage.get("input_tokens") or input_tokens

    return {
        "id": data.get("id") or _new_id("chatcmpl"),
        "object": "chat.completion",
        "created": data.get("created") or _now_int(),
        "model": data.get("model") or model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": "tool_calls" if tool_calls else "stop",
        }],
        "usage": {
            "prompt_tokens": in_tokens,
            "completion_tokens": out_tokens,
            "total_tokens": in_tokens + out_tokens,
        },
    }


# ---------------------------------------------- Chat 响应 -> Responses 响应


def chat_response_to_responses(data: dict, model: str, input_tokens: int = 0) -> dict:
    """Chat Completions 响应体 → Responses 响应体。"""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = _text_of(msg.get("content"))

    output: list[dict] = []
    if text:
        output.append({
            "type": "message", "id": _new_id("msg"), "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        })
    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if not isinstance(args, str):
            args = json.dumps(args or {}, ensure_ascii=False)
        output.append({
            "type": "function_call", "id": _new_id("fc"), "call_id": tc.get("id") or _new_id("call"),
            "name": fn.get("name", ""), "arguments": args, "status": "completed",
        })

    usage = data.get("usage") or {}
    in_tokens = usage.get("prompt_tokens") or 0
    out_tokens = usage.get("completion_tokens") or 0

    return {
        "id": data.get("id") or _new_id("resp"),
        "object": "response",
        "created_at": data.get("created") or _now_int(),
        "model": data.get("model") or model,
        "status": "completed",
        "output": output,
        "usage": {
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "total_tokens": in_tokens + out_tokens,
        },
    }


# ---------------------------------------------------------------- 流式转换


def _chat_chunk(chunk_id: str, model: str, delta: dict, finish: str | None = None) -> str:
    """构造一个 Chat Completions 的 SSE chunk。"""
    body = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": _now_int(),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def _sse_event(event: str, data: dict) -> str:
    """构造一个 Responses API 的 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def responses_stream_to_chat_sse(
    lines: AsyncIterator[str],
    model: str,
) -> AsyncIterator[str]:
    """Responses 的 SSE 事件流 → Chat Completions 的 chunk 流。

    处理的事件：
    - `response.output_text.delta` → 文本增量
    - `response.function_call_arguments.delta` → 工具参数增量
    - `response.output_item.added`（type=function_call）→ 开一个 tool_call
    - `response.completed` → 结束（含 usage）
    """
    chunk_id = _new_id("chatcmpl")
    started_calls: set[str] = set()
    usage: dict[str, int] = {}
    call_index = 0

    async for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            evt = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(evt, dict):
            continue

        etype = evt.get("type") or ""
        out = evt.get("output") or []

        if etype == "response.output_text.delta":
            delta = evt.get("delta") or ""
            if delta:
                yield _chat_chunk(chunk_id, model, {"content": delta})
        elif etype == "response.function_call_arguments.delta":
            call_id = evt.get("item_id") or evt.get("call_id") or ""
            delta = evt.get("delta") or ""
            if call_id and call_id not in started_calls:
                started_calls.add(call_id)
                yield _chat_chunk(chunk_id, model, {"tool_calls": [{
                    "index": call_index, "id": call_id, "type": "function",
                    "function": {"name": "", "arguments": ""},
                }]})
                call_index += 1
            if delta:
                yield _chat_chunk(chunk_id, model, {"tool_calls": [{
                    "index": max(0, call_index - 1), "function": {"arguments": delta},
                }]})
        elif etype == "response.output_item.added":
            for item in out:
                if isinstance(item, dict) and item.get("type") == "function_call":
                    cid = item.get("call_id") or item.get("id") or _new_id("call")
                    if cid not in started_calls:
                        started_calls.add(cid)
                        yield _chat_chunk(chunk_id, model, {"tool_calls": [{
                            "index": call_index, "id": cid, "type": "function",
                            "function": {"name": item.get("name", ""), "arguments": ""},
                        }]})
                        call_index += 1
        elif etype in ("response.completed", "response.incomplete", "response.failed"):
            u = evt.get("response", {}).get("usage") or evt.get("usage") or {}
            if u:
                usage = {
                    "prompt_tokens": u.get("input_tokens") or 0,
                    "completion_tokens": u.get("output_tokens") or 0,
                    "total_tokens": u.get("total_tokens") or 0,
                }
            finish = "tool_calls" if started_calls else "stop"
            yield _chat_chunk(chunk_id, model, {}, finish)
            if usage:
                body = {
                    "id": chunk_id, "object": "chat.completion.chunk", "created": _now_int(),
                    "model": model, "choices": [],
                    "usage": usage,
                }
                yield f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

    # 兜底：流没写 completed 也要正常收尾
    yield _chat_chunk(chunk_id, model, {}, "tool_calls" if started_calls else "stop")
    yield "data: [DONE]\n\n"


async def responses_stream_to_anthropic_sse(
    lines: AsyncIterator[str],
    model: str,
    input_tokens: int = 0,
) -> AsyncIterator[str]:
    """Responses 的 SSE 事件流 → Anthropic 事件流。

    Anthropic 事件序列：message_start → content_block_start → content_block_delta*
    → content_block_stop → message_delta → message_stop
    """
    msg_id = _new_id("msg")
    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant", "content": [],
            "model": model, "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        },
    })

    block_index = 0
    text_open = False
    tool_blocks: dict[str, int] = {}
    out_tokens = 0
    finish = "end_turn"

    async for raw in lines:
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            evt = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(evt, dict):
            continue

        etype = evt.get("type") or ""

        if etype == "response.output_text.delta":
            delta = evt.get("delta") or ""
            if delta:
                if not text_open:
                    yield _sse("content_block_start", {
                        "type": "content_block_start", "index": block_index,
                        "content_block": {"type": "text", "text": ""},
                    })
                    text_open = True
                    block_index += 1
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": block_index - 1,
                    "delta": {"type": "text_delta", "text": delta},
                })
        elif etype == "response.output_item.added":
            for item in (evt.get("output") or []):
                if isinstance(item, dict) and item.get("type") == "function_call":
                    cid = item.get("call_id") or item.get("id") or _new_id("call")
                    if cid in tool_blocks:
                        continue
                    if text_open:
                        yield _sse("content_block_stop", {
                            "type": "content_block_stop", "index": block_index - 1})
                        text_open = False
                    idx = block_index
                    tool_blocks[cid] = idx
                    block_index += 1
                    yield _sse("content_block_start", {
                        "type": "content_block_start", "index": idx,
                        "content_block": {"type": "tool_use", "id": cid,
                                          "name": item.get("name", ""), "input": {}},
                    })
        elif etype == "response.function_call_arguments.delta":
            cid = evt.get("item_id") or evt.get("call_id") or ""
            idx = tool_blocks.get(cid)
            if idx is None:
                continue
            delta = evt.get("delta") or ""
            if delta:
                yield _sse("content_block_delta", {
                    "type": "content_block_delta", "index": idx,
                    "delta": {"type": "input_json_delta", "partial_json": delta},
                })
        elif etype in ("response.completed", "response.incomplete", "response.failed"):
            u = (evt.get("response") or {}).get("usage") or evt.get("usage") or {}
            out_tokens = u.get("output_tokens") or out_tokens
            if tool_blocks:
                finish = "tool_use"
            break

    if text_open:
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": block_index - 1})
    for idx in tool_blocks.values():
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": idx})

    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": finish, "stop_sequence": None},
        "usage": {"input_tokens": input_tokens, "output_tokens": out_tokens},
    })
    yield _sse("message_stop", {"type": "message_stop"})


def _sse(event: str, data: dict) -> str:
    """构造一个 Anthropic 风格的 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def anthropic_stream_to_responses_sse(
    lines: AsyncIterator[str],
    model: str,
) -> AsyncIterator[str]:
    """Anthropic 的事件流 → Responses 的 SSE 事件流。"""
    resp_id = _new_id("resp")
    text_buf: list[str] = []
    usage: dict[str, int] = {}

    yield _sse_event("response.created", {
        "type": "response.created",
        "response": {"id": resp_id, "object": "response", "model": model,
                     "status": "in_progress", "output": []},
    })

    async for raw in lines:
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            evt = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(evt, dict):
            continue
        etype = evt.get("type") or ""

        if etype == "content_block_delta":
            delta = evt.get("delta") or {}
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                text_buf.append(delta["text"])
                yield _sse_event("response.output_text.delta", {
                    "type": "response.output_text.delta",
                    "output_index": 0, "content_index": 0, "delta": delta["text"],
                })
        elif etype == "message_start":
            u = (evt.get("message") or {}).get("usage") or {}
            if u:
                usage["input_tokens"] = u.get("input_tokens") or 0
        elif etype == "message_delta":
            u = evt.get("usage") or {}
            if u:
                usage["output_tokens"] = u.get("output_tokens") or usage.get("output_tokens", 0)

    output: list[dict] = []
    if text_buf:
        output.append({
            "type": "message", "id": _new_id("msg"), "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "".join(text_buf), "annotations": []}],
        })
    yield _sse_event("response.completed", {
        "type": "response.completed",
        "response": {"id": resp_id, "object": "response", "model": model, "status": "completed",
                     "output": output, "usage": usage},
    })


async def chat_stream_to_responses_sse(
    lines: AsyncIterator[str],
    model: str,
) -> AsyncIterator[str]:
    """Chat Completions 的 chunk 流 → Responses 的 SSE 事件流。"""
    resp_id = _new_id("resp")
    text_buf: list[str] = []
    calls: dict[int, dict] = {}
    usage: dict[str, int] = {}

    yield _sse_event("response.created", {
        "type": "response.created",
        "response": {"id": resp_id, "object": "response", "model": model,
                     "status": "in_progress", "output": []},
    })
    yield _sse_event("response.in_progress", {
        "type": "response.in_progress",
        "response": {"id": resp_id, "object": "response", "model": model,
                     "status": "in_progress", "output": []},
    })

    async for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line == "[DONE]":
            continue
        try:
            chunk = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(chunk, dict):
            continue

        u = chunk.get("usage")
        if u:
            usage = {
                "input_tokens": u.get("prompt_tokens") or 0,
                "output_tokens": u.get("completion_tokens") or 0,
                "total_tokens": u.get("total_tokens") or 0,
            }
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}

        content = delta.get("content")
        if content:
            text_buf.append(content)
            yield _sse_event("response.output_text.delta", {
                "type": "response.output_text.delta",
                "output_index": 0, "content_index": 0, "delta": content,
            })

        for tc in (delta.get("tool_calls") or []):
            idx = tc.get("index", 0)
            fn = tc.get("function") or {}
            if idx not in calls:
                calls[idx] = {"id": tc.get("id") or _new_id("call"), "name": fn.get("name", ""), "args": ""}
                yield _sse_event("response.output_item.added", {
                    "type": "response.output_item.added", "output_index": idx,
                    "item": {"type": "function_call", "id": calls[idx]["id"],
                             "call_id": calls[idx]["id"], "name": calls[idx]["name"], "arguments": ""},
                })
            if fn.get("name"):
                calls[idx]["name"] = fn["name"]
            if fn.get("arguments"):
                calls[idx]["args"] += fn["arguments"]
                yield _sse_event("response.function_call_arguments.delta", {
                    "type": "response.function_call_arguments.delta", "output_index": idx,
                    "item_id": calls[idx]["id"], "delta": fn["arguments"],
                })

    output: list[dict] = []
    if text_buf:
        output.append({
            "type": "message", "id": _new_id("msg"), "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "".join(text_buf), "annotations": []}],
        })
    for idx, c in sorted(calls.items()):
        output.append({
            "type": "function_call", "id": _new_id("fc"), "call_id": c["id"],
            "name": c["name"], "arguments": c["args"], "status": "completed",
        })

    yield _sse_event("response.output_item.done", {
        "type": "response.output_item.done", "output_index": 0,
        "item": output[0] if output else {"type": "message", "content": []},
    })
    yield _sse_event("response.completed", {
        "type": "response.completed",
        "response": {"id": resp_id, "object": "response", "model": model, "status": "completed",
                     "output": output, "usage": usage},
    })
