# Switchboard

带 Web 管理界面的本地中转网关，**为 WorkBuddy、Claude Code 等 Agent 客户端打造**。把任意 Anthropic / OpenAI 格式的上游服务（CommandCode、OpenCode Go 等）桥接给 Agent 使用，内置 WorkBuddy / Claude Code 一键接入指南（发一段提示词，Agent 自行完成接入并自测连通），同时提供多上游故障切换、模型映射、密钥分发、限流与用量统计。

相比指南里那个硬编码的 `proxy.py`，这个版本：

| 能力 | 指南版 proxy.py | 本软件 |
|---|---|---|
| 配置方式 | 改代码里的常量 | Web 界面，实时生效 |
| 上游节点 | 单个，写死 | 多节点，优先级 + 权重 + 故障自动切换 |
| 工具调用（Tool Use） | 不支持 | 完整支持，Claude Code 可正常调用工具 |
| 模型映射 | 无 | 别名 → 上游实际模型，可绑定指定节点 |
| 密钥分发 | 无 | 多客户端独立 Key，可单独限速 / 限模型 / 停用 |
| 用量统计 | 无 | Token、延迟、成功率、24h 趋势 + **5h/7d/30d 滚动窗口** |
| 协议方向 | 仅 Anthropic 入 | 双向：Anthropic ⇄ OpenAI 互转 |
| 健康检查 | 无 | 后台周期体检 + 失败冷却 |

> 📚 **需求与设计文档在 [docs/](docs/README.md)**：产品总览、架构决策、各模块 PRD
> （代理核心 / 用量统计 / OpenCode Go 接入 / 端口热更新）。改代码前先读对应文档，
> 改完行为记得同步。

## 目录结构

```
Switchboard/
├── run.py                 # 启动入口（源码运行）
├── start.bat              # Windows 双击启动（控制台窗口）
├── start-hidden.vbs       # 静默启动，无窗口
├── service.bat            # 服务管理菜单（状态/停止/开机自启）
├── requirements.txt
├── scripts/
│   ├── service.py         # 服务管理：状态、停止、开机自启
│   ├── build_exe.py       # 打包为 Windows exe（输出到 release/）
│   └── import_opencode_go.py  # 一键把 OpenCode Go 配成上游
├── backend/
│   ├── main.py            # FastAPI 应用、静态站点挂载、后台任务
│   ├── config.py          # 配置模型与 JSON 持久化
│   ├── converter.py       # Anthropic ⇄ OpenAI 双向协议转换（核心）
│   ├── upstream_pool.py   # 上游调度、健康检查、故障冷却、代理绕过
│   ├── proxy_routes.py    # /v1/messages、/v1/chat/completions 代理主逻辑
│   ├── admin_routes.py    # /api/* 管理接口
│   ├── database.py        # SQLite 请求日志与统计（含 5h/7d/30d 用量窗口）
│   ├── limiter.py         # 滑动窗口限流
│   └── paths.py           # 运行时路径（兼容源码 / 打包后 _MEIPASS）
├── web/                   # 前端（原生 HTML/CSS/JS，无需构建）
├── tests/                 # 模拟上游 + 端到端 + 转换层 + 用量窗口 + 契约检查
├── release/               # 打包产物（独立 exe，免 Python 运行）
│   ├── Switchboard.exe   # 单文件 exe，双击即用
│   ├── README.txt             # exe 版使用说明
│   └── silent-start.vbs       # 静默启动（无窗口）
└── data/                  # 运行时生成：config.json、proxy.db
```

> `release/` 里的 exe 是免 Python 环境的独立程序，把 `release/` 整个文件夹拷到任意
> Windows 机器即可运行，无需安装任何依赖。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动

```bash
python run.py
# 或 Windows 双击 start.bat
```

默认监听 `127.0.0.1:8000`，会自动打开浏览器。想换端口：`python run.py --port 9000`，或者直接在
Web 控制台「设置 → 监听端口」里改——保存后服务会自动重启到新地址并生效，无需手动重启。

### 3. 日常运行与开机自启

| 文件 | 作用 |
|---|---|
| `start.bat` | 双击启动，显示控制台窗口（能看到实时日志） |
| `start-hidden.vbs` | 双击静默启动，无窗口，适合日常使用 |
| `service.bat` | 服务管理菜单：查看状态 / 停止 / 开机自启 |

