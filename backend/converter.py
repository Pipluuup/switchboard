"""Anthropic Messages API <-> OpenAI Chat Completions API 双向转换。

设计目标：让 Claude Code CLI（Anthropic 协议）能接到任意 OpenAI 格式中转，
同时也能让 OpenAI 协议的客户端接到 Anthropic 上游。工具调用必须完整支持，
否则 Claude Code 无法工作。
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
    """把 Anthropic content（str 或 blocks）压成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        return "\n".join(parts)
    return ""


# ---------------------------------------------- Anthropic 请求 -> OpenAI 请求


def anthropic_tools_to_openai(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    out = []
    for t in tools:
        out.append({
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    return out


def anthropic_tool_choice_to_openai(tc: Any) -> Any:
    if tc is None:
        return None
    if isinstance(tc, str):
        return {"auto": "auto", "any": "required", "none": "none"}.get(tc, "auto")
    t = tc.get("type")
    if t == "auto":
        return "auto"
    if t == "any":
        return "required"
    if t == "tool":
        return {"type": "function", "function": {"name": tc.get("name", "")}}
    return "auto"


def anthropic_content_to_openai_parts(content: Any) -> tuple[list[dict], list[dict], list[dict]]:
    """返回 (文本/图片 parts, tool_calls, tool_results)。"""
    text_parts: list[dict] = []
    tool_calls: list[dict] = []
    tool_results: list[dict] = []

    if isinstance(content, str):
        if content:
            text_parts.append({"type": "text", "text": content})
        return text_parts, tool_calls, tool_results

    if not isinstance(content, list):
        return text_parts, tool_calls, tool_results

    for b in content:
        if not isinstance(b, dict):
            continue
        btype = b.get("type")
        if btype == "text":
            if b.get("text"):
                text_parts.append({"type": "text", "text": b["text"]})
        elif btype == "image":
            src = b.get("source", {}) or {}
            if src.get("type") == "base64":
                mt = src.get("media_type", "image/png")
                text_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mt};base64,{src.get('data', '')}"},
                })
            elif src.get("type") == "url":
                text_parts.append({"type": "image_url", "image_url": {"url": src.get("url", "")}})
        elif btype == "tool_use":
            tool_calls.append({
                "id": b.get("id") or _new_id("call"),
                "type": "function",
                "function": {
                    "name": b.get("name", ""),
                    "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False),
                },
            })
        elif btype == "tool_result":
            inner = b.get("content")
            if isinstance(inner, list):
                inner = _text_of(inner)
            if inner is None:
                inner = ""
            tool_results.append({
                "tool_call_id": b.get("tool_use_id", ""),
                "content": inner if isinstance(inner, str) else json.dumps(inner, ensure_ascii=False),
            })
    return text_parts, tool_calls, tool_results


def anthropic_messages_to_openai(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages or []:
        role = m.get("role", "user")
        content = m.get("content")
        if role == "system":
            continue
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        parts, tool_calls, tool_results = anthropic_content_to_openai_parts(content)

        if role == "assistant":
            msg: dict[str, Any] = {"role": "assistant"}
            # 只要含图片就保留数组结构，否则合并成字符串，兼容性最好
            if any(p.get("type") == "image_url" for p in parts):
                msg["content"] = parts or None
            else:
                msg["content"] = "\n".join(p.get("text", "") for p in parts) or None
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        else:
            for tr in tool_results:
                out.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["content"],
                })
            if parts:
                if any(p.get("type") == "image_url" for p in parts):
                    out.append({"role": "user", "content": parts})
                else:
                    out.append({"role": "user", "content": "\n".join(p.get("text", "") for p in parts)})
    return out


