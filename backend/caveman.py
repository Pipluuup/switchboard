"""Caveman 引擎：转发前对请求做瘦身，节省上游 token。

原则（与 caveman 项目的研究结论一致——改写用户 prompt 会降低回答质量，
所以只压「模型读的内容」，从不改写用户当前提问）：
- system 提示词默认不动（改了会打断上游的 prompt cache，往往得不偿失）
- 最近 N 条消息原样保留（正在进行的对话上下文最重要）
- 更早的消息：JSON / 日志感知压缩，其余超长文本截成「头 + 省略标记 + 尾」
- 图片、tool_use 的结构块不改（协议正确性优先）
- 只改「发给上游的副本」，日志详情记录的仍是客户端原始请求

Say less（voice）：把「电报体回复」规则附加到 system，让模型输出变短。
规则改写自 JuliusBrussee/caveman（Apache-2.0）的 skills/caveman/SKILL.md。
"""
from __future__ import annotations

import json
import re
from typing import Any

from .config import store

_MARK = "\n…[caveman 省略 {n} 字符]…\n"

_JSON_STR_LIMIT = 200   # JSON 里字符串值超过该长度才截断
_JSON_ARR_KEEP = 10     # JSON 数组保留首尾各多少项
_LOG_MIN_LINES = 30     # 行数达到该值才判定为日志
_LOG_KEEP = 15          # 日志保留首尾各多少行

_VOICE_MARKER = "[caveman voice]"

# Say less：电报体回复规则（改写自 JuliusBrussee/caveman，Apache-2.0）
VOICE_RULES = f"""{_VOICE_MARKER}
Respond terse like smart caveman. All technical substance stays. Only fluff dies.
This is a voice, not broken grammar. Every fact survives.

1. Answer first: [thing] [action] [reason]. [next step]. No greeting, no recap.
2. Kill ceremony: no "Sure!", "Let me", "I'll now", "Hope this helps"; no just/really/basically/actually/simply.
3. Short word: "fix" not "implement a solution for". Standard acronyms fine; no invented abbreviations.
4. Articles optional, meaning never: drop a/an/the if it still reads. NEVER drop not/never/no/only/except. Numbers and units exact.
5. One idea per sentence: max 20 words, active voice, imperative for instructions. Clarity beats compression.
6. Payload verbatim: code, commands, paths, API names exact. Errors quoted exact, shortest decisive line only. Show changed lines plus 1-2 context lines, not whole files. Existing comments in edited files are payload: never delete one you were not asked to change.
7. Tool runs: no text between routine calls. One line before a multi-step run, one line per phase change, one line with the result.
8. User's language: compress the style, not the language. Never switch language because of quoted text. Technical terms and errors stay verbatim.
9. Never perform the voice: no "mode on" announcements, no fake grammar, no decorative tables or emoji. If terse phrasing is not shorter, use plain.

Break the rules (plain prose) for: security warnings; irreversible actions; order-critical steps; confused users; anything persisted outside chat (code, commits, docs, PRs); questions to the user (full sentences so the answer comes back right).

Pre-send check: first sentence announces what you will do? Delete it. Last sentence recaps or offers help? Delete it. Every negation present? Every code span, path, number, error verbatim?"""


def _squeeze(text: str) -> str:
    """折叠多余空白：连续空格、行尾空白、3 个以上连续换行。"""
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" ?\n{3,}", "\n\n", text)
    return re.sub(r"[ \t]+\n", "\n", text)


# ---------------- 片段保护（tombstone） ----------------
#
# 压缩时如果直接头尾截断，代码块 / URL / 路径会被腰斩——编码场景下模型读到
# 的是断掉的代码，比不压缩还糟。做法（参考 OmniRoute 的 tombstone 思路）：
# 1. 压缩前把受保护的片段摘出来，换成短占位符
# 2. 对剩余文本做压缩
# 3. 把片段原样缝回去
#
# 受保护片段：围栏代码块、行内代码、URL、文件路径、标识符常量。

_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"https?://[^\s<>\"'）)，。；、]+")
_PATH_RE = re.compile(
    r"(?<![\w/])(?:[~.$]*/)?(?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,8}(?::\d+)?"
    r"(?:/[\w.-]+)*"
)
_CONST_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")

_TOMBSTONE_FMT = "{i}"


