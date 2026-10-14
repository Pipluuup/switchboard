# 05 · PRD · OpenCode Go 接入

> 关联代码：`data/config.json`、`scripts/import_opencode_go.py`、
> `backend/upstream_pool.py`（models 白名单）
> 官方文档：<https://opencode.ai/docs/zh-cn/go/>

## 1. 背景与约束

OpenCode Go（`https://opencode.ai/zen/go/v1`）把模型分布在**三个不同协议的端点**上：

| 端点 | 协议 | 模型 | 本代理支持 |
|---|---|---|---|
| `/v1/chat/completions` | OpenAI | GLM / Kimi / LongCat / DeepSeek / MiMo / Hy / Space Bunny（19 个） | ✅ |
| `/v1/messages` | Anthropic | MiniMax M3 / M2.7、Qwen3.8 Max/Flash、Qwen3.7 Plus（5 个） | ✅ |
| `/v1/responses` | OpenAI Responses | Grok 4.7/4.6、GPT 6 Luna、GPT 5.6 Luna、Muse Spark 1.3/1.2 | ❌（总览 #3） |

硬约束：

- Go 要求**专用 User-Agent** 和 **`x-opencode-session` 头**（影响路由与提示词缓存），
  通用 SDK UA 不行 → ✅ 已解决（2026-10-13）：代理检测 OpenCode 上游自动带
  `Switchboard/1.0` UA + 会话头（见下文「会话头实现」）；
- 一个上游条目只能一个协议 → **同 base_url 建两条上游**，靠各自 `models` 白名单区分。

## 2. 上游条目设计

**2026-10-13 白名单已按 `/v1/models` 实测刷新**（Go Key 实拉 39 个模型）：

- Chat 端白名单扩到 **33 个**（新增 `glm-5.1`、`deepseek-flash`、`omen-alpha`、
  `claude-haiku-5-5`、`minimax-m2.5/m2.7/m3`、`space-bunny`（原名 space-bunny-free）、
  `step-5-preview-free`、`cat-jam-free`、`qwen3.6-plus`、`qwen3.7-max/plus`、
  `qwen3.8-max/flash`）；
- `/v1/responses` 专属（仍不可用）：grok-4.7/4.6、gpt-6-luna、gpt-5.6-luna、
  muse-spark-1.3/1.2-contributor；
- Claude Code 客户端直连场景另有独立 `Deepseek` 上游
  （`https://api.deepseek.com/anthropic`，Anthropic 协议，模型 deepseek-flash /
  deepseek-v4-pro）。

| 上游 | protocol | enabled | priority | models 数 | 覆盖 |
|---|---|---|---|---|---|
| OpenCode Go (Chat) | openai | 是 | 200 | 33 | glm-5.3/5.3-flash/5.2/5.1、kimi-k3/k2.7-code/k2.6、longcat-2.0/2.5-preview-free、deepseek-v4.1-flash/v4-pro/v4-flash/v4-flash-vision-exp/flash、mimo-v2.6-flash/pro、mimo-v2.5/pro、hy4-preview、hy3、omen-alpha、claude-haiku-5-5、minimax-m2.5/m2.7/m3、space-bunny、step-5-preview-free、cat-jam-free、qwen3.6-plus、qwen3.7-max/plus、qwen3.8-max/flash |
| OpenCode Go (Messages) | anthropic | 否（待填 Key） | 200 | 5 | minimax-m3、minimax-m2.7、qwen3.8-max、qwen3.8-flash、qwen3.7-plus |

`priority=200` 低于 CommandCode 的 100：同模型多上游可用时优先CommandCode，
Go 作为扩容池。

## 3. 模型别名（映射表）

**2026-10-13 起映射精简为单条**：`Free → agnes-2.5-flash`（默认直通，走 Agnes 免费
通道）。历史 `go-*` 别名（go-glm / go-kimi / go-deepseek / go-minimax / go-qwen /
go-glm-5.1 / go-omen / go-claude-haiku 等）已删除——客户端模型名没有映射命中时，
代理仍会按上游白名单自动调度，`glm-5.3` 等原始模型名可直接使用。

## 4. 启用步骤

```bash
# 方式一：脚本一把梭（幂等：已存在则 PUT 更新，不重复建）
python scripts/import_opencode_go.py --api-key sk-go-xxx --enable

# 方式二：控制台
#   上游管理 → 两条 Go 上游分别填 Key → 启用
```

**默认停用（Key 为空）是有意的**：健康检查每 120s 一次，空 Key 会刷 401 并把节点
打进冷却。先填 Key 再启用。

## 5. import 脚本行为规约

- 幂等：按名称匹配已存在上游则更新（保留其 enabled 状态），否则创建；
- 默认只写配置不启用，`--enable` 才启用；`--no-aliases` 跳过映射；
- 本机必须绕过系统代理：`urllib.request.ProxyHandler({})`（全局 HTTP_PROXY 坑）；
- `--base` 可指向其他实例（默认 `http://127.0.0.1:8000`）。

## 6. 待验证 / 已知问题

1. **Zen `/v1/messages` 是否接受 `Authorization: Bearer`**（总览 #4）：代理转发
   Anthropic 协议时只带 Bearer 不带 `x-api-key`。首启 Messages 上游后立刻看
   日志页成败即可判定。
