# 04 · PRD · 用量统计（5h/7d/30d 滚动窗口）

> 关联代码：`backend/database.py`（USAGE_WINDOWS、`usage_windows()`）、
> `backend/admin_routes.py`（`GET /api/usage`）、`frontend/src/views/Usage.tsx`（`USAGE_WINDOWS` 常量、用量页渲染）

## 1. 需求背景

OpenCode Go 的配额按周期结算：**5 小时窗口消耗月度额度的 20%，周窗口 50%，
自然月 100%**。用户需要随时知道"最近这段时间烧了多少"，才能决定用哪个模型、
要不要省着点。

因此选择了**滚动窗口**（`ts >= now - seconds`）而不是自然日/自然周：

- 5h 窗口直接对应 Go 的 5 小时结算段；
- 7d/30d 覆盖"周"与"月"量级，且任意时刻看都是"最近 7/30 天"的直觉口径。

窗口定义集中在 `database.py` 的 `USAGE_WINDOWS = [("5h", "近 5 小时", 5*3600),
("7d", "近 7 天", 7*86400), ("30d", "近 30 天", 30*86400)]`，改周期只动这一处 +
前端 `USAGE_WINDOWS` 常量。

## 2. 统计口径（关键决策）

- **模型维度取 `actual_model`（映射后的名字）**，为空才退回 `request_model`。
  理由：消耗上游额度的是映射后的模型，看 `go-glm` 的用量没有意义，要看 `glm-5.3`。
- 单窗口返回：requests / ok / failed / success_rate / input+output+total_tokens /
  avg_latency_ms / first_ts / last_ts / by_model / by_upstream。
- 三窗口并行返回，一次请求全部拿到，前端不再分次拉。

## 3. API

```
GET /api/usage  →
{
  "windows": [ { key, label, seconds, since, requests, ok_requests,
                 failed_requests, success_rate, input_tokens, output_tokens,
                 total_tokens, avg_latency_ms, first_ts, last_ts,
                 by_model[], by_upstream[] } × 3 ],
  "retention_days": 7,
  "generated_at": 1791769084.1
}
```

## 4. 前端行为（frontend/src/views/Usage.tsx）

- 用量页三个窗口各一张卡片（颜色 5h=accent / 7d=purple / 30d=cyan）；
- 窗口对比表 + 按所选窗口的模型份额条形图；
- **`7 < retention_days < 30` 时显示黄色警示**：日志保留期会截断 30d 窗口；
- 无需手动刷新策略：切换视图时重新拉取；
- **Plan 实时查询卡片**（2026-10-13）：页面底部渲染 GoQuota / ArkQuota 两张官方
  配额卡片（数据来自 `/api/opencode-console`、`/api/ark-console`，60s 缓存，5min 轮询）；
  设置页 `show_plan_usage` 开关关闭时整组隐藏（见 05 #7/#8）。

## 5. 已知限制与后续方向

| 问题 | 现状 | 方向 |
|---|---|---|
| 日志保留期截断长窗口 | `log_retention_days=7` 时 30d 窗口只剩 7 天数据，前端已警示 | ① 文档引导设 0/≥30；② 新增小时级聚合表，窗口从聚合表算（总览路线 #4） |
| 窗口实时全表扫描 | `ts >= ?` 索引可用，日志量大时仍需观察 | 数据量 > 百万行时引入聚合表 |
| 没有按客户端 Key 的维度 | 全局统计 | by_key 聚合列，服务"谁在烧量"场景 |

## 6. 验证基准

`tests/test_usage.py` 22 条（临时 SQLite）：窗口边界（6h 前不进 5h 窗、40d 前三窗皆无）、
Token 求和、成功率、按上游分组、**模型名必须是 actual_model**。改统计口径先跑它。