def anthropic_request_to_openai(body: dict, model: str) -> dict:
    system = body.get("system")
    if isinstance(system, list):
        system = "\n\n".join(_text_of(b) if isinstance(b, dict) else str(b) for b in system)

    oai_msgs: list[dict] = []
    if system:
        oai_msgs.append({"role": "system", "content": system})
    oai_msgs.extend(anthropic_messages_to_openai(body.get("messages", [])))

    payload: dict[str, Any] = {
        "model": model,
        "messages": oai_msgs,
        "stream": bool(body.get("stream", False)),
    }
    if body.get("max_tokens") is not None:
        payload["max_tokens"] = body["max_tokens"]
    if body.get("temperature") is not None:
        payload["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        payload["top_p"] = body["top_p"]
    if body.get("stop_sequences"):
        payload["stop"] = body["stop_sequences"]

    tools = anthropic_tools_to_openai(body.get("tools"))
    if tools:
        payload["tools"] = tools
        tc = anthropic_tool_choice_to_openai(body.get("tool_choice"))
        if tc is not None:
            payload["tool_choice"] = tc

    if payload["stream"]:
        payload["stream_options"] = {"include_usage": True}
    return payload


# ---------------------------------------------- OpenAI 响应 -> Anthropic 响应


def _oai_finish_to_anthropic_stop(finish: str | None) -> str:
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
        "content_filter": "stop_sequence",
    }.get(finish or "", "end_turn")


def openai_response_to_anthropic(data: dict, model: str, input_tokens: int = 0) -> dict:
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content_blocks: list[dict] = []

    text = msg.get("content")
    if text:
        content_blocks.append({"type": "text", "text": text})

    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args)
        except Exception:  # noqa: BLE001
            args = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id") or _new_id("toolu"),
            "name": fn.get("name", ""),
            "input": args,
        })

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    usage = data.get("usage") or {}
    return {
        "id": data.get("id") or _new_id("msg"),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content_blocks,
        "stop_reason": _oai_finish_to_anthropic_stop(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", input_tokens),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def openai_stream_to_anthropic_sse(
    lines: AsyncIterator[str], model: str, input_tokens: int = 0
) -> AsyncIterator[str]:
    """把 OpenAI SSE 流转成 Anthropic 事件流。

    Anthropic 事件序列：message_start -> content_block_start -> (ping)
    -> content_block_delta* -> content_block_stop -> message_delta -> message_stop
    """
    msg_id = _new_id("msg")
    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0},
        },
    })

    block_index = 0
    text_block_open = False
    tool_block_map: dict[int, int] = {}   # openai tool_call index -> anthropic block index
    tool_args_buf: dict[int, str] = {}
    tool_meta: dict[int, dict] = {}
    stop_reason = "end_turn"
    out_tokens = 0
    in_tokens = input_tokens

    def close_text():
        nonlocal text_block_open
        if text_block_open:
            text_block_open = False
            return _sse("content_block_stop", {"type": "content_block_stop", "index": block_index - 1})
        return ""

    async for line in lines:
        if not line:
            continue
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except Exception:  # noqa: BLE001
            continue

        if chunk.get("usage"):
            u = chunk["usage"]
            if u.get("prompt_tokens"):
                in_tokens = u["prompt_tokens"]
            if u.get("completion_tokens"):
                out_tokens = u["completion_tokens"]

        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        finish = choices[0].get("finish_reason")

        # ---- 文本 ----
        dtext = delta.get("content")
        if dtext:
            if not text_block_open:
                tail = close_text()
                if tail:
                    yield tail
                yield _sse("content_block_start", {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {"type": "text", "text": ""},
                })
                text_block_open = True
                block_index += 1
            yield _sse("content_block_delta", {
                "type": "content_block_delta",
                "index": block_index - 1,
                "delta": {"type": "text_delta", "text": dtext},
            })

        # ---- 工具调用 ----
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            if idx not in tool_block_map:
                tail = close_text()
                if tail:
                    yield tail
                fn = tc.get("function") or {}
                tool_meta[idx] = {"id": tc.get("id") or _new_id("toolu"), "name": fn.get("name", "")}
                tool_args_buf[idx] = ""
                yield _sse("content_block_start", {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": tool_meta[idx]["id"],
                        "name": tool_meta[idx]["name"],
                        "input": {},
                    },
                })
                tool_block_map[idx] = block_index
                block_index += 1

            frag = (tc.get("function") or {}).get("arguments")
            if frag:
                a_idx = tool_block_map[idx]
                tool_args_buf[idx] += frag
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": a_idx,
                    "delta": {"type": "input_json_delta", "partial_json": frag},
                })

            if tc.get("id") and idx in tool_block_map and not tool_meta[idx].get("id_locked"):
                tool_meta[idx]["id"] = tc["id"]
                tool_meta[idx]["id_locked"] = True

        if finish:
            stop_reason = _oai_finish_to_anthropic_stop(finish)

    tail = close_text()
    if tail:
        yield tail
    for a_idx in sorted(tool_block_map.values()):
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": a_idx})

    if not text_block_open and not tool_block_map and not block_index:
        # 上游一个字都没吐，补一个空文本块，避免客户端解析异常
        yield _sse("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        })
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})

    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": out_tokens},
    })
    yield _sse("message_stop", {"type": "message_stop"})