2. ~~**请求头透传未做**~~ ✅ 已解决（2026-10-13）。
   **会话头实现**：`upstream_pool.endpoint()` 检测 `opencode.ai` 上游时自动附加
   `User-Agent: Switchboard/1.0` 与 `x-opencode-session`；会话 ID 由
   `proxy_routes.opencode_session_id()` 确定性解析——优先透传客户端会话头
   （`x-opencode-session` / `x-claude-session-id` / `x-session-id`），其次取
   请求 `metadata.user_id`（Claude Code 每会话携带）派生，兜底按客户端身份
   派生，无需维护会话状态。连通性测试用 `ccp-test-<id>` 固定会话。
3. `/v1/responses` 系列模型（Grok/GPT Luna/Muse）不可用；误配白名单落空时会被
   派给错误上游（总览 #2）。

## 7. 官方配额数据（2026-10-13 实测打通）

OpenCode Console 前端（`/console`）的 Go 面板数据源：

- 接口：`GET https://opencode.ai/console/api/go/status`，
  头：`x-org-id: <wrk_工作区ID>` + cookie `__Host-console_session`（httpOnly，
  **约 30 天有效**）。注意前缀是 `/console/api/`，裸 `/api/` 是站点 404。
- 响应关键字段：`product`（go/go-plus）、`access.endsAt`（Renews 日期）、
  `access.meters.{fiveHour,week,month}` 各含 `usedMicroCents / limitMicroCents /
  resetsAt`（microcents ÷ 1e7 = 美元）。Go 计划实测限额：Rolling $120 /
  Weekly $300 / Monthly $600（窗口汇总限额，与按模型限额是两套口径）。
- 代理实现：设置页「Plan 实时查询」卡片存 org id + cookie（默认收起，勾「展开配置」
  展开；「展示用量」开关控制用量页配额卡片显隐，存 `show_plan_usage`）；
  `backend/opencode_console.py` 拉取（60s 缓存）经 `GET /api/opencode-console`
  输出。配额一律以官方数据为准（本地复算模块已移除）。cookie 过期后到设置页
  重新复制即可（F12 → 应用 → Cookie）。

## 8. 火山方舟编码计划（2026-10-13 同日打通）

同一套「控制台官方数据」模式扩展到第二个 plan（用户需求：多 coding plan）：

- 接口：`POST https://console.volcengine.com/api/top/ark/cn-beijing/2024-01-01/GetAgentPlanAFPUsage?`
  body `{}`；鉴权 = 控制台会话 Cookie 整串 + `X-Csrf-Token`（值 = csrfToken
  cookie，double-submit）。无时效签名，Cookie 会话失效后重新复制即可。
- 数据：`Result.PlanType`（small/medium/large/max）+ `AFP{FiveHour,Daily,
  Weekly,Monthly}` 各含 `Quota/Used/ResetTime`（AFP 点数；ResetTime 毫秒，
  -1 无数据）。代理当前套餐 small：5h 2000 / 日 10000 / 周 7000 / 月 20000。
- **Coding Plan（编码套餐，百分比计量）**：
  `POST .../2024-01-01/GetCodingPlanUsage?` body `{}` →
  `Result.Status`（Running 等）+ `QuotaUsage[]`：`{Level: session/weekly/monthly,
  Percent, Cap, ResetTimestamp(秒)}`。与 Agent Plan 是两套独立额度，同一
  Cookie 可同时拉取；`ark_console.py` 一次返回两个 plan，任一失败不影响另一个。
- 实现：`backend/ark_console.py`（60s 缓存）→ `GET /api/ark-console` →
  前端 `ArkQuota.tsx`；设置页「火山方舟」卡片存 Cookie 整串（F12 → Network
  → api/top/ark 请求 → Request Headers → Cookie）。
- 本地复算模块（opencode_quota.py / db.query_since）已按用户要求移除，
  配额一律以官方数据为准，未配置/失效时提示补充凭证。

以后接入新 coding plan 的套路：浏览器抓包找控制台用量接口 → 确认鉴权形态
（cookie / 短期 token）→ 写一个 `*_console.py` fetcher + `/api/*-console` 端点
+ 一个 Quota 组件 + 设置页「Plan 实时查询」卡片里的新小节（③ …）。

## 9. 免费上游池（2026-10-13 接入）

与 Go 并列的兜底扩容池，均为 OpenAI 协议、priority 300（仅当模型名唯一匹配时调度）：

| 上游 | Base URL | 白名单 | 备注 |
|---|---|---|---|
| Agnes AI (free) | `https://api.agnes-ai.cn/v1` | agnes-2.5-flash / pro / pro-alpha / pro-beta、agnes-3.0-flash | 体验金余额制；`预扣费失败...剩余额度￥0.10` 403 = 余额不足。图像/视频模型（agnes-image-*、agnes-video-*）不支持 chat 协议未入白名单 |
| SenseNova (free) | `https://token.sensenova.cn/v1` | glm-5.2、kimi-k3、deepseek-v4-flash、sensenova-6.8-flash-lite、deepseek-flash | pricing 全 0；免费档 tpm/rpm 限流紧（连续压测即触发）。deepseek-v4.1-flash 不在当前 token plan |

前端约定：上游名/备注含 `free`（不区分大小写）→ 列表置顶 + 🎁 图标 + 删除按钮
禁用（视为内置免费节点）；上游管理页有「全部 / Free / 自定义」筛选。

模型探测：`POST /api/upstreams/probe-models`（body: base_url / api_key / protocol /
upstream_id，Key 留空且给 upstream_id 时用已存 Key）。anthropic 端点拉不到
/models 时自动去掉最后一段路径重试（覆盖 api.deepseek.com/anthropic 场景）。
获取结果先弹预选面板（默认全选、可过滤/勾选），确认后才写入表单。