class _Protector:
    """把受保护片段摘成占位符，压缩后原样缝回。"""

    def __init__(self) -> None:
        self.parts: list[str] = []

    def _stash(self, s: str) -> str:
        self.parts.append(s)
        return _TOMBSTONE_FMT.format(i=len(self.parts) - 1)

    def protect(self, text: str) -> str:
        """摘出受保护片段，返回带占位符的文本。"""
        if not text:
            return text
        for rx in (_FENCE_RE, _INLINE_CODE_RE, _URL_RE, _PATH_RE, _CONST_RE):
            text = rx.sub(lambda m: self._stash(m.group(0)), text)
        return text

    def restore(self, text: str) -> str:
        """把占位符换回原文。"""
        if not text or not self.parts:
            return text
        # 反复替换：占位符可能被压缩逻辑切开（罕见），多轮收敛
        for _ in range(4):
            new = re.sub(r"(\d+)",
                         lambda m: self.parts[int(m.group(1))] if int(m.group(1)) < len(self.parts) else m.group(0),
                         text)
            if new == text:
                break
            text = new
        return text


# ---------------- 格式感知压缩（Read less 增强） ----------------


def _shrink_json_value(v: Any, depth: int = 0) -> Any:
    """递归压缩 JSON 结构：结构保留、长字符串截断、大数组留首尾。"""
    if isinstance(v, str):
        if len(v) > _JSON_STR_LIMIT:
            keep = max(64, _JSON_STR_LIMIT // 2)
            v = v[:keep] + f"…[caveman 省略 {len(v) - keep - 40} 字符]…" + v[-40:]
        return v
    if isinstance(v, list):
        if len(v) > _JSON_ARR_KEEP:
            head = [_shrink_json_value(x, depth + 1) for x in v[:_JSON_ARR_KEEP - 2]]
            tail = [_shrink_json_value(v[-1], depth + 1)]
            omitted = len(v) - (_JSON_ARR_KEEP - 2) - 1
            return head + [f"…[caveman 省略 {omitted} 项]…"] + tail
        return [_shrink_json_value(x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {k: _shrink_json_value(x, depth + 1) for k, x in v.items()}
    return v


def _try_json_shrink(text: str, min_chars: int) -> tuple[str, int] | None:
    """文本是 JSON 时做结构化压缩（保留字段名与层级，只删值里的噪声）。"""
    s = text.strip()
    if not s or s[0] not in "{[" or len(s) > 200_000 or len(s) < min_chars * 2:
        return None
    try:
        obj = json.loads(s)
    except Exception:  # noqa: BLE001
        return None
    out = json.dumps(_shrink_json_value(obj), ensure_ascii=False, separators=(",", ":"))
    if len(out) >= len(text) * 0.9:
        return None  # 压不动就交给通用截断
    return out, len(text) - len(out)


_LOG_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]|^\[\w+\]| (INFO|WARN|ERROR|DEBUG|TRACE|WARNING|ERR)\b")


def _try_log_shrink(text: str) -> tuple[str, int] | None:
    """日志感知压缩：保留首尾 _LOG_KEEP 行，中间用标记代替。"""
    lines = text.splitlines()
    if len(lines) < _LOG_MIN_LINES:
        return None
    hits = sum(1 for ln in lines if _LOG_LINE_RE.search(ln))
    if hits < len(lines) * 0.4:
        return None
    omitted = len(lines) - _LOG_KEEP * 2
    out = "\n".join(lines[:_LOG_KEEP]) + f"\n…[caveman 省略 {omitted} 行]…\n" + "\n".join(lines[-_LOG_KEEP:])
    return out, len(text) - len(out)


def _shrink_text(text: str, min_chars: int) -> tuple[str, int]:
    """超长文本压缩：先试 JSON 结构化，再试日志行压缩，兜底「头 + 省略标记 + 尾」。

    低于阈值的文本只做空白折叠。
    受保护片段（代码块 / URL / 路径 / 常量）先摘成占位符，压缩完原样缝回。
    """
    if len(text) < min_chars:
        t = _squeeze(text)
        return t, len(text) - len(t)
    for fn in (_try_json_shrink, _try_log_shrink):
        r = fn(text, min_chars) if fn is _try_json_shrink else fn(text)
        if r:
            return r
    head = max(64, int(min_chars * 0.5))
    tail = max(32, int(min_chars * 0.25))
    omit = len(text) - head - tail
    return text[:head] + _MARK.format(n=omit) + text[-tail:], omit


def _shrink_text_protected(text: str, min_chars: int, light: bool = False) -> tuple[str, int]:
    """带片段保护的压缩：先摘出受保护片段，压缩剩余部分，最后原样缝回。"""
    prot = _Protector()
    masked = prot.protect(text)
    if light:
        new, n = _shrink_text_light(masked, min_chars)
    else:
        new, n = _shrink_text(masked, min_chars)
    restored = prot.restore(new)
    # 缝回后可能变长（占位符比原文短），按实际长度修正节省量
    return restored, max(0, len(text) - len(restored))


def _shrink_text_light(text: str, min_chars: int) -> tuple[str, int]:
    """轻量压缩（缓存优先模式）：只折叠空白 + 大幅保留原文，不动结构。

    目的：把改动量压到最小——压缩旧消息本就会改写可缓存前缀，
    改动越少，前缀重建后越快恢复稳定。
    与 _shrink_text 的差别：不做 JSON / 日志结构化裁剪，且保留的头尾比例大得多。
    """
    t = _squeeze(text)
    if len(t) < min_chars:
        return t, len(text) - len(t)
    # 保留 85%：只截掉中间一小段
    keep_ratio = 0.85
    budget = int(len(t) * keep_ratio)
    head = int(budget * 0.75)
    tail = budget - head
    omit = len(t) - head - tail
    if omit <= 0:
        return t, len(text) - len(t)
    return t[:head] + _MARK.format(n=omit) + t[-tail:], omit


def _shrink_block_list(blocks: list, min_chars: int, light: bool = False) -> int:
    """压缩 Anthropic blocks / OpenAI 多模态 parts 里的文本型内容。"""
    fn = _shrink_text_light if light else _shrink_text
    saved = 0
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            new, n = _shrink_text_protected(b["text"], min_chars, light)
            b["text"], saved = new, saved + n
        elif b.get("type") == "tool_result":
            c = b.get("content")
            if isinstance(c, str):
                new, n = _shrink_text_protected(c, min_chars, light)
                b["content"], saved = new, saved + n
            elif isinstance(c, list):
                saved += _shrink_block_list(c, min_chars, light)
    return saved


def _shrink_message(m: dict, min_chars: int, light: bool = False) -> int:
    """压缩单条消息：纯文本 content 直接处理，块列表交给 _shrink_block_list。

    同时覆盖 OpenAI 的 tool 角色消息（content 为字符串的工具结果）。
    """
    c = m.get("content")
    if isinstance(c, str):
        new, n = _shrink_text_protected(c, min_chars, light)
        m["content"] = new
        return n
    if isinstance(c, list):
        return _shrink_block_list(c, min_chars, light)
    return 0


def _tool_use_ids(m: dict) -> set[str]:
    """取出消息里 tool_use 的 id（Anthropic blocks / OpenAI tool_calls）。"""
    ids: set[str] = set()
    c = m.get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                ids.add(str(b["id"]))
    for tc in (m.get("tool_calls") or []) if isinstance(m.get("tool_calls"), list) else []:
        if isinstance(tc, dict) and tc.get("id"):
            ids.add(str(tc["id"]))
    return ids


def _tool_result_ids(m: dict) -> set[str]:
    """取出消息里 tool_result / tool 角色消息所对应的 tool_use id。"""
    ids: set[str] = set()
    c = m.get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                ids.add(str(b["tool_use_id"]))
    if m.get("role") == "tool" and m.get("tool_call_id"):
        ids.add(str(m["tool_call_id"]))
    return ids


def _compress_messages(msgs: list, min_chars: int, keep: int, light: bool) -> int:
    """压缩窗口外消息，但保证 tool_use / tool_result 配对完整。

    配对规则（对应 OmniRoute 的「结构完整性守卫」）：
    - 一条消息含 tool_use → 它的 tool_result 所在消息**跳过压缩**，
      否则会出现「有调用无结果」的悬空配对，部分 provider 会直接报错
    - 一条消息含 tool_result → 同样跳过，保证它引用的调用仍完整
    """
    # 收集全部 tool_use id：若某个 tool_result 的配对调用还在窗口外，
    # 就不能压这条 tool_result
    protected_ids: set[str] = set()
    for m in msgs:
        if isinstance(m, dict):
            protected_ids |= _tool_use_ids(m)

    saved = 0
    for m in msgs[:-keep] if len(msgs) > keep else []:
        if not isinstance(m, dict):
            continue
        # 配对保护：含 tool_use 或 tool_result 的消息不压
        if _tool_use_ids(m) or _tool_result_ids(m):
            continue
        saved += _shrink_message(m, min_chars, light)
    return saved


def compress_body(body: dict, cache_aware: bool = False) -> tuple[dict, int]:
    """按设置压缩请求体（原地修改），返回 (body, 省略的字符数)。

    `cache_aware=True` 时进入「缓存优先」模式：保留窗口放大、阈值提高，
    尽量少改写前缀——因为压缩旧消息恰好会改写可缓存的前缀（system + 早期消息），
    对命中价差大的上游（DeepSeek 命中价约原价 1/10）来说，缓存比压缩更值钱。

    详细统计见 stats()（原始/压缩后字符数、节省率、命中引擎、模式）。
    """
    st = store.config.settings
    if not st.caveman_enabled:
        return body, 0
    min_chars = max(64, st.caveman_min_chars)
    keep = max(1, st.caveman_keep_recent)
    light = cache_aware
    if cache_aware:
        min_chars = max(min_chars, st.caveman_min_chars * 2)
        keep = max(keep, st.caveman_keep_recent * 2)
    saved = 0
    if st.caveman_compress_system and not cache_aware:
        sys = body.get("system")
        if isinstance(sys, str):
            new, n = _shrink_text_protected(sys, min_chars)
            body["system"], saved = new, saved + n
        elif isinstance(sys, list):
            saved += _shrink_block_list(sys, min_chars)
    msgs = body.get("messages")
    if isinstance(msgs, list):
        saved += _compress_messages(msgs, min_chars, keep, light)
    return body, saved


# ---------------- 压缩统计 ----------------


def stats(body_before: dict, body_after: dict, saved: int,
          cache_aware: bool = False, voice_injected: bool = False,
          voice: dict[str, Any] | None = None,
          compressed_chars: int | None = None) -> dict:
    """压缩统计：原始/压缩后字符数、节省率、命中能力、模式。

    供日志「优化」列与详情页展示——比单纯的「省了多少字符」更能说明
    这次压缩到底做了什么。

    `voice` 是 voice_decision() 的结果：带上模式与判定原因，日志里
    才能看出「这次为什么注入 / 为什么没注入」。

    `compressed_chars` 是「压缩完、注入规则前」的大小。注入 Say less 规则
    本身会让请求变长（+约 1900 字符），如果拿注入后的体积去算节省率，
    会得出「省了 -1234%」这种荒唐数字——所以压缩收益和规则开销分开算。
    """
    def _size(b: dict) -> int:
        try:
            return len(json.dumps(b, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            return 0

    before = _size(body_before)
    after = _size(body_after)
    # 压缩后的体积：优先用调用方给的中间快照，退回整体体积
    comp = compressed_chars if compressed_chars is not None else after
    pct = round((before - comp) * 100.0 / before, 1) if before else 0.0
    # 规则开销：注入后比压缩后多出来的部分（未注入则为 0）
    voice_added = max(0, after - comp)
    caps: list[str] = []
    if saved:
        caps.append("结构感知压缩")
    if cache_aware:
        caps.append("缓存优先")
    if voice_injected:
        caps.append("Say less")
    out = {
        "orig_chars": before,
        "new_chars": after,
        "compressed_chars": comp,
        "saved_chars": saved,
        "saved_pct": pct,
        "voice_added_chars": voice_added,
        "mode": "cache-aware" if cache_aware else "normal",
        "capabilities": caps,
    }
    if voice is not None:
        out["voice"] = voice
    return out


# ---------------- 缓存感知 ----------------

# 支持 prompt cache（自动前缀缓存）的上游域名片段。
# 这些上游命中缓存的 token 按折扣价计费（DeepSeek 约为原价 1/10，
# Anthropic 命中价为输入价的 1.25 倍），缓存失效的损失大于压缩收益。
CACHE_CAPABLE_HOSTS = (
    "anthropic.com", "openai.com", "deepseek.com", "moonshot.cn", "bigmodel.cn",
    "siliconflow.cn", "volces.com", "dashscope.aliyuncs.com", "sensenova.cn",
    "agnes-ai.cn", "groq.com", "openrouter.ai", "together.xyz", "mistral.ai",
    "x.ai", "fireworks.ai", "cerebras.ai", "baidu.com", "stepfun.com",
)


def upstream_supports_cache(upstream: Any) -> bool:
    """该上游是否支持 prompt cache（按 Base URL 域名判断）。"""
    try:
        base = (getattr(upstream, "base_url", "") or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return any(h in base for h in CACHE_CAPABLE_HOSTS)


def cache_aware_mode(upstream: Any) -> bool:
    """是否进入「缓存优先」模式：上游支持缓存且用户开启了缓存感知。"""
    if not store.config.settings.caveman_cache_aware:
        return False
    return upstream_supports_cache(upstream)


# ---------------- Say less：voice 规则注入 ----------------

# 三态模式
VOICE_OFF = "off"    # 未启动：永不注入
VOICE_ON = "on"      # 启动：总是注入
VOICE_AUTO = "auto"  # 自适应：满足条件才注入


def request_message_count(body: dict) -> int:
    """请求里的消息条数（messages + Responses 的 input 数组）。"""
    n = 0
    msgs = body.get("messages")
    if isinstance(msgs, list):
        n += len(msgs)
    items = body.get("input")
    if isinstance(items, list):
        n += len(items)
    return n


def voice_decision(cache_aware: bool = False, in_tokens: int = 0,
                   message_count: int = 0) -> dict[str, Any]:
    """判定本次请求要不要注入 Say less 规则，返回决策结果与判定依据。

    模式：
    - off（未启动）：永不注入
    - on（启动）  ：总是注入
    - auto（自适应）：满足条件才注入——默认模式

    auto 的判定顺序：
    1. 上游支持 prompt cache 且用户没勾「缓存优先也注入」→ 跳过。
       注入会改写 system 前缀，让可缓存前缀失效；命中价差大的上游
       （DeepSeek 命中价约原价 1/10）损失可能大于规则省下的输出。
    2. 会话既不够长、输入也不够大 → 跳过。
       短问答的输出本来就短，规则那约 400 token 的输入不划算。
    3. 其余 → 注入（会话够长或输入够大，说明这轮输出量不小，规则划算）。

    返回字段同时用于日志展示，所以 reason 要写成人话。
    """
    st = store.config.settings
    mode = st.voice_mode

    def _make(inject: bool, reason: str) -> dict[str, Any]:
        return {
            "inject": inject,
            "mode": mode,
            "reason": reason,
            "in_tokens": in_tokens,
            "messages": message_count,
            "cache_aware": cache_aware,
        }

    if mode == VOICE_OFF:
        return _make(False, "未启动：模式设为「未启动」，不注入")
    if mode == VOICE_ON:
        return _make(True, "已启动：模式设为「启动」，无条件注入")

    # ---- auto：先看缓存，再看这次请求值不值得 ----
    if cache_aware and not st.voice_ignore_cache:
        return _make(False, "自适应跳过：上游支持 prompt cache，注入会改写可缓存前缀"
                            "（可在设置里勾「缓存优先也注入」放行）")

    min_tok = max(0, st.voice_auto_min_tokens)
    min_msg = max(0, st.voice_auto_min_messages)
    big_tokens = in_tokens >= min_tok
    many_msgs = message_count >= min_msg
    if not big_tokens and not many_msgs:
        return _make(False, f"自适应跳过：会话 {message_count} 条消息、输入 {in_tokens} token，"
                            f"均未达阈值（需 {min_msg} 条 / {min_tok} token）")
    why = []
    if many_msgs:
        why.append(f"会话 {message_count} 条消息")
    if big_tokens:
        why.append(f"输入 {in_tokens} token")
    return _make(True, "自适应注入：" + "、".join(why))


def inject_voice(body: dict, decision: dict[str, Any] | None = None) -> bool:
    """把「电报体回复」规则附加到请求的 system（已注入过则跳过）。

    `decision` 由 voice_decision() 给出；不传就现场判一次。
    返回 True 表示本次确实注入了规则。
    """
    if decision is None:
        decision = voice_decision()
    if not decision.get("inject"):
        return False
    if isinstance(body.get("system"), str):
        if _VOICE_MARKER in body["system"]:
            return False
        body["system"] = body["system"].rstrip() + "\n\n" + VOICE_RULES
        return True
    if isinstance(body.get("system"), list):
        for b in body["system"]:
            if isinstance(b, dict) and _VOICE_MARKER in str(b.get("text", "")):
                return False
        body["system"] = list(body["system"]) + [{"type": "text", "text": VOICE_RULES}]
        return True
    # OpenAI：注入到首条 system 消息；没有就前置一条
    msgs = body.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "system":
                c = m.get("content")
                if isinstance(c, str):
                    if _VOICE_MARKER in c:
                        return False
                    m["content"] = c.rstrip() + "\n\n" + VOICE_RULES
                    return True
        body["messages"] = [{"role": "system", "content": VOICE_RULES}] + msgs
        return True
    return False
