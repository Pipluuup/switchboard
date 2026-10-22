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

## 3.1 数据源：明细 + 日聚合双源（2026-10-22 新增）

`request_logs` 明细表受 `log_retention_days` 清理（默认 7 天），
直接查它算 30 天窗口只能看到 7 天的数据——这就是已知问题 #5。

新增旁路表 `usage_daily_rollups`，把明细按「天 × 上游 × 实际模型」压成一行：

| 字段 | 说明 |
|---|---|
| `date` | `YYYY-MM-DD`（本地时区） |
| `upstream_id` / `upstream_name` | 上游 |
| `actual_model` | 映射后的实际模型（消耗额度的是它） |
| `request_model` | 客户端请求的模型——**与实际模型分开存**，才看得见「客户端要 claude-opus、实际烧的是 glm-5.3」这种转换关系 |
| `request_count` / `success_count` | 请求数 / 成功数 |
| `input_tokens` / `output_tokens` | |
| `cache_read_tokens` / `cache_write_tokens` | 缓存命中 / 写入 |
| `avg_latency_ms` | |

**写入时机**：在 `main.py` 的健康检查循环里，`rollup_days()` 必须在
`cleanup()` **之前**调用——明细一旦被删，那部分数据就永远进不了聚合表。

**用「按日期删除后重算」而不是 UPSERT 累加**：累加在重跑时会双计，重算是幂等的。

**窗口按长度选数据源**：

- 窗口 ≤ 保留期 → 查明细（精度高，有精确延迟）
- 窗口 > 保留期 → 查聚合表（不受保留期影响）

`usage_windows()` 的返回值带 `source` 字段（`rollup` / `detail`）标明用了哪个。

## 3.2 缓存 token 的口径

三种协议的 usage 语义**不一致**：

| 协议 | 缓存读 | 缓存写 | `input_tokens` 是否含缓存 |
|---|---|---|---|
| anthropic | `cache_read_input_tokens` | `cache_creation_input_tokens` | **不含** |
| responses | `input_tokens_details.cached_tokens` | — | **含** |
| openai (chat) | `prompt_tokens_details.cached_tokens` | — | **含** |

落库时统一成 **`input_tokens` 不含缓存**（OpenAI 协议要减掉 `cached_tokens`），
这样 `input + cache_read + cache_write` 在三种协议下含义统一，聚合表可以直接相加。

> 这个差异是真实的——CC-Switch 为此专门存了 `input_token_semantics` 列，
> 其本机数据里 semantics=1 的全是 codex（Responses）、semantics=2 的全是
> claude（Anthropic）。

`total_tokens` 仍是 `input + output`（**不含缓存**）：缓存单独展示，
混进总量会让「缓存一开总量暴涨」误导。

## 4. 前端行为（frontend/src/views/Usage.tsx）

- 用量页三个窗口各一张卡片（颜色 5h=accent / 7d=purple / 30d=cyan）；
- 卡片右上角显示「缓存命中 N」，表格有独立的「缓存命中」列；
- 窗口对比表 + 按所选窗口的模型份额条形图；
- **`7 < retention_days < 30` 时显示黄色警示**：日志保留期会截断 30d 窗口；
- 无需手动刷新策略：切换视图时重新拉取；
- **Plan 实时查询卡片**（2026-10-13）：页面底部渲染 GoQuota / ArkQuota 两张官方
  配额卡片（数据来自 `/api/opencode-console`、`/api/ark-console`，60s 缓存，5min 轮询）；
  设置页 `show_plan_usage` 开关关闭时整组隐藏（见 05 #7/#8）。

## 5. 已知限制与后续方向

| 问题 | 现状 | 方向 |
|---|---|---|
| ~~日志保留期截断长窗口~~ | ✅ 已解决（2026-10-22）：30d 窗口改查日聚合表 | — |
| 窗口实时全表扫描 | `ts >= ?` 索引可用，日志量大时仍需观察 | 数据量 > 百万行时引入聚合表 |
| 没有按客户端 Key 的维度 | 全局统计 | by_key 聚合列，服务"谁在烧量"场景 |

## 6. 验证基准

`tests/test_usage.py` 36 条（临时 SQLite）：窗口边界（6h 前不进 5h 窗、40d 前三窗皆无）、
Token 求和、成功率、按上游分组、**模型名必须是 actual_model**，
以及日聚合表（幂等重算、与明细一致、长窗口切数据源、聚合表不受保留期影响）。
改统计口径先跑它。