命令行：

```bash
python scripts/service.py status            # 查看运行状态
python scripts/service.py stop              # 停止服务
python scripts/service.py autostart on      # 设为开机自启
python scripts/service.py autostart off     # 取消开机自启
python scripts/service.py                   # 交互式菜单
```

开机自启通过计划任务实现（任务名 `Switchboard`），登录后自动在后台启动，无窗口。
若所在环境禁用了 `schtasks`，脚本会提示改用手动方式：把 `start-hidden.vbs`
的快捷方式放进 `shell:startup` 目录（Win+R 输入即可打开）。

### 4. 配置上游

打开管理界面 → **上游管理** → 新增上游：

| 字段 | 说明 |
|---|---|
| 名称 | 便于识别即可，如「Goat 主节点」 |
| 协议 | 上游是 `/v1/messages` 选 **Anthropic**；是 `/v1/chat/completions` 选 **OpenAI** |
| Base URL | 例如 `https://api.commandcode.dev/v1`（结尾不要带斜杠） |
| API Key | 上游的 Key / Token |
| 优先级 | 数字越小越优先，同优先级内按权重 |
| 支持的模型 | 留空表示不限；填了则只有请求的模型在列表内才会调度到它 |

填完点**测试**，能返回「连通成功」再保存。如果测试报 404，八成是协议选错了。

### 4.1 接入 OpenCode Go

