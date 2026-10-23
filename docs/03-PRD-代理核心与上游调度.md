# 03 · PRD · 代理核心与上游调度

> 关联代码：`backend/converter.py`、`backend/responses_converter.py`、
> `backend/upstream_pool.py`、`backend/proxy_routes.py`

## 1. 故障转移链（2026-10-23 新增）

### 目标

映射不再只是「别名 → 单一（上游, 模型）」，而是一张**从上到下依次尝试的链**：
每档 `ChainHop { upstream_id, target }` 各自带上游与模型。
专治两件老调度管不了的事：

1. 同一上游上的模型下线/限流（单模型失败，上游整体是好的）；
2. 想固定走 A 上游、失败后落到 B 上游的明确偏好（公共调度只按 priority，表达不了）。

### 数据与解析（config.py）

- `ModelMapping.chain: list[ChainHop]`；`hops()` 负责三重退化：
  - 链为空 → 用 `target` + `upstream_id` 造一档（**老配置零迁移**）；
  - 链里全是空档（既没上游也没模型）→ 同样退回老字段；
  - 链非空 → 完全以链为准，`target`/`upstream_id` 只作元数据保留。
- 无映射命中的请求 → 单档「自动调度 + 原样模型名」，沿用公共调度。
- 档位语义：`upstream_id` 留空 = 该档自动调度；`target` 留空 =
  该档沿用请求的原始模型名（pass-through）——`plan_chain` 里写为
  `target = hop.target or fallback_model`。

### 展开与切换（upstream_pool.py `plan_chain()`）

- 每档展开为候选：固定上游 1 个；自动调度档展开为 `max_retries + 1` 个
  调度候选；已试过的上游不重复（`attempted set`）。
- 固定上游在本机配置里不存在 → 不尝试，直接记 `ChainEntry(None, target, i, "上游不存在")` 进轨迹。
- **切换触发**：`status >= 500 或 status ∈ {429, 404}`。
  400/401/403 是请求本身的错，不切（401 立即熔断，404 反而不熔断——见错误分类表）。
- 规划用只读路径（`candidates(readonly=True)`）：**规划绝不翻转熔断状态**，
  不消耗半开试探名额。

### 日志与可观测性

- 每条请求日志带 `failover_trail`（JSON 字符串）：
  `[{hop, upstream, model, status, result}]`，一请求一日志，切换历史全在 trail 里。
  - `status: 0` = 没发出去（跳过 / 网络异常）；
  - 末尾追加最终成功行的 `result` = "成功"。
- 该字段同时在 `GET /api/logs` 列表与详情响应里（`_LIST_COLS`）。
- 前端：映射页表格并列为单列「故障转移链」逐档展示；日志页加「切换」列
  （切换 N 次橙色 tag）与详情弹窗「切换轨迹」区。

### 验证基准

- `tests/test_failover_chain.py` 40 条：链展开 / 跳过规则 / 切换条件 / 只读规划 / 老配置兼容。
- `tests/e2e_failover.py`（隔离实例 8130 + mock 8115）：真实 HTTP 路径上
  500/429/404 切档、400/401 不切、整链失败、流式切换、轨迹落日志。

## 2. 协议转换（converter.py + responses_converter.py）

### 目标

客户端可能说 Anthropic、OpenAI Chat 或 OpenAI Responses；上游同理。
转换层把不一致的方向补齐——**请求与响应各一张查表**：

| 入站 → 上游 | 是否转换 |
|---|---|
| anthropic → anthropic | 透传（改写 model 名） |
| anthropic → openai | 转换（含流式） |
| openai → anthropic | 转换（含流式） |
| openai → openai | 透传（改写 model 名） |
| anthropic → responses | 转换（含流式） |
| responses → anthropic | 转换（含流式） |
| responses → openai | 转换（含流式） |
| openai → responses | 转换（含流式） |
| responses → responses | 透传（改写 model 名） |

> `responses` 与 `chat` 共用一套语义（Responses 只是形状不同），
> 所以同一对方向复用同一个转换函数。查表在 `proxy_routes.py` 的
> `_REQUEST_CONVERTERS` / `_RESPONSE_CONVERTERS`。

### 行为规格

- **流式**：上游 SSE → 逐事件转换 → 以客户端协议的 SSE 格式回吐；上游非流式则
  整体转换后一次返回。
- **工具调用**：`tool_use` / `tool_result`（Anthropic）与 `tool_calls` / `role=tool`
  （OpenAI）双向映射，保证 Claude Code 能正常完成工具循环。
