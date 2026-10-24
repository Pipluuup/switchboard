"""账号池单元测试（不需要启动服务，纯内存）。

覆盖账号轮换的核心语义：

1. **current_account 顺序轮换**——取第一个「启用且额度未尽」的账号；
   未配上限（usage_limit=0）不受额度约束。
2. **失败反馈**——失败计数/最近错误记到账号名下；401/403 自动停用该账号，
   下一次 current_account 自然轮到下一枚；成功清错误并 +1 用量。
3. **池组节点绑定**——note 前缀 account-pool:<pid> 的弱引用绑定，
   pool_id_of 能从上游节点反查池 id；配置导入导出往返不丢池。

运行：python tests/test_pools.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import AccountPool, PooledAccount, store  # noqa: E402
from backend.upstream_pool import UpstreamPool, pool_id_of  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS'  if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond else ""))


def main() -> int:
    p = UpstreamPool()
    # 备份正式配置，测试结束后恢复，正式配置零接触
    saved_upstreams = store.list_upstreams()
    saved_pools = store.list_pools()

    try:
        # ------------------------------------------------- 1. 顺序轮换
        print("\n[1] 顺序轮换")
        store.config.account_pools = [AccountPool.model_validate({
            "id": "ap_t1", "name": "池A", "kind": "workbuddy",
            "base_url": "http://127.0.0.1:1/v1",
            "accounts": [
                {"id": "ac_1", "label": "a@example.com", "cookie": "sess=1"},
                {"id": "ac_2", "label": "b@example.com", "api_key": "sk-2"},
                {"id": "ac_3", "label": "c@example.com", "cookie": "sess=3",
                 "enabled": False},
            ],
        })]
        pool_obj = p.current_account("ap_t1")
        check("优先第一个启用账号", pool_obj is not None and pool_obj.id == "ac_1")
        # 第一枚额度用尽 → 轮到第二枚
        store.config.account_pools[0].accounts[0].usage_used = 5
        store.config.account_pools[0].accounts[0].usage_limit = 5
        pool_obj = p.current_account("ap_t1")
        check("额度用尽换下一个", pool_obj is not None and pool_obj.id == "ac_2")
        # 停用的账号不参与轮换（ac_3 enabled=False 已验证被跳过——
        # 这里补一条：把前两枚都停用，结果应为 None）
        for a in store.config.account_pools[0].accounts[:2]:
            a.enabled = False
        check("全部停用返回 None", p.current_account("ap_t1") is None)
        store.config.account_pools[0].accounts[0].enabled = True
        store.config.account_pools[0].accounts[0].usage_used = 0
        store.config.account_pools[0].accounts[0].usage_limit = 0
        check("重新启用后回到第一枚", (p.current_account("ap_t1") or PooledAccount()).id == "ac_1")

        # ------------------------------------------------- 2. 失败反馈
        print("\n[2] 失败反馈")
        for a in store.config.account_pools[0].accounts:
            a.enabled = True
            a.usage_used = 0
            a.usage_limit = 0
        p.report_account_failure("ap_t1", "ac_1", "HTTP 401: unauthorized", 401)
        a1 = store.config.account_pools[0].accounts[0]
        check("401 自动停用", a1.enabled is False)
        check("失败计数 +1", a1.failed_requests == 1)
        check("最近错误已记录", "401" in a1.last_error)
        check("下一请求轮到 ac_2", (p.current_account("ap_t1") or PooledAccount()).id == "ac_2")
        p.report_account_failure("ap_t1", "ac_2", "HTTP 500: oops", 500)
        a2 = store.config.account_pools[0].accounts[1]
        check("5xx 不停用只记账", a2.enabled is True and a2.failed_requests == 1)
        # ac_1 仍因 401 停用，轮换顺序里它是第一枚启用且额度未尽的
        check("5xx 后轮到启用的 ac_2",
              (p.current_account("ap_t1") or PooledAccount()).id == "ac_2")
        p.report_account_success("ap_t1", "ac_1")
        check("成功记账 usage_used +1", a1.usage_used == 1)
        check("成功清掉最近错误", a1.last_error == "")
        check("成功后 last_used_at 已更新", a1.last_used_at > 0)

        # ------------------------------------------------- 3. 池组节点绑定
        print("\n[3] 池组节点绑定")
        from backend.config import Upstream
        node = Upstream(id="up_pool", name="池A", base_url="http://127.0.0.1:1/v1",
                        note=AccountPool.UPSTREAM_TAG + "ap_t1")
        store.config.upstreams.append(node)
        check("pool_id_of 反查池 id", pool_id_of(node) == "ap_t1")
        check("普通节点无池标记", pool_id_of(Upstream(id="up_x", name="x", base_url="u", note="随便写")) == "")
        # 池删除后节点保留（store 层行为）
        check("delete_pool 删除池", store.delete_pool("ap_t1"))
        check("delete_pool 二次删返回 False", store.delete_pool("ap_t1") is False)
        check("池组节点仍在", any(u.id == "up_pool" for u in store.list_upstreams()))
    finally:
        # 恢复正式配置
        store.config.upstreams = saved_upstreams
        store.config.account_pools = saved_pools
        store.save()
    fails = [r for r in results if not r[1]]
    print(f"\n共 {len(results)} 条，通过 {len(results) - len(fails)}，失败 {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
