# 03 · PRD · 代理核心与上游调度

> 关联代码：`backend/converter.py`、`backend/upstream_pool.py`、`backend/proxy_routes.py`

## 1. 协议转换（converter.py）

### 目标

客户端只说 Anthropic；上游可能说 Anthropic 或 OpenAI。转换层把 2×2 组合里
不一致的三个方向补齐：

| 客户端 → 上游 | 是否转换 |
|---|---|
| anthropic → anthropic | 透传（改写 model 名） |
| anthropic → openai | 转换（含流式） |
| openai → anthropic | 转换（含流式） |
| openai → openai | 透传（改写 model 名） |

### 行为规格

- **流式**：上游 SSE → 逐事件转换 → 以客户端协议的 SSE 格式回吐；上游非流式则
  整体转换后一次返回。
- **工具调用**：`tool_use` / `tool_result`（Anthropic）与 `tool_calls` / `role=tool`
  （OpenAI）双向映射，保证 Claude Code 能正常完成工具循环。
- **Token 统计**：转换结果带 usage 时提取 `input_tokens` / `output_tokens` 落日志。
- 改写规则：发往上游的 `model` 恒为映射后的 `target`；日志同时记
  `request_model`（客户端要的）与 `actual_model`（实际消耗额度的名字）。

### 验证基准

`tests/test_converter.py` 38 条断言是转换行为的**唯一权威描述**——改转换器先读它，
改完必须全绿。

## 2. 上游调度（upstream_pool.py）

### 选路规则（`candidates()`）

1. 取 `enabled` 的上游；
2. 若上游配了 `models` 白名单，则请求模型必须命中，否则该上游不参与；
3. 按 `priority` 升序、同优先级按 `weight` 加权产生候选顺序；
4. 处于冷却期（`cooldown_until` 未到）的上游排到最后或跳过。

### 故障处理

- 请求失败：`consecutive_failures += 1`，达到 `failure_threshold`（默认 3）进入
  冷却 `cooldown_seconds`（默认 60s）；
- 请求成功：清零失败计数、记录延迟；
- 按 `max_retries` 依次尝试候选列表中的下一个上游；
- 单次请求超时取上游 `timeout`（默认 180s）。

### 健康检查

`main.py` 的 lifespan 起后台循环：每 `health_check_interval` 秒（默认 120s）对
所有 enabled 上游做体检，结果写回运行态。**Key 为空就启用上游 = 健康循环定时刷
401 并把节点打进冷却**——这是 OpenCode Go 上游默认停用的原因。

## 3. 代理入口（proxy_routes.py)

- `POST /v1/messages`（Anthropic 风格）、`POST /v1/chat/completions`（OpenAI 风格）；
- 鉴权 → 限流 → 映射 → 选路 → 转换 → 转发 → 记日志，链路见 [02](02-架构与技术决策.md)；
- 客户端断开/超时需保证上游连接被关闭，不悬挂。

## 4. 已知限制（对应总览 #1/#2/#4）

1. **请求头不透传**：`endpoint()` 自建请求头，客户端 UA、自定义头都到不了上游。
   改造方向：`Upstream` 增加 `custom_headers` 字段，`endpoint()` 与 `pool.call()`
   接受并合并额外头；前端上游表单加对应输入。
2. **白名单落空回退**：`candidates()` 里"过滤后为空就退回全量"的逻辑会让
   `grok-4.7` 这类无主模型砸到 CommandCode 头上。应改为返回空候选 → 422
   "网关内无此模型"。
3. `/v1/responses` 无入口无转换（见 [05](05-PRD-OpenCode-Go接入.md)）。

## 5. E2E 测试规约

`tests/test_e2e.py` 开头的清场会**删光目标实例的全部上游/映射/Key**，且 `BASE`
硬编码 `8000`。跑法必须是隔离实例：

```
set SWITCHBOARD_DATA_DIR=<临时目录>  &&  python run.py --no-browser --port 8010
# 再把 t.BASE 补丁为 http://127.0.0.1:8010
```

正式配置零接触。`tests/mock_upstream.py` 在独立端口（如 8100）提供假上游。
