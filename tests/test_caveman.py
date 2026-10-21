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


def test_cache_capable_detection():
    """上游缓存能力识别。"""
    class U:
        def __init__(self, b): self.base_url = b
    assert caveman.upstream_supports_cache(U("https://api.deepseek.com/anthropic"))
    assert caveman.upstream_supports_cache(U("https://api.agnes-ai.cn/v1"))
    assert not caveman.upstream_supports_cache(U("https://opencode.ai/inference/go/v1"))
    assert not caveman.upstream_supports_cache(U("http://127.0.0.1:11434/v1"))
    print("✅ 上游缓存能力识别正确")


if __name__ == "__main__":
    test_code_block_not_broken()
    test_url_and_path_preserved()
    test_tool_pairing_protected()
    test_cache_aware_weaker()
    test_deterministic()
    test_stats()
    test_cache_capable_detection()
    print("\n全部通过")