# ---------------------------------------------- OpenAI 请求 -> Anthropic 请求


def openai_tools_to_anthropic(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    out = []
    for t in tools:
        fn = t.get("function") or t
        out.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def openai_tool_choice_to_anthropic(tc: Any) -> Any:
    if tc is None:
        return None
    if isinstance(tc, str):
        return {"auto": {"type": "auto"}, "none": {"type": "auto"},
                "required": {"type": "any"}}.get(tc, {"type": "auto"})
    if tc.get("type") == "function":
        return {"type": "tool", "name": (tc.get("function") or {}).get("name", "")}
    return {"type": "auto"}


def openai_messages_to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """返回 (system, messages)。OpenAI 的 tool 消息要合并回 user 的 tool_result 块。"""
    system_parts: list[str] = []
    out: list[dict] = []

    for m in messages or []:
        role = m.get("role", "user")
        content = m.get("content")
        if role == "system":
            system_parts.append(content if isinstance(content, str) else _text_of(content))
            continue

        if role == "tool":
            tid = m.get("tool_call_id", "")
            c = content if isinstance(content, str) else _text_of(content)
            # 合并进上一条 user 消息（通常紧跟 assistant tool_calls）
            if out and out[-1]["role"] == "user" and any(
                b.get("type") == "tool_result" for b in out[-1]["content"]
            ):
                out[-1]["content"].append({
                    "type": "tool_result", "tool_use_id": tid, "content": c,
                })
            else:
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tid, "content": c},
                ]})
            continue

        if role == "assistant":
            blocks: list[dict] = []
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                blocks.extend(b for b in content if isinstance(b, dict))
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw)
                except Exception:  # noqa: BLE001
                    args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or _new_id("toolu"),
                    "name": fn.get("name", ""),
                    "input": args,
                })
            if not blocks:
                blocks.append({"type": "text", "text": ""})
            out.append({"role": "assistant", "content": blocks})
            continue

        # user
        if isinstance(content, str):
            out.append({"role": "user", "content": content})
        elif isinstance(content, list):
            blocks = []
            for p in content:
                if not isinstance(p, dict):
                    continue
                if p.get("type") == "text":
                    blocks.append({"type": "text", "text": p.get("text", "")})
                elif p.get("type") == "image_url":
                    url = (p.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        head, _, b64 = url.partition(",")
                        mt = head.split(":")[1].split(";")[0] if ":" in head else "image/png"
                        blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": mt, "data": b64},
                        })
                    else:
                        blocks.append({"type": "image", "source": {"type": "url", "url": url}})
            out.append({"role": "user", "content": blocks or ""})
        else:
            out.append({"role": "user", "content": ""})

    return "\n\n".join(system_parts), out


