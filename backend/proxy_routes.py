"""代理主路由：接收客户端请求 -> 选上游 -> 协议转换 -> 转发 -> 记录日志。

入站支持 Anthropic(/v1/messages) 与 OpenAI(/v1/chat/completions) 两种协议，
出站按上游声明的协议自动决定是否转换，并具备失败自动切换上游的能力。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import caveman
from . import converter as cv
from . import responses_converter as rc
from .config import ChainHop, store
from .database import db
from .limiter import limiter
from .upstream_pool import pool

router = APIRouter()

ANTHROPIC_VERSION = "2023-06-01"


def should_failover(status_code: int) -> bool:
    """该状态码是否应该切到故障转移链的下一档。

    - **5xx**：上游真的炸了 → 换节点
    - **429**：限流 → 换节点（同一上游重试大概率还是限）
    - **404**：模型不存在 / 已被下线 → 换节点（别家可能还有同模型的）

    不切的情况：
    - **400/422**：客户端请求本身有问题，换节点也一样错
    - **401/403**：Key 失效或被拒，换节点无意义（该上游会被立即熔断）
    """
    return status_code >= 500 or status_code in (429, 404)

# 协议转换查表：(入站协议, 上游协议) -> 请求转换函数
# 「responses」与「chat」共用一套语义（Responses 只是形状不同），
# 所以同一对方向可以复用同一个函数。
_REQUEST_CONVERTERS: dict[tuple[str, str], Any] = {
    ("anthropic", "openai"): cv.anthropic_request_to_openai,
    ("openai", "anthropic"): cv.openai_request_to_anthropic,
    ("anthropic", "responses"): rc.responses_request_to_chat,
    ("responses", "anthropic"): rc.chat_request_to_responses,
    # 入站 responses → 上游 chat：把 Responses 的 input 转成 chat 的 messages
    ("responses", "openai"): rc.responses_request_to_chat,
    # 入站 chat → 上游 responses：把 chat 的 messages 转成 Responses 的 input
    ("openai", "responses"): rc.chat_request_to_responses,
}

# 响应转换查表：(入站协议, 上游协议) -> 响应转换函数
_RESPONSE_CONVERTERS: dict[tuple[str, str], Any] = {
    # 上游 openai → 入站 anthropic
    ("anthropic", "openai"): cv.openai_response_to_anthropic,
    # 上游 anthropic → 入站 openai
    ("openai", "anthropic"): cv.anthropic_response_to_openai,
    # 上游 responses → 入站 anthropic / openai
    ("anthropic", "responses"): rc.responses_response_to_chat,
    ("openai", "responses"): rc.responses_response_to_chat,
    # 上游 chat → 入站 responses
    ("responses", "openai"): rc.chat_response_to_responses,
    ("responses", "anthropic"): rc.chat_response_to_responses,
}


# ------------------------------------------------------------------ 工具


_CN_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def estimate_tokens(s: str) -> int:
    """粗略估算 token 数：CJK 约 1 字 1 token，其余按 4 字符 1 token。

    仅用于上游未返回 usage 时的兜底统计。
    """
    if not s:
        return 0
    cjk = len(_CN_RE.findall(s))
    other = len(s) - cjk
    return max(1, int(cjk + other / 4.0))


def body_text(body: dict) -> str:
    """粗略取出请求里的文本内容，用于 token 估算。"""
    parts: list[str] = []
    sys = body.get("system")
    if isinstance(sys, str):
        parts.append(sys)
    elif isinstance(sys, list):
        parts.extend(cv._text_of(b) if isinstance(b, dict) else str(b) for b in sys)
    # Responses：input 数组
    for it in body.get("input", []) or []:
        if isinstance(it, str):
            parts.append(it)
        elif isinstance(it, dict):
            if it.get("type") == "function_call":
                parts.append(str(it.get("arguments") or ""))
            elif it.get("type") == "function_call_output":
                parts.append(str(it.get("output") or ""))
            else:
                parts.append(cv._text_of(it.get("content")))
    for m in body.get("messages", []) or []:
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict):
                    if b.get("type") == "text":
                        parts.append(b.get("text", ""))
                    elif b.get("type") == "tool_use":
                        parts.append(json.dumps(b.get("input") or {}, ensure_ascii=False))
                    elif b.get("type") == "tool_result":
                        parts.append(cv._text_of(b.get("content")))
    return "\n".join(parts)


def extract_usage(text: str, protocol: str) -> tuple[int, int, int, int]:
    """从响应（JSON 或 SSE 文本）里尽量提取 token 用量。

    返回 (input, output, cache_read, cache_write)。

    **input 一律是「不含缓存命中」的部分**——这样三种协议下
    `input + cache_read + cache_write` 含义统一，聚合表可以直接相加：
    - Anthropic：`input_tokens` 本来就不含缓存（缓存单独计），原样取
    - OpenAI Chat / Responses：`prompt_tokens` / `input_tokens` **已含**缓存命中，
      要减去 `cached_tokens` 才与 Anthropic 对齐

    这个语义差异是真实的（CC-Switch 为此专门存了 input_token_semantics 列：
    其本机数据里 semantics=1 的全是 codex/Responses，semantics=2 的全是
    claude/Anthropic），不归一会把缓存命中的量重复计一次。
    """
    tin = tout = cread = cwrite = 0
    try:
        if protocol == "anthropic":
            ins = re.findall(r'"input_tokens"\s*:\s*(\d+)', text)
            outs = re.findall(r'"output_tokens"\s*:\s*(\d+)', text)
            if ins:
                tin = int(ins[0])          # message_start 里的才是 input
            if outs:
                tout = int(outs[-1])       # 最后一次 message_delta 的累计值
            # 缓存：读命中与写入分开计
            creads = re.findall(r'"cache_read_input_tokens"\s*:\s*(\d+)', text)
            if creads:
                cread = int(creads[-1])
            cwrites = re.findall(r'"cache_creation_input_tokens"\s*:\s*(\d+)', text)
            if cwrites:
                cwrite = int(cwrites[-1])
        elif protocol == "responses":
            # Responses 的 usage 字段名不同：input_tokens / output_tokens
            ins = re.findall(r'"input_tokens"\s*:\s*(\d+)', text)
            outs = re.findall(r'"output_tokens"\s*:\s*(\d+)', text)
            if ins:
                tin = int(ins[-1])
            if outs:
                tout = int(outs[-1])
            # input_tokens 已含缓存命中，要减掉
            cached = re.findall(r'"cached_tokens"\s*:\s*(\d+)', text)
            if cached:
                cread = int(cached[-1])
                tin = max(0, tin - cread)
        else:
            ins = re.findall(r'"prompt_tokens"\s*:\s*(\d+)', text)
            outs = re.findall(r'"completion_tokens"\s*:\s*(\d+)', text)
            if ins:
                tin = int(ins[-1])
            if outs:
                tout = int(outs[-1])
            # prompt_tokens 已含缓存命中，要减掉
            cached = re.findall(r'"cached_tokens"\s*:\s*(\d+)', text)
            if cached:
                cread = int(cached[-1])
                tin = max(0, tin - cread)
    except Exception:  # noqa: BLE001
        pass
    return tin, tout, cread, cwrite


def resp_text(obj: Any) -> str:
    """从已转换的响应对象里取出模型产出的文本，用于 token 兜底估算。"""
    if not isinstance(obj, dict):
        return ""
    content = obj.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):          # Anthropic blocks
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "tool_use":
                parts.append(json.dumps(b.get("input") or {}, ensure_ascii=False))
        return "\n".join(parts)
    if obj.get("output") is not None:        # Responses
        parts = []
        for item in obj.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                parts.append(cv._text_of(item.get("content")))
            elif item.get("type") == "function_call":
                parts.append(str(item.get("arguments") or ""))
        return "".join(parts)
    choices = obj.get("choices") or [{}]    # OpenAI
    msg = (choices[0] if choices else {}).get("message") or {}
    if msg.get("content"):
        return msg["content"]
    return "".join((tc.get("function") or {}).get("arguments", "")
                   for tc in (msg.get("tool_calls") or []))


def anthropic_error(message: str, etype: str = "invalid_request_error", status: int = 400):
    return JSONResponse(
        status_code=status,
        content={"type": "error", "error": {"type": etype, "message": message}},
    )


def openai_error(message: str, etype: str = "invalid_request_error",
                 code: str | None = None, status: int = 400):
    err: dict[str, Any] = {"message": message, "type": etype}
    if code:
        err["code"] = code
    return JSONResponse(status_code=status, content={"error": err})


def err_response(in_protocol: str, message: str, status: int = 400,
                 etype: str = "invalid_request_error"):
    if in_protocol == "anthropic":
        return anthropic_error(message, etype, status)
    return openai_error(message, etype, None, status)


def _cap_detail(s: str) -> str:
    """请求/响应详情按设置截断存储，防止长提示词撑爆日志库。"""
    return s[: max(512, store.config.settings.payload_max_bytes)]


def _stream_text(raw: str, protocol: str) -> str:
    """从上游原始 SSE 流里重建模型产出的完整文本（供日志详情展示）。"""
    if protocol == "anthropic":
        # content_block_delta 事件里的 text_delta 片段
        joined = "".join(re.findall(
            r'"type"\s*:\s*"text_delta"\s*,\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"', raw))
        try:
            return json.loads(f'"{joined}"')  # 还原 \n 等转义
        except Exception:  # noqa: BLE001
            return joined
    # OpenAI / Responses：逐行解析 data: {...}
    out: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            d = json.loads(data)
            # Responses 事件流
            if d.get("type") == "response.output_text.delta":
                if isinstance(d.get("delta"), str):
                    out.append(d["delta"])
                continue
            delta = ((d.get("choices") or [{}])[0].get("delta") or {})
            if isinstance(delta.get("content"), str):
                out.append(delta["content"])
        except Exception:  # noqa: BLE001
            continue
    return "".join(out)


def _resp_summary(captured: str, out_obj: Any, protocol: str,
                  tin: int, tout: int) -> str:
    """日志详情的响应摘要：完整对话内容 + usage 汇总（不存原始 SSE 流）。"""
    if not store.config.settings.log_payloads:
        return ""
    text = resp_text(out_obj) if isinstance(out_obj, dict) else _stream_text(captured, protocol)
    summary = {
        "role": "assistant",
        "content": text,
        "usage": {"input_tokens": tin, "output_tokens": tout,
                  "total_tokens": tin + tout},
    }
    return _cap_detail(json.dumps(summary, ensure_ascii=False))


# 客户端可能携带的会话标识头：OpenCode Go 明确识别 x-opencode-session 与
# Claude Code 的原生会话头，客户端有就直接透传，保证端到端会话一致。
SESSION_HEADERS = ("x-opencode-session", "x-claude-session-id", "x-session-id")


def opencode_session_id(request: Request, body: dict, ck) -> str:
    """为 OpenCode Go 解析/生成稳定会话 ID。

    优先级：客户端会话头 > 请求 metadata.user_id（Claude Code 每个会话
    都会携带，恰好是「每个对话一个 ID」） > 客户端身份兜底。
    全部确定性派生，无需维护会话状态。
    """
    for name in SESSION_HEADERS:
        v = request.headers.get(name)
        if v:
            return v.strip()
    meta = body.get("metadata") or {}
    seed = str(meta.get("user_id") or "") if isinstance(meta, dict) else ""
    if not seed:
        seed = f"{ck.name if ck else 'anon'}:{request.client.host if request.client else 'local'}"
    return "ccp-" + uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:24]


# ------------------------------------------------------------------ 鉴权


def client_protocol(request: Request) -> str:
    """推断客户端协议：Anthropic 客户端（如 Claude Code）会带 anthropic-version 头。

    不能靠 URL path 判断——/v1/messages/count_tokens 并不以 messages 结尾。
    """
    if request.headers.get("anthropic-version"):
        return "anthropic"
    # Responses 客户端不发 anthropic-version，只能看路径
    if request.url.path.rstrip("/").endswith("/responses"):
        return "responses"
    return "openai"


def authenticate(request: Request, protocol: str | None = None) -> tuple[Any | None, Any | None]:
    """返回 (client_key 对象 | None, 错误响应 | None)。

    策略：没有任何已启用 Key 时放行匿名；存在已启用 Key 则必须匹配。
    protocol 由路由显式指定，未指定时按请求头推断。
    """
    proto = protocol or client_protocol(request)
    keys = [k for k in store.list_keys() if k.enabled]
    if not keys:
        return None, None

    raw = request.headers.get("x-api-key") or request.headers.get("authorization", "")
    if raw.lower().startswith("bearer "):
        raw = raw[7:]
    raw = raw.strip()
    if not raw:
        return None, err_response(
            proto,
            "缺少 API Key，请在管理后台创建客户端 Key 并在请求头携带 x-api-key",
            401, "authentication_error",
        )
    ck = store.find_key_by_value(raw)
    if not ck or not ck.enabled:
        return None, err_response(proto, "API Key 无效或已停用", 401, "authentication_error")
    return ck, None


# ------------------------------------------------------------------ 流式包装


async def _byte_iter(resp: httpx.Response) -> AsyncIterator[str]:
    """把上游原始字节流按 SSE 行切出来（供转换器消费）。"""
    async for line in resp.aiter_lines():
        yield line


# ------------------------------------------------------------------ 主处理


async def handle_request(request: Request, in_protocol: str):
    t0 = time.time()
    path = request.url.path
    ck, err = authenticate(request)
    if err:
        return err

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return err_response(in_protocol, "请求体不是合法 JSON", 400)

    if not isinstance(body, dict):
        return err_response(in_protocol, "请求体必须是 JSON 对象", 400)

    model = body.get("model") or ""
    if not model:
        model = "claude-sonnet-4-5"
        body["model"] = model

    # ---- 请求详情捕获（设置开关，默认关；截断存储） ----
    req_capture = ""
    if store.config.settings.log_payloads:
        try:
            req_capture = json.dumps(body, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            req_capture = str(body)
        req_capture = _cap_detail(req_capture)

    # ---- 限流 ----
    if ck:
        used = db.today_tokens(ck.name)
        ok, msg = limiter.check(ck.id, ck.rate_limit_rpm, ck.daily_token_limit, used)
        if not ok:
            return err_response(in_protocol, msg, 429, "rate_limit_error")
        if ck.allowed_models and model not in ck.allowed_models:
            return err_response(
                in_protocol,
                f"当前 Key 不允许使用模型 {model}，允许：{', '.join(ck.allowed_models)}",
                403, "permission_error",
            )

    # ---- 模型映射 → 故障转移链 ----
    # 映射命中 → 用该映射的链（可能多档，逐档降级）；
    # 未命中 → 单档「自动调度 + 原模型名」，沿用公共调度。
    hops = store.resolve_chain(model)
    target_model = (hops[0].target if hops else "") or model
    # 客户端 Key 的固定上游优先于映射（显式指定 > 映射里的绑定）
    pinned = ck.upstream_id if (ck and ck.upstream_id) else ""
    if pinned:
        # Key 固定了上游：只走这一档，不做链式降级
        hops = [ChainHop(upstream_id=pinned, target=target_model)]

    # ---- 展开链 ----
    plan = pool.plan_chain(hops, fallback_model=model)
    cands = [e.upstream for e in plan if e.upstream is not None and not e.skipped]
    if not cands:
        hint = "没有可用的上游节点"
        if store.list_upstreams():
            skipped = "；".join(
                f"第{e.hop + 1}档 {e.skipped}" for e in plan if e.skipped
            )
            hint = ("所有上游均处于冷却或已禁用，请检查上游配置或点击「重置状态」"
                    + (f"（{skipped}）" if skipped else ""))
        else:
            hint = "尚未配置任何上游节点，请在管理后台「上游管理」中添加"
        return err_response(in_protocol, hint, 503, "api_error")

    stream = bool(body.get("stream", False))
    in_tokens_est = estimate_tokens(body_text(body))
    session_id = opencode_session_id(request, body, ck)

    # ---- Caveman 引擎：转发前瘦身（原地修改 body；日志详情记录的仍是原始请求） ----
    # 缓存感知：候选里只要有支持 prompt cache 的上游，就走弱压缩模式——
    # 压缩旧消息会改写「system + 早期消息」这段可缓存前缀，命中价失效的损失
    # 可能大于压缩收益（DeepSeek 命中价约为原价 1/10）。宁可少省，也不改前缀。
    cache_aware = any(caveman.upstream_supports_cache(u) for u in cands)
    body_before = json.loads(json.dumps(body, ensure_ascii=False, default=str))
    _, caveman_saved = caveman.compress_body(body, cache_aware=cache_aware)
    # 压缩后的中间快照：注入 Say less 规则之前的体积。
    # 规则本身会让请求变大（+约 1900 字符），拿最终体积算「节省率」
    # 会得出负数——压缩收益与规则开销必须分开记。
    compressed_chars = len(json.dumps(body, ensure_ascii=False, default=str))
    # Say less：先判这次请求要不要注入，再决定是否改写 system。
    # 判定依据（模式 / 原因 / 本次输入量与消息数）一并进日志的「优化」列。
    voice_dec = caveman.voice_decision(
        cache_aware=cache_aware,
        in_tokens=in_tokens_est,
        message_count=caveman.request_message_count(body),
    )
    voice_on = caveman.inject_voice(body, voice_dec)
    caveman_stats = caveman.stats(body_before, body, caveman_saved,
                                  cache_aware=cache_aware, voice_injected=voice_on,
                                  voice=voice_dec, compressed_chars=compressed_chars)
    if voice_on:
        print(f"[caveman] {model} voice 规则已注入（Say less）— {voice_dec['reason']}")
    elif voice_dec["mode"] == caveman.VOICE_AUTO:
        print(f"[caveman] {model} 未注入 Say less — {voice_dec['reason']}")
    if caveman_saved:
        tag = "（缓存优先）" if cache_aware else ""
        print(f"[caveman] {model} <- {ck.name if ck else '匿名'} 瘦身 {caveman_saved} 字符"
              f"（{caveman_stats['saved_pct']}%）{tag}")
    tried: list[str] = []
    last_err = ""
    last_status = 0
    # 故障转移轨迹：每一档的尝试结果，写进请求日志
    trail: list[dict[str, Any]] = []

    # 按链的顺序逐档尝试。plan 里已排好序、已剔除不可用的档。
    for entry in plan:
        up = entry.upstream
        if up is None:
            # 规划期就跳过的档（上游不存在/停用/冷却/熔断）
            trail.append({"hop": entry.hop + 1, "upstream": "", "model": entry.target,
                          "status": 0, "result": entry.skipped or "跳过"})
            continue
        tried.append(up.name)
        hop_model = entry.target or target_model

        # ---- 构造上游 payload ----
        if in_protocol == up.protocol:
            payload = dict(body)
            payload["model"] = hop_model
        else:
            conv = _REQUEST_CONVERTERS.get((in_protocol, up.protocol))
            if conv is None:
                last_err = (f"{up.name}: 暂不支持 {in_protocol} → {up.protocol} 协议转换")
                pool.report_failure(up, last_err)
                trail.append({"hop": entry.hop + 1, "upstream": up.name,
                              "model": hop_model, "status": 0,
                              "result": last_err[:120]})
                continue
            payload = conv(body, hop_model)

        try:
            resp = await pool.call(up, payload, stream, session_id=session_id)
        except Exception as e:  # noqa: BLE001
            # 注意：这里不能 aclose 共享连接池，否则会打断并发中的其他请求
            pool.report_failure(up, f"{type(e).__name__}: {e}")
            last_err = f"{up.name}: {type(e).__name__}: {e}"
            trail.append({"hop": entry.hop + 1, "upstream": up.name, "model": hop_model,
                          "status": 0, "result": f"{type(e).__name__}: {e}"[:120]})
            continue

        # ---- 上游返回错误：可重试则切换下一个 ----
        if resp.status_code >= 400:
            text = ""
            try:
                text = (await resp.aread()).decode("utf-8", "ignore")[:500]
            except Exception:  # noqa: BLE001
                pass
            await resp.aclose()
            # 带上状态码：客户端错误（400/404/422）不该算上游的失败，
            # 否则上游是好的也会被误熔断。401/403 会立即熔断（Key 失效）。
            pool.report_failure(up, f"HTTP {resp.status_code}: {text[:200]}",
                                status_code=resp.status_code)
            last_err = f"{up.name} -> HTTP {resp.status_code}: {text[:300]}"
            last_status = resp.status_code
            trail.append({"hop": entry.hop + 1, "upstream": up.name, "model": hop_model,
                          "status": resp.status_code,
                          "result": f"HTTP {resp.status_code}"})
            # 切换条件：5xx（上游炸了）/ 429（限流）/ 404（模型不存在，
            # 比如模型被下线——换节点可能还有同模型的别家）。
            # 400/401/403 属客户端或鉴权问题，换节点无意义，直接返回。
            if should_failover(resp.status_code):
                continue  # 换下一个上游
            # 其余 4xx 属客户端问题，重试无意义
            latency = int((time.time() - t0) * 1000)
            db.add(client_key=(ck.key if ck else ""), client_name=(ck.name if ck else "匿名"),
                   upstream_id=up.id, upstream_name=up.name, in_protocol=in_protocol,
                   out_protocol=up.protocol, request_model=model, actual_model=hop_model,
                   stream=int(stream), status=resp.status_code, ok=0,
                   input_tokens=in_tokens_est, latency_ms=latency,
                   error=f"HTTP {resp.status_code}: {text[:300]}",
                   request_body=req_capture, response_body=_cap_detail(text),
                   caveman_saved=caveman_saved, voice_injected=int(voice_on),
                   caveman_stats=json.dumps(caveman_stats, ensure_ascii=False),
                   failover_trail=json.dumps(trail, ensure_ascii=False))
            return err_response(in_protocol, f"上游返回错误：{text[:400]}",
                                resp.status_code, "api_error")

        # ---- 成功 ----
        up_start = time.time()
        client_name = ck.name if ck else "匿名"
        client_key_val = ck.key if ck else ""

        def make_log(captured: str, out_obj: Any = None, up=up, in_tok=in_tokens_est,
                     hop_model=hop_model):
            tin, tout, cread, cwrite = extract_usage(captured, up.protocol)
            if not tin:
                tin = in_tok
            if not tout:
                # 上游未返回 usage 时按产出文本估算，避免统计恒为 0
                tout = estimate_tokens(captured) if stream else estimate_tokens(resp_text(out_obj))
            # 成功的那一档也记进轨迹，末端标「成功」
            trail.append({"hop": entry.hop + 1, "upstream": up.name, "model": hop_model,
                          "status": 200, "result": "成功"})
            db.add(
                client_key=client_key_val, client_name=client_name,
                upstream_id=up.id, upstream_name=up.name,
                in_protocol=in_protocol, out_protocol=up.protocol,
                request_model=model, actual_model=hop_model,
                stream=int(stream), status=200, ok=1,
                input_tokens=tin, output_tokens=tout,
                cache_read_tokens=cread, cache_write_tokens=cwrite,
                latency_ms=int((time.time() - t0) * 1000), error="",
                request_body=req_capture,
                response_body=_resp_summary(captured, out_obj, up.protocol, tin, tout),
                caveman_saved=caveman_saved, voice_injected=int(voice_on),
                caveman_stats=json.dumps(caveman_stats, ensure_ascii=False),
                failover_trail=json.dumps(trail, ensure_ascii=False),
            )
            pool.report_success(up, int((time.time() - up_start) * 1000))
            if ck:
                ck.last_used_at = time.time()
                ck.total_requests += 1

        # ---------- 流式 ----------
        if stream:
            if in_protocol == up.protocol:
                src: AsyncIterator[bytes] = resp.aiter_bytes()

                async def gen_raw():
                    buf: list[str] = []
                    try:
                        async for ch in src:
                            buf.append(ch.decode("utf-8", "ignore") if isinstance(ch, bytes) else ch)
                            yield ch
                    finally:
                        await resp.aclose()
                        make_log("".join(buf))

                return StreamingResponse(gen_raw(), media_type="text/event-stream",
                                         headers={"Cache-Control": "no-cache",
                                                  "X-Accel-Buffering": "no"})

            # 需要转换：raw_buf 收集「上游原始」响应，供 extract_usage 按上游协议解析
            raw_buf: list[str] = []

            async def tee_lines(src: AsyncIterator[str]):
                async for line in src:
                    raw_buf.append(line)
                    yield line

            # 流式转换：(上游协议, 入站协议) 决定用哪个转换器
            src_lines = tee_lines(_byte_iter(resp))
            if up.protocol == "openai" and in_protocol == "anthropic":
                conv = cv.openai_stream_to_anthropic_sse(src_lines, hop_model, in_tokens_est)
            elif up.protocol == "anthropic" and in_protocol == "openai":
                conv = cv.anthropic_stream_to_openai_sse(src_lines, hop_model)
            elif up.protocol == "responses" and in_protocol == "openai":
                conv = rc.responses_stream_to_chat_sse(src_lines, hop_model)
            elif up.protocol == "responses" and in_protocol == "anthropic":
                conv = rc.responses_stream_to_anthropic_sse(src_lines, hop_model, in_tokens_est)
            elif up.protocol == "openai" and in_protocol == "responses":
                conv = rc.chat_stream_to_responses_sse(src_lines, hop_model)
            elif up.protocol == "anthropic" and in_protocol == "responses":
                conv = rc.anthropic_stream_to_responses_sse(src_lines, hop_model)
            else:
                make_log("".join(raw_buf))
                return err_response(
                    in_protocol,
                    f"暂不支持 {up.protocol} → {in_protocol} 流式转换",
                    502, "api_error")

            async def gen_conv():
                try:
                    async for piece in conv:
                        yield piece.encode("utf-8")
                finally:
                    await resp.aclose()
                    make_log("".join(raw_buf))

            return StreamingResponse(gen_conv(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache",
                                              "X-Accel-Buffering": "no"})

        # ---------- 非流式 ----------
        raw = await resp.aread()
        await resp.aclose()
        text = raw.decode("utf-8", "ignore")
        try:
            data = json.loads(text)
        except Exception:  # noqa: BLE001
            make_log(text)
            return err_response(in_protocol, f"上游返回非 JSON 内容：{text[:300]}", 502, "api_error")

        if in_protocol == up.protocol:
            out = data
        else:
            conv = _RESPONSE_CONVERTERS.get((in_protocol, up.protocol))
            if conv is None:
                make_log(text)
                return err_response(
                    in_protocol,
                    f"暂不支持 {up.protocol} → {in_protocol} 响应转换",
                    502, "api_error")
            out = conv(data, hop_model, in_tokens_est)

        make_log(text, out)
        return JSONResponse(content=out)

    # ---- 所有候选都失败 ----
    latency = int((time.time() - t0) * 1000)
    db.add(client_key=(ck.key if ck else ""), client_name=(ck.name if ck else "匿名"),
           upstream_id="", upstream_name=",".join(tried), in_protocol=in_protocol,
           out_protocol="", request_model=model, actual_model=target_model,
           stream=int(stream), status=last_status or 502, ok=0,
           input_tokens=in_tokens_est, latency_ms=latency,
           error=last_err[:500] or "全部上游均不可用",
           request_body=req_capture, response_body="",
           caveman_saved=caveman_saved, voice_injected=int(voice_on),
           caveman_stats=json.dumps(caveman_stats, ensure_ascii=False),
           failover_trail=json.dumps(trail, ensure_ascii=False))
    return err_response(
        in_protocol,
        f"所有上游调用失败（已尝试 {len(tried)} 个）：{last_err[:300]}",
        last_status if last_status in (429, 500, 502, 503) else 502,
        "api_error",
    )


# ------------------------------------------------------------------ 路由


@router.post("/v1/messages")
async def anthropic_messages(request: Request):
    return await handle_request(request, "anthropic")


@router.post("/v1/messages/count_tokens")
async def anthropic_count_tokens(request: Request):
    """Claude Code 会调用它预估 prompt 长度，返回估算值避免 404。"""
    ck, err = authenticate(request, "anthropic")
    if err:
        return err
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    n = estimate_tokens(body_text(body if isinstance(body, dict) else {}))
    return JSONResponse({"input_tokens": n})


@router.post("/v1/chat/completions")
async def openai_chat(request: Request):
    return await handle_request(request, "openai")


@router.post("/v1/responses")
async def openai_responses(request: Request):
    """OpenAI Responses 协议入口（Codex / OpenAI 新 SDK / OpenCode Go 的 responses 端点）。"""
    return await handle_request(request, "responses")


@router.get("/v1/models")
async def list_models(request: Request):
    """返回对外暴露的模型别名，供 Claude Code 启动探活使用。"""
    ck, err = authenticate(request)
    if err:
        return err

    names: list[str] = []
    for m in store.list_mappings():
        if m.enabled and m.alias and m.alias not in names:
            names.append(m.alias)
    if not names:
        for up in store.list_upstreams():
            if up.enabled:
                names.extend(up.models)
    if not names:
        names = ["claude-sonnet-4-5", "claude-opus-4-1"]

    now = int(time.time())
    return JSONResponse({
        "object": "list",
        "data": [{"id": n, "object": "model", "created": now, "owned_by": "switchboard"}
                 for n in names],
    })
