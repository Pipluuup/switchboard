"""故障转移链的单元测试（不需要启动服务）。

覆盖四类容易出错的点：

1. **链的展开**——固定上游按序展开；未固定上游按调度策略展开成多个候选；
   不可用的档（停用/冷却/熔断/不存在）被标记跳过并带上原因。
2. **切换条件**——5xx/429/404 切下一档；400/401/403 不切（客户端或鉴权问题）。
3. **只读规划**——规划链不改变熔断状态、不占用半开试探名额。
   （否则「还没发请求就把上游标成半开」，白占一个试探机会。）
4. **链与老配置的兼容**——没有 `chain` 的老映射退化成单档，行为不变。

运行：python tests/test_failover_chain.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 单测隔离：强制使用临时数据目录，绝不读写正式 data/config.json。
# 本测试会改写 store 里的上游/映射并触发落盘——曾经直接把本机正式配置覆盖掉。
import tempfile  # noqa: E402

os.environ["SWITCHBOARD_DATA_DIR"] = tempfile.mkdtemp(prefix="sb-test-data-")

from backend.config import ChainHop, ModelMapping, store  # noqa: E402
from backend.proxy_routes import should_failover  # noqa: E402
from backend.upstream_pool import UpstreamPool  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond else ""))


def fresh_upstream(uid: str, name: str = "", **kw) -> "object":
    from backend.config import Upstream
    return Upstream(id=uid, name=name or uid, base_url="http://127.0.0.1:1/v1", **kw)


def reset_settings() -> None:
    s = store.config.settings
    s.circuit_enabled = True
    s.failure_threshold = 3
    s.cooldown_seconds = 60
    s.circuit_error_rate_threshold = 0.5
    s.circuit_min_requests = 10
    s.circuit_open_seconds = 60
    s.circuit_half_open_success = 2
    s.circuit_window_seconds = 120


def main() -> int:
    pool = UpstreamPool()
    reset_settings()
    saved_upstreams = store.list_upstreams()

    try:
        # ------------------------------------------------- 1. 切换条件
        print("\n[1] 切换条件")
        check("500 切下一档", should_failover(500))
        check("502 切下一档", should_failover(502))
        check("503 切下一档", should_failover(503))
        check("429 切下一档（限流）", should_failover(429))
        check("404 切下一档（模型不存在/下线）", should_failover(404))
        check("400 不切（客户端参数错）", not should_failover(400))
        check("422 不切（客户端参数错）", not should_failover(422))
        check("401 不切（Key 失效）", not should_failover(401))
        check("403 不切（Key 被拒）", not should_failover(403))
        check("200 不切（成功）", not should_failover(200))

        # ------------------------------------------------- 2. 链的展开
        print("\n[2] 链的展开")
        a = fresh_upstream("up_a", "A")
        b = fresh_upstream("up_b", "B")
        c = fresh_upstream("up_c", "C")
        store.config.upstreams = [a, b, c]
        for u in (a, b, c):
            u.enabled = True
            u.circuit_state = "closed"
            u.cooldown_until = 0.0
            u.consecutive_failures = 0

        hops = [ChainHop(upstream_id="up_a", target="model-x"),
                ChainHop(upstream_id="up_b", target="model-y"),
                ChainHop(upstream_id="up_c", target="model-z")]
        plan = pool.plan_chain(hops, fallback_model="fallback")
        check("链展开成 3 档", len(plan) == 3, str(len(plan)))
        check("第 1 档是 A", plan[0].upstream is a and plan[0].target == "model-x",
              str(plan[0]))
        check("第 2 档是 B", plan[1].upstream is b and plan[1].target == "model-y",
              str(plan[1]))
        check("第 3 档是 C", plan[2].upstream is c and plan[2].target == "model-z",
              str(plan[2]))
        check("每档都带自己的模型名（可与上游不同）",
              [e.target for e in plan] == ["model-x", "model-y", "model-z"],
              str([e.target for e in plan]))

        # ------------------------------------------------- 3. 不可用的档被跳过
        print("\n[3] 不可用的档被标记跳过")
        b.enabled = False
        plan = pool.plan_chain(hops, fallback_model="fallback")
        check("停用的上游被跳过", plan[1].skipped == "上游已停用", str(plan[1]))
        check("跳过的档不占上游", plan[1].upstream is b, str(plan[1].upstream))
        b.enabled = True

        b.cooldown_until = time.time() + 60
        plan = pool.plan_chain(hops, fallback_model="fallback")
        check("冷却中的上游被跳过", plan[1].skipped == "冷却中", str(plan[1]))
        b.cooldown_until = 0.0

        b.circuit_state = "open"
        b.circuit_opened_at = time.time()
        plan = pool.plan_chain(hops, fallback_model="fallback")
        check("熔断中的上游被跳过", plan[1].skipped == "熔断中", str(plan[1]))
        b.circuit_state = "closed"
        b.circuit_opened_at = 0.0

        plan = pool.plan_chain([ChainHop(upstream_id="up_nope", target="m")],
                               fallback_model="fallback")
        check("上游不存在被标记", plan[0].skipped == "上游不存在", str(plan[0]))

        # ------------------------------------------------- 4. 只读规划
        print("\n[4] 规划不改变熔断状态")
        b.circuit_state = "open"
        b.circuit_opened_at = time.time() - 120  # 冷却已到期
        pool.plan_chain([ChainHop(upstream_id="up_b", target="m")], fallback_model="m")
        check("规划不把 open 改成 half_open", b.circuit_state == "open", b.circuit_state)
        check("规划不占用半开试探名额", "up_b" not in pool._half_open_inflight)
        b.circuit_state = "closed"
        b.circuit_opened_at = 0.0

        # 自动调度档也不能占名额
        pool._half_open_inflight.clear()
        b.circuit_state = "open"
        b.circuit_opened_at = time.time() - 120
        pool.plan_chain([ChainHop(upstream_id="", target="m")], fallback_model="m")
        check("自动调度规划也不占半开名额", not pool._half_open_inflight,
              str(pool._half_open_inflight))
        b.circuit_state = "closed"
        b.circuit_opened_at = 0.0

        # ------------------------------------------------- 5. 自动调度档
        print("\n[5] 未固定上游的档按调度展开")
        a.priority, b.priority, c.priority = 10, 20, 30
        for u in (a, b, c):
            u.max_retries = 2
        plan = pool.plan_chain([ChainHop(upstream_id="", target="m")], fallback_model="m")
        check("自动调度展开成多个候选", len(plan) >= 2, str(len(plan)))
        check("按优先级排序，第一个是 A", plan[0].upstream is a, str(plan[0].upstream))
        check("后续是 B、C", [e.upstream.id for e in plan[1:]] == ["up_b", "up_c"],
              str([e.upstream.id for e in plan[1:]]))

        # max_retries 限制候选数量：max_retries=1 → 最多 2 个
        for u in (a, b, c):
            u.max_retries = 1
        plan = pool.plan_chain([ChainHop(upstream_id="", target="m")], fallback_model="m")
        check("max_retries=1 → 最多尝试 2 个", len(plan) == 2, str(len(plan)))
        for u in (a, b, c):
            u.max_retries = 2

        # ------------------------------------------------- 6. 老配置兼容
        print("\n[6] 老配置（无 chain）退化成单档")
        old = ModelMapping(alias="Claude", target="minimax-m3.1-flash-free",
                           upstream_id="up_a")
        hs = old.hops()
        check("老映射产出 1 档", len(hs) == 1, str(len(hs)))
        check("老映射那档保留上游与模型",
              hs[0].upstream_id == "up_a" and hs[0].target == "minimax-m3.1-flash-free",
              str(hs[0]))

        # ------------------------------------------------- 7. 链优先于老字段
        print("\n[7] 链非空时以链为准")
        chained = ModelMapping(
            alias="Claude", target="old-target", upstream_id="up_a",
            chain=[ChainHop(upstream_id="up_b", target="new-model")],
        )
        hs = chained.hops()
        check("链覆盖 target/upstream_id", len(hs) == 1 and hs[0].upstream_id == "up_b"
              and hs[0].target == "new-model", str(hs))

        # 链里全是空档 → 视为无链
        empty = ModelMapping(alias="E", target="t", upstream_id="up_a",
                             chain=[ChainHop()])
        check("链里空档被过滤掉，退回老字段",
              len(empty.hops()) == 1 and empty.hops()[0].target == "t",
              str(empty.hops()))

        # ------------------------------------------------- 8. resolve_chain
        print("\n[8] resolve_chain")
        saved_mappings = store.config.mappings
        try:
            store.config.mappings = [
                ModelMapping(alias="Claude", target="m1", upstream_id="up_a"),
                ModelMapping(
                    alias="Chain", target="m2", upstream_id="up_b",
                    chain=[ChainHop(upstream_id="up_a", target="c1"),
                           ChainHop(upstream_id="up_b", target="c2")],
                ),
                ModelMapping(alias="Off", target="m3", enabled=False),
            ]
            hs = store.resolve_chain("Claude")
            check("命中老映射 → 单档", len(hs) == 1 and hs[0].target == "m1", str(hs))
            hs = store.resolve_chain("Chain")
            check("命中链式映射 → 2 档", len(hs) == 2, str(len(hs)))
            check("链式映射的档序正确",
                  [h.upstream_id for h in hs] == ["up_a", "up_b"], str(hs))
            hs = store.resolve_chain("Off")
            check("停用的映射不生效 → 走公共调度",
                  len(hs) == 1 and hs[0].upstream_id == "" and hs[0].target == "Off",
                  str(hs))
            hs = store.resolve_chain("NoSuchAlias")
            check("无映射 → 单档自动调度",
                  len(hs) == 1 and hs[0].upstream_id == "", str(hs))
        finally:
            store.config.mappings = saved_mappings

        # ------------------------------------------------- 9. update_mapping 校验
        print("\n[9] update_mapping 把 chain 转成 ChainHop 对象")
        saved_mappings = store.config.mappings
        try:
            m = ModelMapping(alias="X", target="t")
            store.config.mappings = [m]
            store.update_mapping(m.id, {
                "chain": [{"upstream_id": "up_a", "target": "z1"},
                          {"upstream_id": "up_b", "target": "z2"}],
            })
            got = store.config.mappings[0]
            check("chain 被转成 ChainHop（不是裸 dict）",
                  isinstance(got.chain[0], ChainHop), str(type(got.chain[0])))
            check("chain 内容正确",
                  [(h.upstream_id, h.target) for h in got.hops()] ==
                  [("up_a", "z1"), ("up_b", "z2")], str(got.hops()))
        finally:
            store.config.mappings = saved_mappings

        # ------------------------------------------------- 10. 404 不熔断但会切换
        print("\n[10] 404 的熔断语义")
        up404 = fresh_upstream("up_404")
        store.config.upstreams = [up404]
        try:
            pool.report_failure(up404, "model not found", status_code=404)
            check("404 不计入熔断（上游本身是好的）",
                  up404.circuit_state == "closed", up404.circuit_state)
            check("404 不累加连续失败计数", up404.consecutive_failures == 0,
                  str(up404.consecutive_failures))
        finally:
            store.config.upstreams = saved_upstreams

    finally:
        store.config.upstreams = saved_upstreams

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("失败项：")
        for name, _ok, detail in failed:
            print(f"  - {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
