"""熔断器状态机的单元测试（不需要启动服务）。

重点验证四个容易出错的点：

1. **抖动型上游靠错误率抓**——「失败 成功 失败 成功…」时连续计数会被
   一次成功清零，永远打不中阈值；靠窗口内错误率才能抓到。
2. **错误率判定需要最小样本数**——刚启动的节点 3 次全失败错误率 100%，
   但样本不足时不该据此熔断。
3. **客户端错误不算上游的失败**——400/404/422 是客户端请求本身有问题，
   计入熔断会误伤健康上游。
4. **半开试探的「在途」标记要释放**——客户端错误走的是提前返回分支，
   若忘了释放，该上游会被永久排除，再也进不了候选。

运行：python tests/test_circuit.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 单测隔离：强制使用临时数据目录，绝不读写正式 data/config.json。
import tempfile  # noqa: E402

os.environ["SWITCHBOARD_DATA_DIR"] = tempfile.mkdtemp(prefix="sb-test-data-")

from backend.config import Upstream, store  # noqa: E402
from backend.upstream_pool import UpstreamPool  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond else ""))


def fresh_upstream(name: str = "测试上游") -> Upstream:
    return Upstream(id=f"up_test_{name}", name=name, base_url="http://127.0.0.1:1/v1")


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

    # ---------------------------------------------------- 1. 连续失败达阈值 → open
    print("\n[1] 连续失败达阈值")
    up = fresh_upstream("consec")
    for _ in range(2):
        pool.report_failure(up, "boom")
    check("2 次失败还没 open（阈值 3）", up.circuit_state == "closed", up.circuit_state)
    pool.report_failure(up, "boom")
    check("3 次失败后 open", up.circuit_state == "open", up.circuit_state)
    check("open 时 healthy=False", up.healthy is False, str(up.healthy))
    check("open 时记录了开启时间", up.circuit_opened_at > 0, str(up.circuit_opened_at))

    # ---------------------------------------------------- 2. 连续失败的语义
    print("\n[2] 连续失败 vs 错误率")
    up = fresh_upstream("flaky")
    pool.report_failure(up, "boom")
    pool.report_failure(up, "boom")
    pool.report_success(up, 10)
    check("成功会重置「连续」失败计数（连续=不间断）",
          up.consecutive_failures == 0, str(up.consecutive_failures))
    check("但错误窗口里这次失败仍被记录（治抖动靠的是这个）",
          len(up.circuit_events) == 3, str(len(up.circuit_events)))
    # 抖动型上游：失败/成功交替 15 次，错误率 53% > 50% → 应被熔断
    up = fresh_upstream("flaky2")
    for i in range(15):
        if i % 2 == 0:
            pool.report_failure(up, "boom")
        else:
            pool.report_success(up, 10)
    check("抖动型上游（失败/成功交替）被错误率熔断",
          up.circuit_state == "open", up.circuit_state)

    # ---------------------------------------------------- 3. 错误率判定
    print("\n[3] 错误率阈值")
    up = fresh_upstream("rate")
    # 15 次：8 失败 7 成功 → 错误率 53% > 50%
    for i in range(15):
        if i % 2 == 0:
            pool.report_failure(up, "boom")
        else:
            pool.report_success(up, 10)
    check("样本够时错误率超阈值 → open", up.circuit_state == "open", up.circuit_state)

    up = fresh_upstream("rate_low")
    # 15 次：3 失败 12 成功 → 错误率 20% < 50%
    # 失败要交错排列，否则会先撞上「连续失败 3 次」那条判定路径，
    # 就测不到错误率这条了（见上面的 rate 用例）
    for i in range(15):
        if i in (0, 4, 8):
            pool.report_failure(up, "boom")
        else:
            pool.report_success(up, 10)
    check("样本够但错误率不高 → 不 open", up.circuit_state == "closed", up.circuit_state)

    # ---------------------------------------------------- 4. 最小样本数
    print("\n[4] 样本数不足不按错误率判定")
    up = fresh_upstream("small")
    # 连续失败 2 次（未达阈值 3），样本数 2 < min_requests 10
    pool.report_failure(up, "boom")
    pool.report_failure(up, "boom")
    check("样本不足（2 < 10）不因错误率 open", up.circuit_state == "closed", up.circuit_state)

    # ---------------------------------------------------- 5. 错误分类
    print("\n[5] 错误分类")
    up = fresh_upstream("client_err")
    for _ in range(5):
        pool.report_failure(up, "客户端参数错", status_code=400)
    check("400（客户端错）不计入熔断", up.circuit_state == "closed", up.circuit_state)
    check("400 不累加 consecutive_failures", up.consecutive_failures == 0,
          str(up.consecutive_failures))

    up = fresh_upstream("notfound")
    pool.report_failure(up, "不存在", status_code=404)
    check("404 不计入熔断", up.circuit_state == "closed", up.circuit_state)

    up = fresh_upstream("auth")
    pool.report_failure(up, "Key 失效", status_code=401)
    check("401 立即 open（Key 失效无需重试）", up.circuit_state == "open", up.circuit_state)

    up = fresh_upstream("forbidden")
    pool.report_failure(up, "禁止", status_code=403)
    check("403 立即 open", up.circuit_state == "open", up.circuit_state)

    up = fresh_upstream("ratelimit")
    for _ in range(3):
        pool.report_failure(up, "限流", status_code=429)
    check("429 计入熔断", up.circuit_state == "open", up.circuit_state)

    up = fresh_upstream("server_err")
    for _ in range(3):
        pool.report_failure(up, "上游炸了", status_code=500)
    check("5xx 计入熔断", up.circuit_state == "open", up.circuit_state)

    up = fresh_upstream("net_err")
    for _ in range(3):
        pool.report_failure(up, "ConnectError")
    check("网络异常计入熔断", up.circuit_state == "open", up.circuit_state)

    # ---------------------------------------------------- 6. 半开与恢复
    print("\n[6] 半开与恢复")
    up = fresh_upstream("halfopen")
    for _ in range(3):
        pool.report_failure(up, "boom")
    check("已 open", up.circuit_state == "open", up.circuit_state)
    # 冷却到期
    up.circuit_opened_at = time.time() - 120
    check("冷却到期后 _circuit_admits 放行", pool._circuit_admits(up, time.time()))
    check("放行后进入 half_open", up.circuit_state == "half_open", up.circuit_state)
    # 半开期连续成功 2 次才关闭
    pool.report_success(up, 10)
    check("半开 1 次成功仍是 half_open", up.circuit_state == "half_open", up.circuit_state)
    pool.report_success(up, 10)
    check("半开 2 次成功 → closed", up.circuit_state == "closed", up.circuit_state)
    check("关闭后失败计数清零", up.consecutive_failures == 0, str(up.consecutive_failures))
    check("关闭后 healthy=True", up.healthy is True, str(up.healthy))

    # 半开失败 → 回到 open
    up = fresh_upstream("halfopen_fail")
    for _ in range(3):
        pool.report_failure(up, "boom")
    up.circuit_opened_at = time.time() - 120
    pool._circuit_admits(up, time.time())
    check("进入 half_open", up.circuit_state == "half_open", up.circuit_state)
    pool.report_failure(up, "还是不行")
    check("半开失败 → 回到 open", up.circuit_state == "open", up.circuit_state)

    # ---------------------------------------------------- 7. 并发试探只放行一个
    print("\n[7] 半开期并发只放行一个试探")
    up = fresh_upstream("inflight")
    for _ in range(3):
        pool.report_failure(up, "boom")
    up.circuit_opened_at = time.time() - 120
    first = pool._circuit_admits(up, time.time())
    second = pool._circuit_admits(up, time.time())
    check("第一次放行", first is True, str(first))
    check("第二次不再放行（已有试探在途）", second is False, str(second))

    # ---------------------------------------------------- 8. candidates 排除熔断节点
    print("\n[8] candidates 排除熔断节点")
    saved = store.list_upstreams()
    try:
        good = fresh_upstream("good")
        bad = fresh_upstream("bad")
        bad.circuit_state = "open"
        bad.circuit_opened_at = time.time()
        bad.cooldown_until = 0.0
        store.config.upstreams = [good, bad]
        ids = [u.id for u in pool.candidates()]
        check("正常上游在候选里", good.id in ids, str(ids))
        check("熔断中的上游被排除", bad.id not in ids, str(ids))
    finally:
        store.config.upstreams = saved

    # ---------------------------------------------------- 9. 熔断开关关闭
    print("\n[9] 熔断开关")
    store.config.settings.circuit_enabled = False
    up = fresh_upstream("disabled")
    for _ in range(5):
        pool.report_failure(up, "boom")
    check("关闭熔断后不进入 open 状态", up.circuit_state == "closed", up.circuit_state)
    check("关闭熔断后仍按旧逻辑累计失败数", up.consecutive_failures == 5,
          str(up.consecutive_failures))
    store.config.settings.circuit_enabled = True

    # ---------------------------------------------------- 10. reset_state 重置熔断
    print("\n[10] reset_state")
    up = fresh_upstream("reset")
    for _ in range(3):
        pool.report_failure(up, "boom")
    check("已 open", up.circuit_state == "open", up.circuit_state)
    saved = store.list_upstreams()
    try:
        store.config.upstreams = [up]
        pool.reset_state()
        check("reset 后回到 closed", up.circuit_state == "closed", up.circuit_state)
        check("reset 后失败计数清零", up.consecutive_failures == 0,
              str(up.consecutive_failures))
    finally:
        store.config.upstreams = saved

    # ---------------------------------------------------- 11. 健康检查不重置熔断
    print("\n[11] 探活成功不重置熔断")
    up = fresh_upstream("health")
    for _ in range(3):
        pool.report_failure(up, "boom")
    check("已 open", up.circuit_state == "open", up.circuit_state)
    pool.report_health_ok(up, 5)
    check("探活成功后仍是 open", up.circuit_state == "open", up.circuit_state)
    check("探活只更新了延迟", up.last_latency_ms == 5, str(up.last_latency_ms))

    # ---------------------------------------------------- 11.5 客户端错误不卡死半开
    print("\n[11.5] 客户端错误不卡死半开试探")
    up = fresh_upstream("clienterr_half")
    for _ in range(3):
        pool.report_failure(up, "boom")
    up.circuit_opened_at = time.time() - 120
    pool._circuit_admits(up, time.time())
    check("已进入 half_open", up.circuit_state == "half_open", up.circuit_state)
    check("试探在途标记已置", up.id in pool._half_open_inflight)
    # 客户端错误（400）不该计入熔断，但必须释放「在途」标记，
    # 否则这个上游会被永久排除，再也进不了候选。
    pool.report_failure(up, "客户端参数错", status_code=400)
    check("400 后不计入熔断（仍 half_open）",
          up.circuit_state == "half_open", up.circuit_state)
    check("400 后释放了在途标记（不会永久排除该上游）",
          up.id not in pool._half_open_inflight)
    check("冷却到期后还能再次试探", pool._circuit_admits(up, time.time()))

    # ---------------------------------------------------- 12. 错误率窗口裁剪
    print("\n[12] 错误率窗口会过期")
    up = fresh_upstream("window")
    for i in range(10):
        pool.report_failure(up, "boom")
    # 把窗口内的失败事件改成很久以前 → 不该再计入
    old = time.time() - 3600
    up.circuit_events = [(old, False)] * 10
    pool.report_success(up, 10)
    check("窗口外的事件被裁掉", len(up.circuit_events) <= 2,
          str(len(up.circuit_events)))

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("失败项：")
        for name, _ok, detail in failed:
            print(f"  - {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