[OpenCode Go](https://opencode.ai/docs/go/) 的模型分散在**三个端点**上，本代理支持其中两个：

| Go 端点 | 模型 | 上游协议 |
|---|---|---|
| `/v1/chat/completions` | GLM / Kimi / LongCat / DeepSeek / MiMo / Hy / Space Bunny | OpenAI |
| `/v1/messages` | MiniMax M3 · M2.7 / Qwen3.8 Max · Flash / Qwen3.7 Plus | Anthropic |
| `/v1/responses` | Grok 4.7 · 4.6 / GPT 6 Luna / GPT 5.6 Luna / Muse Spark | ❌ 本代理不支持 |

一条上游只能是一种协议，所以要建**两条**（同一 Base URL），靠「支持的模型」白名单分流。
脚本会替你建好，并且是幂等的：

```bash
python scripts/import_opencode_go.py --api-key sk-你的Go密钥 --enable
```

它会创建 `OpenCode Go (Chat)` / `OpenCode Go (Messages)` 两条上游，以及
`go-glm` / `go-kimi` / `go-deepseek` / `go-minimax` / `go-qwen` 等短别名映射。
之后在客户端里直接请求 `go-glm` 即可（别名会在调度前解析成 `glm-5.3`）。

> 不传 `--api-key` 也能跑，会先建好条目，密钥留到界面里填——记得填完勾选「启用该节点」。

### 4.2 用量怎么看

左侧 **用量** 页按 **5 小时 / 7 天 / 30 天**三个**滚动**窗口（从当前时刻往前回溯，
不是自然日/自然月）统计请求数与 Token；窗口切换后可按模型看占比明细。

这与 Go 的额度周期是对齐的（5 小时 = 月额度 20%、每周 50%、每月 100%）。

> ⚠️ 「近 30 天」需要日志存在 30 天。默认日志只保留 **7 天**，长窗口会被截断——
> 页面会给出黄色提示。到「设置 → 日志保留天数」改成 `0`（永久）或 `30` 以上即可。

### 5. 创建客户端 Key

**客户端 Key** → 新增 Key。可设置：

- 速率限制（次/分钟）
- 每日 Token 额度
- 允许的模型白名单
- 绑定固定上游

> 只要存在「启用」状态的 Key，代理就会要求客户端携带合法 Key；把所有 Key 停用或删除，则退化为匿名放行模式。

### 6. 接入 Claude Code

界面右上角 **接入指南** 会给出当前配置对应的环境变量，复制即可：

```bash
# macOS / Linux
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000/v1"
export ANTHROPIC_API_KEY="你的客户端Key"

# Windows PowerShell
$env:ANTHROPIC_BASE_URL="http://127.0.0.1:8000/v1"
$env:ANTHROPIC_API_KEY="你的客户端Key"
```

Windows 想永久生效：

```powershell
setx ANTHROPIC_BASE_URL "http://127.0.0.1:8000/v1"
setx ANTHROPIC_API_KEY "你的客户端Key"
```

然后在 VS Code 终端里直接 `claude` 即可。

用 `ccswitch` 的话，在配置里加一段：

```json
{
  "providers": {
    "switchboard": {
      "name": "Switchboard",
      "api_base": "http://127.0.0.1:8000/v1",
      "api_key": "你的客户端Key",
      "model": "claude-sonnet-4-5"
    }
  }
}
```

## 协议支持

代理对**入站**和**出站**协议自动适配，四种组合全部支持：

| 客户端协议 | 上游协议 | 处理方式 |
|---|---|---|
| Anthropic | Anthropic | 直接透传（零转换开销） |
| Anthropic | OpenAI | 请求转 OpenAI，响应流转回 Anthropic 事件流 |
| OpenAI | OpenAI | 直接透传 |
| OpenAI | Anthropic | 请求转 Anthropic，响应流转回 OpenAI SSE |

支持的参数：系统提示词、多轮对话、图片（base64 / URL）、工具定义与工具调用、tool_choice、temperature、top_p、stop、max_tokens、流式与非流式。

### 端点

- `POST /v1/messages` — Anthropic Messages API
- `POST /v1/messages/count_tokens` — Token 预估算
- `POST /v1/chat/completions` — OpenAI Chat Completions API
- `GET /v1/models` — 模型列表（返回已配置的别名）

## 故障切换

1. 按「优先级升序 → 权重降序」挑选候选上游
2. 请求失败（连接错误 / 5xx / 429）时，记录失败并切换到下一个候选
3. 单个节点连续失败达到**失败阈值**（默认 3 次）后进入**冷却期**（默认 60 秒），冷却期内不再调度
4. 后台周期性体检（默认 120 秒）会自动恢复健康节点

在界面上可以随时「重置」某个节点状态，或「批量体检」全部节点。

## 管理 API

基础路径 `/api`，若设置了管理口令需在请求头携带 `X-Admin-Token`。

```
GET    /api/overview              概览统计
GET    /api/usage                 用量：5h / 7d / 30d 滚动窗口 + 模型明细
GET    /api/upstreams             上游列表
POST   /api/upstreams             新增上游
PUT    /api/upstreams/{id}        修改上游
DELETE /api/upstreams/{id}        删除上游
POST   /api/upstreams/{id}/test   连通性测试（发一条真实消息）
POST   /api/upstreams/{id}/health 健康检查（仅 /v1/models）
POST   /api/upstreams/{id}/reset  重置节点状态
GET    /api/upstreams/{id}/models 拉取上游可用模型
POST   /api/upstreams/health-all  批量体检
GET/POST/PUT/DELETE /api/mappings 模型映射 CRUD
GET/POST/PUT/DELETE /api/keys     客户端 Key CRUD
POST   /api/keys/{id}/regenerate  重置密钥
GET    /api/logs                  请求日志（limit/offset/ok/keyword）
DELETE /api/logs                  清空日志
GET/PUT /api/settings             设置（host/port 不在此受理）
POST   /api/server/restart        修改监听地址并自动交接端口（见下）
GET    /api/config/export         导出配置
POST   /api/config/import         导入配置
GET    /api/quickstart            接入示例
```

### 修改监听地址/端口（即改即生效）

`POST /api/server/restart`，请求体 `{"host": "127.0.0.1", "port": 9000}`（缺省取当前配置）。
流程：绑定预检（含最多 4 秒重试，容忍旧进程退出宽限）→ 写入配置 → 拉起脱离进程树的新进程
→ 轮询其 `/healthz` → 就绪后旧进程退出。任一步失败都会保持旧服务运行并回滚配置，不会出现
「配置指向新端口却没有任何服务在跑」。注意：同端口只改监听地址（如 `127.0.0.1` → `0.0.0.0`）
无法在线交接，需先停止服务再启动；交接后的新进程日志写入 `data/server.log`。

## 测试

分三层，都不需要真实上游：

**1. 转换层单元测试**（纯函数，秒级，改转换逻辑后应先跑）：

```bash
python tests/test_converter.py    # 预期 38/38 通过
```

覆盖请求/响应/流式的双向转换、工具调用参数拼接、图片、非法 JSON 兜底、空响应补齐。

**2. 用量窗口单元测试**（临时 SQLite，秒级，改统计口径后跑）：

```bash
python tests/test_usage.py        # 预期 22/22 通过
```

覆盖 5h/7d/30d 的窗口边界（6 小时前的请求不能落进 5h 窗口、40 天前的不落进任何窗口）、
Token 汇总、成功率、以及模型聚合取的是**映射后**的名字（消耗上游额度的那个）。

**3. 前后端契约检查**（静态分析，不需要浏览器）：

```bash
python tests/test_frontend_contract.py    # 预期 60/60 通过
```

校验前端引用的 DOM id 在 HTML 中都存在、调用的 API 后端都已实现、
每个导航视图都有数据加载函数、后端返回字段与前端读取一致。
改前端后跑它，可以避免「打开就白屏」。

**4. 端到端测试**（需要服务与模拟上游）：

```bash
# 终端 1
python run.py --no-browser --port 8000
# 终端 2
python tests/mock_upstream.py --port 8100
# 终端 3
python tests/test_e2e.py          # 预期 44/44 通过
```

覆盖四种协议组合、多轮工具结果、故障切换、限流、鉴权与错误格式。

> ⚠️ e2e 开头的清场步骤会**删除实例上全部上游、映射与 Key**。别对着你在用的实例跑：
> 用 `SWITCHBOARD_DATA_DIR` 指到临时目录另起一个实例，再让测试打它。

## 打包 exe（release/）

免 Python 环境、双击即用的独立程序。打包脚本基于 PyInstaller：

```bash
pip install pyinstaller
python scripts/build_exe.py                # 单文件（release/Switchboard.exe）
python scripts/build_exe.py --mode onedir  # 单目录（启动更快）
python scripts/build_exe.py --purge        # 仅清理 build/ 中间产物
```

打包后会写入 `release/Switchboard.exe`，连同 `release/README.txt`、`release/silent-start.vbs`
一起拷贝即可分发。打包逻辑对路径做了隔离处理：

- 只读资源（web/）取自 PyInstaller 临时解压目录 `_MEIPASS`
- 数据目录默认在 **exe 同级 `data/`**，拷贝整个 `release/` 即可迁移；若该目录不可写
  （如装在 `Program Files`），自动改用 `%LOCALAPPDATA%\Switchboard\data`
- 可用环境变量 `SWITCHBOARD_DATA_DIR` 强制指定数据目录（旧名 `CCP_DATA_DIR` 仍兼容）

> 打包本身已验证：在「本机存在 HTTP 代理」的环境下，exe 对 127.0.0.1 上游
> 仍能稳定工作（见下节「本机代理」）。

## 常见问题

**Claude Code 提示 401**
检查 `ANTHROPIC_API_KEY` 是否与界面里的客户端 Key 完全一致，且该 Key 处于启用状态。

**上游测试报 404**
协议选错了。上游提供 `/v1/messages` 就选 Anthropic，提供 `/v1/chat/completions` 就选 OpenAI。

**上游测试报 401 / 403**
上游 Key 有误或已过期。若上游是 Goat 套餐，确认 Token 是否带 `sk-` 前缀。

**Claude Code 能聊天但无法使用工具**
确认上游模型本身支持 function calling / tool use。可以在「请求日志」里看失败详情。

**局域网访问**
启动时加 `--host 0.0.0.0`，并务必在设置里配置管理口令——否则任何人都能修改你的上游配置。

**想让上游请求走系统代理**
设置里的「使用系统代理」默认开启，会读取 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量；若上游是内网地址或你希望直连，关掉它即可。

**本机代理（Clash / v2ray / 系统代理）导致上游间歇 404/502**
如果你开着系统代理且勾选了「代理局域网连接 / 处理本地流量」，指向 `127.0.0.1`、
`localhost` 的上游会被代理软件劫持而失败。本软件已做特殊处理：**127.0.0.1 / localhost
等本机地址永远绕过系统代理**，无论「使用系统代理」开关如何；只有公网（非 loopback）
上游才会按该开关决定是否走代理。若你确实需要让本机上游也走代理，请改用非 loopback
域名或公网地址。

## 数据备份

配置文件在 `data/config.json`（含上游、映射、Key），日志在 `data/proxy.db`。设置页支持一键导出/导入配置 JSON，换机器时很方便。

> 注意：导出的配置包含上游 API Key 明文，请妥善保管。导入配置不包含监听地址与端口。