- **Token 统计**：转换结果带 usage 时提取 `input_tokens` / `output_tokens` 落日志。
- 改写规则：发往上游的 `model` 恒为映射后的 `target`；日志同时记
  `request_model`（客户端要的）与 `actual_model`（实际消耗额度的名字）。

### 协议取值的约束

`config.py` 的 `Protocol` 字面量里**每加一个取值，`upstream_pool.endpoint()`
就必须有对应分支**——否则它会静默走 `else` 分支打到 `/chat/completions`，
用户以为在用新协议、实际打错端点。`tests/test_frontend_contract.py` 盯着这条。

### 验证基准

- `tests/test_converter.py` 38 条：Anthropic ⇄ OpenAI 转换与流式事件映射
- `tests/test_responses_converter.py` 29 条：Responses 协议转换（请求/响应/流式）

两个套件合起来是转换行为的**唯一权威描述**——改转换器先读它们，改完必须全绿。

## 3. 上游调度（upstream_pool.py）

### 选路规则（`candidates()`）

1. 取 `enabled` 的上游；
2. 若上游配了 `models` 白名单，则请求模型必须命中，否则该上游不参与；
3. 按 `priority` 升序、同优先级按 `weight` 加权产生候选顺序；
4. 处于冷却期（`cooldown_until` 未到）的上游排到最后或跳过；
5. **熔断中的上游直接排除**（`circuit_state == "open"`）；冷却到期后转半开，
   且只放行一个试探请求。

### 故障处理

- 请求失败：`consecutive_failures += 1`，达到 `failure_threshold`（默认 3）进入
  冷却 `cooldown_seconds`（默认 60s）；
- 请求成功：清零失败计数、记录延迟；
- 按 `max_retries` 依次尝试候选列表中的下一个上游；
- 单次请求超时取上游 `timeout`（默认 180s）。

### 熔断器（2026-10-22 新增）

上面的「连续失败 + 冷却」是**二元冷却**，有两个硬伤：

1. **抖动型上游永远打不中阈值**——`失败 成功 失败 成功…` 序列里，
   每次成功都把 `consecutive_failures` 清零，计数永远到不了 3，
   坏节点一直留在候选池，每个请求有 50% 概率白等一次超时；
2. **冷却期结束是全量放行，没有半开**——若上游其实没恢复（Key 被吊销），
   这批请求全部失败，又重新触发冷却，形成周期性雪崩。

熔断器补上这两点，状态机三态（参考 CC-Switch v4 的 `proxy_config`）：