def openai_request_to_anthropic(body: dict, model: str) -> dict:
    system, msgs = openai_messages_to_anthropic(body.get("messages", []))
    payload: dict[str, Any] = {
        "model": model,
        "messages": msgs,
        "max_tokens": body.get("max_tokens") or 4096,
        "stream": bool(body.get("stream", False)),
    }
    if system:
        payload["system"] = system
    if body.get("temperature") is not None:
        payload["temperature"] = body["temperature"]
    if body.get("top_p") is not None:
        payload["top_p"] = body["top_p"]
    if body.get("stop"):
        s = body["stop"]
        payload["stop_sequences"] = [s] if isinstance(s, str) else list(s)

    tools = openai_tools_to_anthropic(body.get("tools"))
    if tools:
        payload["tools"] = tools
        tc = openai_tool_choice_to_anthropic(body.get("tool_choice"))
        if tc is not None:
            payload["tool_choice"] = tc
    return payload


# ---------------------------------------------- Anthropic 响应 -> OpenAI 响应


def _anthropic_stop_to_oai_finish(stop: str | None) -> str:
    return {
        "end_turn": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "stop_sequence": "stop",
    }.get(stop or "", "stop")


def anthropic_response_to_openai(data: dict, model: str) -> dict:
    blocks = data.get("content") or []
    text_parts: list[str] = []
    tool_calls: list[dict] = []

    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            text_parts.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            tool_calls.append({
                "id": b.get("id") or _new_id("call"),
                "type": "function",
                "function": {
                    "name": b.get("name", ""),
                    "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False),
                },
            })

    msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls

    usage = data.get("usage") or {}
    return {
        "id": data.get("id") or _new_id("chatcmpl"),
        "object": "chat.completion",
        "created": _now_int(),
        "model": model,
        "choices": [{
            "index": 0,
            "message": msg,
            "finish_reason": _anthropic_stop_to_oai_finish(data.get("stop_reason")),
        }],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        },
    }


def _oai_chunk(chunk_id: str, model: str, delta: dict, finish: str | None = None) -> str:
    return "data: " + json.dumps({
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": _now_int(),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }, ensure_ascii=False) + "\n\n"


async def anthropic_stream_to_openai_sse(
    lines: AsyncIterator[str], model: str
) -> AsyncIterator[str]:
    """把 Anthropic 事件流转成 OpenAI SSE。"""
    chunk_id = _new_id("chatcmpl")
    tool_idx_map: dict[int, int] = {}
    next_idx = 0
    sent_any = False

    async for line in lines:
        if not line:
            continue
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        try:
            ev = json.loads(payload)
        except Exception:  # noqa: BLE001
            continue
        etype = ev.get("type")

        if etype == "content_block_start":
            cb = ev.get("content_block") or {}
            if cb.get("type") == "tool_use":
                idx = ev.get("index", 0)
                tool_idx_map[idx] = next_idx
                yield _oai_chunk(chunk_id, model, {
                    "tool_calls": [{
                        "index": next_idx,
                        "id": cb.get("id"),
                        "type": "function",
                        "function": {"name": cb.get("name", ""), "arguments": ""},
                    }],
                })
                next_idx += 1
                sent_any = True

        elif etype == "content_block_delta":
            d = ev.get("delta") or {}
            if d.get("type") == "text_delta":
                sent_any = True
                yield _oai_chunk(chunk_id, model, {"content": d.get("text", "")})
            elif d.get("type") == "input_json_delta":
                idx = ev.get("index", 0)
                yield _oai_chunk(chunk_id, model, {
                    "tool_calls": [{
                        "index": tool_idx_map.get(idx, 0),
                        "function": {"arguments": d.get("partial_json", "")},
                    }],
                })

        elif etype == "message_delta":
            delta = ev.get("delta") or {}
            finish = _anthropic_stop_to_oai_finish(delta.get("stop_reason"))
            yield _oai_chunk(chunk_id, model, {}, finish)

        elif etype == "message_stop":
            break

    if not sent_any:
        yield _oai_chunk(chunk_id, model, {})
    yield "data: [DONE]\n\n"
