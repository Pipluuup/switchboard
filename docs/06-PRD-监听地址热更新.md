# 06 · PRD · 监听地址热更新（改端口即生效）

> 关联代码：`backend/admin_routes.py`（`POST /api/server/restart`）、
> `backend/main.py`（`runtime_addr`）、`web/app.js`（saveSettings）、`run.py`

## 1. 需求

设置页早就有"监听地址/端口"输入框，也能保存，但必须手动重启进程才生效。
需求：**在控制台改完端口，服务自动重启到新地址，浏览器跟着过去**。

## 2. 交接流程

```
POST /api/server/restart  {"host": "127.0.0.1", "port": 9000}
  │
  ├─ ① 参数校验（端口 1-65535）
  ├─ ② 与 runtime_addr 比较：相同 → {"restarted": false}，不折腾
  ├─ ③ 绑定预检：试绑目标地址，失败则重试，最多 ~4 秒
  │      （容忍旧进程 1 秒退出宽限 + Windows 释放端口延迟）
  ├─ ④ 写入配置（host/port 落 config.json）
  ├─ ⑤ spawn 脱离进程树的新进程（DETACHED_PROCESS，stdout/stderr → data/server.log）
  ├─ ⑥ 轮询新进程 /healthz（trust_env=False），最长 30 秒
  ├─ ⑦ 成功 → call_later(1.0, os._exit(0))，旧进程退出，交接完成
  └─ 任一步失败 → 保持旧服务运行 + 配置回滚到 runtime_addr
```

## 3. 关键设计决策

| 决策 | 理由 |
|---|---|
| `PUT /api/settings` **不受理** host/port | 否则会出现"配置已写、进程没跟上"的半生效状态；改地址必须走交接端点 |
| `runtime_addr` 在 lifespan 捕获、之后只读 | 判断"地址是否真的变了"与失败回滚目标都必须是**实际在跑**的地址，不能信可被改的 settings |
| 新进程 detached + 日志落 `data/server.log` | DETACHED 进程没有控制台，不重定向就彻底失明（坑 #18） |
| 绑定预检重试 ~4 秒 | 旧进程 `os._exit(0)` 后 Windows 可能 1-2 秒内仍报 WSAEADDRINUSE（坑 #16） |
| 旧进程延迟 1 秒退出 | 保证重启响应先送达客户端 |
| SQLite 用 WAL | 交接瞬间新旧进程同时写 `proxy.db` 不冲突 |

## 4. 边界与已知限制

- **同端口只改监听地址**（如 `127.0.0.1` → `0.0.0.0`）无法在线交接：新进程绑不上
  旧进程还占着的端口。明确报 400，提示先停服务再启动。
- 目标端口被**其他程序**占用：预检重试 4 秒后仍失败 → 400，配置不写。
- 新进程 30 秒未就绪：502，配置回滚，旧服务继续跑（极小概率出现双实例，端口不同，可手动清理）。

## 5. 运维速查

```bash
python scripts/service.py status   # 状态（按 config 端口探活）
python scripts/service.py stop     # 停止（按端口找 PID taskkill）
start-hidden.vbs                   # 双击静默启动
```

交接后的服务**不属于任何终端会话**——关掉启动它的窗口/任务它也活着。

## 6. 实测记录（2026-09-30）

| 用例 | 结果 |
|---|---|
| 8021→8023 交接 | 200，1.1s，配置同步 |
| 立即往返 8023→8021（踩宽限期） | 200，2.1s（重试生效） |
| 重启到占用端口 19387 | 400 明确报错，配置未污染 |
| 还原 8000 | 200，1.1s |
| 旧进程退出 | exit 0 |
| 回归（转换/用量/契约） | 38/38、22/22、60/60 |

改重启逻辑后必须重跑上表场景（脚本思路：httpx `trust_env=False` 顺序 POST +
轮询 healthz）。