```
        ┌──────────────────────────────────────────┐
        │                                          │
        ▼                                          │
    ┌────────┐   连续失败 ≥ failure_threshold              │
    │ CLOSED │   或 错误率 > circuit_error_rate_threshold   │
    │ （正常）│──────────────────────►┌────────────┐
    └────────┘   （且样本数 ≥ min_requests）│   OPEN    │
        ▲                                 │  （拉黑） │
        │                                 └────────────┘
        │ 连续成功 ≥ half_open_success          │ timeout 到期
        │                                       ▼
        │                                 ┌────────────┐
        └─────────────────────────────────│  HALF_OPEN │
              （试探性放行一个请求）           │ （半开）  │
                                          └────────────┘
                                                │ 试探失败
                                                └──► 回到 OPEN
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `circuit_enabled` | true | 总开关；关闭时退回旧的二元冷却 |
| `failure_threshold` | 3 | 连续失败多少次 → 打开熔断 |
| `circuit_error_rate_threshold` | 0.5 | 窗口内错误率超这个比例 → 打开熔断 |
| `circuit_min_requests` | 10 | 样本数不足这个量时**不**用错误率判定 |
| `circuit_open_seconds` | 60 | 熔断打开后多久进入半开 |
| `circuit_half_open_success` | 2 | 半开期连续成功多少次 → 完全关闭 |
| `circuit_window_seconds` | 120 | 错误率统计窗口（秒） |

**`circuit_min_requests` 最容易被忽略但最关键**：刚启动的节点 3 次全失败，
错误率 100%，但毫无统计意义——直接拉黑是误判。样本不足时只看连续失败次数。

**半开只放行一个请求**（`_half_open_inflight` 记录在途试探），
避免并发同时涌向刚恢复的上游。

### 错误分类：客户端的错不该算上游的失败

`report_failure(status_code=...)` 按状态码区分责任：

| 状态码 | 处理 | 理由 |
|---|---|---|
| 401 / 403 | **立即熔断** | Key 失效或被拒，重试无意义 |
| 400 / 404 / 422 | **不计入**熔断 | 客户端请求本身有问题，上游是好的 |
| 429 | 计入 | 限流，等久点 |
| 5xx | 计入 | 上游真出问题了 |
| 0（网络异常/超时） | 计入 | 连不上 |

> 旧实现对**所有** 4xx 都记一次失败——客户端连发 3 个格式错误的请求，
> 就能把一个完全健康的上游误熔断。

**实测案例（2026-10-23，SenseNova 免费网关）**：`glm-5.2` / `kimi-k3` 稳定 200，
`deepseek-*` 频繁 429 `inference exceeds tpm/rpm limit`（免费档端点级限流）。
这验证了 429 计入熔断的正确性：不拉黑的话每个请求都要白等一次注定 429 的响应；
60s 冷却 + 半开试探则让恢复的节点自动重新上岗。（详见该上游备注。）

### 前端交互坑（映射编辑器）

**`Space.Compact` + `Form.Item noStyle` 组合下，AutoComplete 一旦有值，
宽度会被压塌到 24px**（rc-select 单选模式的宽度计算 bug，新增空值时看不出问题、
编辑已有映射才暴露）。修法：放弃 `Space.Compact`，改普通 flex 行——
Select 固定 42% + `flexShrink: 0`，AutoComplete `flex: 1; minWidth: 0`。
映射编辑器固定展示 3 档（老式单档编辑时自动补空位），全空档在 `submit()` 里剔除，
不污染后端链数据。

### 健康检查

`main.py` 的 lifespan 起后台循环：每 `health_check_interval` 秒（默认 120s）对
所有 enabled 上游做体检，结果写回运行态。**Key 为空就启用上游 = 健康循环定时刷
401 并把节点打进冷却**——这是 OpenCode Go 上游默认停用的原因。

**探活成功不重置熔断状态**（`report_health_ok` 只更新延迟）：
`/models` 探活成功不代表真实请求成功——限流中的上游 `/models` 往往正常返回。
若这里调 `report_success`，熔断器会被「探活成功」误判为已恢复。

## 4. 代理入口（proxy_routes.py)

- `POST /v1/messages`（Anthropic 风格）、`POST /v1/chat/completions`（OpenAI Chat 风格）、
  `POST /v1/responses`（OpenAI Responses 风格），另有 `POST /v1/messages/count_tokens`
  与 `GET /v1/models`；
- 入站协议**不靠 URL path 判断**——`/v1/messages/count_tokens` 并不以 messages 结尾。
  判据是：带 `anthropic-version` 头 → Anthropic；路径以 `/responses` 结尾 → Responses；
  其余 → OpenAI Chat；
- 鉴权 → 限流 → 映射 → 选路 → 压缩 → 转换 → 转发 → 记日志，
  链路见 [02](02-架构与技术决策.md)；
- 客户端断开/超时需保证上游连接被关闭，不悬挂。

## 5. 已知限制（对应总览 #2/#4/#5）

1. **请求头不透传**：`endpoint()` 自建请求头，客户端 UA、自定义头都到不了上游。
   改造方向：`Upstream` 增加 `custom_headers` 字段，`endpoint()` 与 `pool.call()`
   接受并合并额外头；前端上游表单加对应输入。
   （OpenCode Go 是特例：`_is_opencode()` 会自动补专用 UA 与 `x-opencode-session`。）
2. **白名单落空回退**：`candidates()` 里"过滤后为空就退回全量"的逻辑会让
   `grok-4.7` 这类无主模型砸到 CommandCode 头上。应改为返回空候选 → 422
   "网关内无此模型"。
3. ~~**日志保留期截断 30d 窗口**~~ ✅ 已修复（2026-10-22）：
   新增日聚合表 `usage_daily_rollups`，窗口长于保留期时改查它（见 [04](04-PRD-用量统计.md)）。

## 6. E2E 测试规约

`tests/test_e2e.py` 开头的清场会**删光目标实例的全部上游/映射/Key**。跑法必须是隔离实例：

```
set SWITCHBOARD_DATA_DIR=<临时目录>  &&  python run.py --no-browser --port 8010
# 再把 t.BASE 补丁为 http://127.0.0.1:8010
```

正式配置零接触。`tests/mock_upstream.py` / `tests/mock_failover_upstream.py`
在独立端口（如 8100 / 8115）提供假上游，后者靠模型名区分 500/429/404/正常行为。

三个 e2e 脚本的地址都支持环境变量覆盖，不用再改源码里的硬编码：
`SWITCHBOARD_BASE`（主服务）、`SWITCHBOARD_MOCK`（假上游）。
CI 里 `test_e2e.py` 跑 8000 + 8100，`e2e_voice.py` 跑 8124 + 8110，
`e2e_failover.py` 跑 8130 + 8115，互不干扰。
