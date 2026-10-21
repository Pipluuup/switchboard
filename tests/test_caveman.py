"""Caveman 引擎单元测试：片段保护、tool 配对保护、缓存感知、压缩统计。

运行：python tests/test_caveman.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import caveman  # noqa: E402
from backend.config import store  # noqa: E402


def _enable(**over):
    """临时改设置（测试用），返回原值以便恢复。"""
    st = store.config.settings
    old = {k: getattr(st, k) for k in over}
    for k, v in over.items():
        setattr(st, k, v)
    return old


def _restore(old):
    for k, v in old.items():
        setattr(store.config.settings, k, v)


def test_code_block_not_broken():
    """代码块在压缩后必须原样保留（不能被腰斩）。"""
    code = "```python\ndef foo():\n    return 'x'  # 关键注释\n```"
    filler = ("普通文字内容。" * 200)
    text = f"前面说明\n{code}\n后面还有 {filler}"

    old = _enable(caveman_enabled=True, caveman_keep_recent=1, caveman_min_chars=100,
                  caveman_compress_system=False)
    try:
        body = {"model": "m", "messages": [
            {"role": "user", "content": text},
            {"role": "user", "content": "现在的问题"},
        ]}
        caveman.compress_body(body)
        out = body["messages"][0]["content"]
        assert code in out, "代码块被破坏！"
        assert "关键注释" in out
        print("✅ 代码块保护：代码原样保留")
    finally:
        _restore(old)


def test_url_and_path_preserved():
    """URL 与文件路径在压缩后必须保留。"""
    text = ("详见 " + "https://example.com/api/v1/docs?x=1 " * 50 +
            "文件 src/backend/handler.py 里 MAX_RETRIES 常量")

    old = _enable(caveman_enabled=True, caveman_keep_recent=1, caveman_min_chars=100,
                  caveman_compress_system=False)
    try:
        body = {"model": "m", "messages": [
            {"role": "user", "content": text},
            {"role": "user", "content": "问题"},
        ]}
        caveman.compress_body(body)
        out = body["messages"][0]["content"]
        assert "https://example.com/api/v1/docs?x=1" in out, "URL 丢失"
        assert "src/backend/handler.py" in out, "路径丢失"
        assert "MAX_RETRIES" in out, "常量丢失"
        print("✅ 片段保护：URL / 路径 / 常量均保留")
    finally:
        _restore(old)


def test_tool_pairing_protected():
    """含 tool_use / tool_result 的消息不参与压缩（配对不被拆散）。"""
    long_tool_result = "命令输出\n" + ("line of output\n" * 300)
    body = {"model": "m", "messages": [
        {"role": "user", "content": "跑个命令"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_01", "name": "bash", "input": {"cmd": "ls"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_01", "content": long_tool_result}]},
        {"role": "user", "content": "现在的问题"},
    ]}

    old = _enable(caveman_enabled=True, caveman_keep_recent=1, caveman_min_chars=100,
                  caveman_compress_system=False)
    try:
        caveman.compress_body(body)
        tr = body["messages"][2]["content"][0]
        assert len(tr["content"]) == len(long_tool_result), "tool_result 被压缩了！"
        assert body["messages"][1]["content"][0]["type"] == "tool_use"
        print("✅ tool 配对保护：tool_result 完整未压缩")
    finally:
        _restore(old)


def test_cache_aware_weaker():
    """缓存优先模式应比常规模式压缩得少（少扰动可缓存前缀）。"""
    text = "日志：\n" + "\n".join(f"2026-01-01 10:{i:02d}:00 INFO batch {i} ok" for i in range(200))
    msgs = [{"role": "user", "content": text}] + \
           [{"role": "assistant", "content": f"轮{i}"} for i in range(6)] + \
           [{"role": "user", "content": "问题"}]

    old = _enable(caveman_enabled=True, caveman_keep_recent=4, caveman_min_chars=400,
                  caveman_compress_system=False)
    try:
        b1 = {"model": "m", "messages": [dict(m) for m in msgs]}
        caveman.compress_body(b1, cache_aware=False)
        b2 = {"model": "m", "messages": [dict(m) for m in msgs]}
        caveman.compress_body(b2, cache_aware=True)
        n1 = len(json.dumps(b1, ensure_ascii=False))
        n2 = len(json.dumps(b2, ensure_ascii=False))
        assert n2 > n1, f"缓存优先应保留更多内容：{n2} vs {n1}"
        print(f"✅ 缓存感知：常规 {n1} 字符 vs 缓存优先 {n2} 字符（保留更多）")
    finally:
        _restore(old)


def test_deterministic():
    """同一输入压缩结果必须逐字节一致（缓存命中的前提）。"""
    text = "日志：\n" + "\n".join(f"2026-01-01 10:{i:02d}:00 INFO batch {i} ok" for i in range(120))
    msgs = [{"role": "user", "content": text}] + \
           [{"role": "assistant", "content": f"轮{i}"} for i in range(6)]

    old = _enable(caveman_enabled=True, caveman_keep_recent=4, caveman_min_chars=400,
                  caveman_compress_system=False)
    try:
        outs = []
        for _ in range(5):
            b = {"model": "m", "messages": [dict(m) for m in msgs]}
            caveman.compress_body(b)
            outs.append(json.dumps(b, ensure_ascii=False, sort_keys=True))
        assert len(set(outs)) == 1, "压缩结果不确定！"
        print("✅ 确定性：5 次压缩结果完全一致")
    finally:
        _restore(old)


def test_stats():
    """压缩统计：原始/压缩后字符数、节省率、模式、能力列表。"""
    text = "日志：\n" + "\n".join(f"2026-01-01 10:{i:02d}:00 INFO batch {i} ok" for i in range(200))
    msgs = [{"role": "user", "content": text}] + \
           [{"role": "assistant", "content": f"轮{i}"} for i in range(6)] + \
           [{"role": "user", "content": "问题"}]

    old = _enable(caveman_enabled=True, caveman_keep_recent=4, caveman_min_chars=400,
                  caveman_compress_system=False)
    try:
        before = {"model": "m", "messages": [dict(m) for m in msgs]}
        after = {"model": "m", "messages": [dict(m) for m in msgs]}
        saved = caveman.compress_body(after)[1]
        st = caveman.stats(before, after, saved, cache_aware=True, voice_injected=True)
        assert st["orig_chars"] > st["new_chars"]
        assert 0 < st["saved_pct"] < 100
        assert st["mode"] == "cache-aware"
        assert "Say less" in st["capabilities"]
        assert "缓存优先" in st["capabilities"]
        print(f"✅ 压缩统计：{st['orig_chars']}→{st['new_chars']} 字符，省 {st['saved_pct']}%，"
              f"能力 {st['capabilities']}")
    finally:
        _restore(old)


def test_stats_voice_overhead_not_counted_as_saving():
    """注入规则会让请求变大——不能把这份开销算成「省了多少」。

    拿注入后的体积算节省率会得出负数（实测 -1234%），必须用
    「压缩后、注入前」的中间体积来算压缩收益，规则开销单独记。
    """
    before = {"model": "m", "system": "原始 system", "messages": [{"role": "user", "content": "问题"}]}
    after = json.loads(json.dumps(before))
    compressed_chars = len(json.dumps(after, ensure_ascii=False))
    after["system"] = after["system"] + "\n\n" + caveman.VOICE_RULES

    st = caveman.stats(before, after, 0, voice_injected=True, compressed_chars=compressed_chars)
    assert st["saved_pct"] == 0.0, f"没压缩就不该有节省率：{st['saved_pct']}"
    assert st["voice_added_chars"] > 0, "规则开销应单独记"
    assert st["new_chars"] > st["compressed_chars"]
    print(f"✅ 规则开销单独计：压缩省 {st['saved_pct']}%，规则开销 +{st['voice_added_chars']} 字符")



def test_cache_capable_detection():
    """上游缓存能力识别。"""
    class U:
        def __init__(self, b): self.base_url = b
    assert caveman.upstream_supports_cache(U("https://api.deepseek.com/anthropic"))
    assert caveman.upstream_supports_cache(U("https://api.agnes-ai.cn/v1"))
    assert not caveman.upstream_supports_cache(U("https://opencode.ai/inference/go/v1"))
    assert not caveman.upstream_supports_cache(U("http://127.0.0.1:11434/v1"))
    print("✅ 上游缓存能力识别正确")


# ---------------- Say less 三态 ----------------

VOICE_KEYS = ("voice_mode", "voice_auto_min_tokens", "voice_auto_min_messages",
              "voice_ignore_cache")


def _voice(**over):
    """临时改 Say less 设置（测试用）。"""
    old = _enable(**over)
    return old


def test_voice_off_never_injects():
    """「未启动」= 永不注入，条件再满足也不注入。"""
    old = _voice(voice_mode="off")
    try:
        d = caveman.voice_decision(cache_aware=False, in_tokens=99999, message_count=99)
        assert not d["inject"], "off 模式不该注入"
        assert d["mode"] == "off"
        body = {"model": "m", "system": "原有 system", "messages": []}
        assert not caveman.inject_voice(body, d)
        assert body["system"] == "原有 system", "system 被改写了"
        print("✅ Say less「未启动」：任何条件都不注入")
    finally:
        _restore(old)


def test_voice_on_always_injects():
    """「启动」= 总是注入，哪怕上游支持缓存、请求也很小。"""
    old = _voice(voice_mode="on")
    try:
        d = caveman.voice_decision(cache_aware=True, in_tokens=1, message_count=1)
        assert d["inject"], "on 模式必须注入"
        body = {"model": "m", "system": "原有 system", "messages": []}
        assert caveman.inject_voice(body, d)
        assert caveman._VOICE_MARKER in body["system"]
        print("✅ Say less「启动」：无条件注入")
    finally:
        _restore(old)


def test_voice_auto_skips_small_request():
    """「自适应」：会话短且输入小 → 跳过（规则的 400 token 不划算）。"""
    old = _voice(voice_mode="auto", voice_auto_min_tokens=2000, voice_auto_min_messages=4)
    try:
        d = caveman.voice_decision(cache_aware=False, in_tokens=100, message_count=2)
        assert not d["inject"], "短请求不该注入"
        assert "自适应跳过" in d["reason"]
        print(f"✅ Say less「自适应」跳过小请求：{d['reason']}")
    finally:
        _restore(old)


def test_voice_auto_injects_long_session():
    """「自适应」：会话够长 → 注入。"""
    old = _voice(voice_mode="auto", voice_auto_min_tokens=2000, voice_auto_min_messages=4)
    try:
        d = caveman.voice_decision(cache_aware=False, in_tokens=100, message_count=6)
        assert d["inject"], "长会话该注入"
        assert "自适应注入" in d["reason"]
        print(f"✅ Say less「自适应」注入长会话：{d['reason']}")
    finally:
        _restore(old)


def test_voice_auto_injects_big_input():
    """「自适应」：消息少但输入很大 → 也注入（输出量必然不小）。"""
    old = _voice(voice_mode="auto", voice_auto_min_tokens=2000, voice_auto_min_messages=4)
    try:
        d = caveman.voice_decision(cache_aware=False, in_tokens=5000, message_count=1)
        assert d["inject"], "大输入该注入"
        print(f"✅ Say less「自适应」注入大输入：{d['reason']}")
    finally:
        _restore(old)


def test_voice_auto_respects_cache():
    """「自适应」：上游支持缓存且没勾「缓存优先也注入」→ 跳过。"""
    old = _voice(voice_mode="auto", voice_auto_min_tokens=2000, voice_auto_min_messages=4,
                 voice_ignore_cache=False)
    try:
        d = caveman.voice_decision(cache_aware=True, in_tokens=99999, message_count=99)
        assert not d["inject"], "缓存上游不该注入"
        assert "prompt cache" in d["reason"]
    finally:
        _restore(old)

    # 勾了「缓存优先也注入」→ 放行
    old = _voice(voice_mode="auto", voice_auto_min_tokens=2000, voice_auto_min_messages=4,
                 voice_ignore_cache=True)
    try:
        d = caveman.voice_decision(cache_aware=True, in_tokens=99999, message_count=99)
        assert d["inject"], "勾了忽略缓存就该注入"
        print("✅ Say less「自适应」：缓存上游默认跳过，勾选后放行")
    finally:
        _restore(old)


def test_voice_decision_in_stats():
    """决策结果要进 stats（日志「优化」列靠它展示判定依据）。"""
    old = _voice(voice_mode="auto", voice_auto_min_tokens=2000, voice_auto_min_messages=4)
    try:
        d = caveman.voice_decision(cache_aware=False, in_tokens=100, message_count=2)
        st = caveman.stats({"a": 1}, {"a": 1}, 0, voice_injected=False, voice=d)
        assert "voice" in st
        assert st["voice"]["inject"] is False
        assert st["voice"]["mode"] == "auto"
        assert st["voice"]["reason"]
        print("✅ 决策结果进 stats：日志可展示判定依据")
    finally:
        _restore(old)


def test_request_message_count():
    """消息数统计：messages 与 Responses 的 input 都算。"""
    body = {"messages": [{"role": "user"}, {"role": "assistant"}], "input": [{"role": "user"}]}
    assert caveman.request_message_count(body) == 3
    assert caveman.request_message_count({"messages": []}) == 0
    assert caveman.request_message_count({}) == 0
    print("✅ 请求消息数统计正确")


if __name__ == "__main__":
    test_code_block_not_broken()
    test_url_and_path_preserved()
    test_tool_pairing_protected()
    test_cache_aware_weaker()
    test_deterministic()
    test_stats()
    test_stats_voice_overhead_not_counted_as_saving()
    test_cache_capable_detection()
    test_voice_off_never_injects()
    test_voice_on_always_injects()
    test_voice_auto_skips_small_request()
    test_voice_auto_injects_long_session()
    test_voice_auto_injects_big_input()
    test_voice_auto_respects_cache()
    test_voice_decision_in_stats()
    test_request_message_count()
    print("\n全部通过")
