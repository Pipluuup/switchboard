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


def _shrink_block_list(blocks: list, min_chars: int) -> int:
    """压缩 Anthropic blocks / OpenAI 多模态 parts 里的文本型内容。"""
    saved = 0
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            new, n = _shrink_text(b["text"], min_chars)
            b["text"], saved = new, saved + n
        elif b.get("type") == "tool_result":
            c = b.get("content")
            if isinstance(c, str):
                new, n = _shrink_text(c, min_chars)
                b["content"], saved = new, saved + n
            elif isinstance(c, list):
                saved += _shrink_block_list(c, min_chars)
    return saved


def _shrink_message(m: dict, min_chars: int) -> int:
    """压缩单条消息：纯文本 content 直接处理，块列表交给 _shrink_block_list。

    同时覆盖 OpenAI 的 tool 角色消息（content 为字符串的工具结果）。
    """
    c = m.get("content")
    if isinstance(c, str):
        new, n = _shrink_text(c, min_chars)
        m["content"] = new
        return n
    if isinstance(c, list):
        return _shrink_block_list(c, min_chars)
    return 0


def compress_body(body: dict) -> tuple[dict, int]:
    """按设置压缩请求体（原地修改），返回 (body, 省略的字符数)。"""
    st = store.config.settings
    if not st.caveman_enabled:
        return body, 0
    min_chars = max(64, st.caveman_min_chars)
    saved = 0
    if st.caveman_compress_system:
        sys = body.get("system")
        if isinstance(sys, str):
            new, n = _shrink_text(sys, min_chars)
            body["system"], saved = new, saved + n
        elif isinstance(sys, list):
            saved += _shrink_block_list(sys, min_chars)
    msgs = body.get("messages")
    keep = max(1, st.caveman_keep_recent)
    if isinstance(msgs, list) and len(msgs) > keep:
        for m in msgs[:-keep]:
            if isinstance(m, dict):
                saved += _shrink_message(m, min_chars)
    return body, saved


# ---------------- Say less：voice 规则注入 ----------------


def inject_voice(body: dict) -> bool:
    """按设置把「电报体回复」规则附加到请求的 system（已注入过则跳过）。"""
    if not store.config.settings.voice_enabled:
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
