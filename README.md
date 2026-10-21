<div align="center">

# Switchboard

**A local LLM gateway for WorkBuddy, Claude Code & other agent clients**

为 WorkBuddy、Claude Code 等 Agent 打造的本地 LLM 中转网关

[![Python](https://img.shields.io/badge/python-3.10+-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009485)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-green)](#license)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)]()

</div>

---

把任意 **Anthropic / OpenAI 格式**的上游服务桥接给 Agent：客户端不用改任何配置习惯，后端想用哪家就接哪家。控制台内置接入指南，复制一段提示词发给 WorkBuddy，Agent 自行完成接入并自测连通。

## ✨ Features

- 🔀 **协议互转** — Anthropic / OpenAI Chat / OpenAI Responses 三种协议互转，流式 / 工具调用 / 图片全支持
- 🔁 **多上游调度** — 优先级 + 权重，失败自动切换，健康检查 + 故障冷却
- 🏷️ **模型映射** — 别名 → 上游实际模型，可绑定指定节点
- 🔌 **内置 35 家提供商** — 选提供商即自动填 Base URL / 协议 / Key 申请页，也可全自定义
- 📋 **上游模板** — 每个节点可导出分享模板（含 Base URL / 模型 / 备注 / Key 申请网址，不含 Key），导入即预填新建表单
- 🔑 **密钥分发** — 多客户端独立 Key，单独限速 / 限模型 / 随时停用
- 📊 **用量统计** — Token / 延迟 / 成功率，5h · 7d · 30d 滚动窗口
- 🗜️ **Token 优化** — Caveman 引擎转发前压缩输入（Read less）+ Say less 输出约束 + 缓存感知（保护 prompt cache）+ 代码片段保护，日志逐条展示节省
- 🔍 **请求详情日志** — 每次请求的完整请求体 / 响应摘要可随时回看（默认可关）
- 📈 **Plan 实时查询** — OpenCode Go / 火山方舟官方配额实时展示，登录 Cookie 由浏览器扩展一键写入
- 🔗 **客户端一键接入** — 像 ccswitch 一样改写 Claude Code / Codex / Gemini 配置指向本服务，自动备份可还原
- 🖥️ **双形态控制台** — 客户端版弹独立桌面窗口，网页版开浏览器；同一套前后端

## 🚀 Quick Start

```bash
git clone https://github.com/Pipluuup/switchboard.git
cd switchboard
pip install -r requirements.txt
python run.py
```

或直接下载 [Releases](https://github.com/Pipluuup/switchboard/releases) 里的 exe 双击即用
（客户端版弹桌面窗口，网页版开浏览器）。

打开 `http://127.0.0.1:8000`，三步完成配置：

| Step | 操作 |
|:---:|---|
| 1️⃣ | **上游管理** → 新增上游（选提供商自动填 Base URL，填 Key） |
| 2️⃣ | **客户端 Key** → 创建 Key（可设速率 / 额度 / 模型白名单） |
| 3️⃣ | **接入指南** → 复制环境变量或提示词给客户端 |

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000/v1"
export ANTHROPIC_API_KEY="你的客户端Key"
```

之后 Claude Code 直接 `claude` 即可；WorkBuddy 用界面里的接入提示词一键完成。

## 🗜️ Token 优化

控制台「Token 优化」页提供三级瘦身，可叠加使用：

| 能力 | 层级 | 说明 |
|---|---|---|
| **Read less**（Caveman 引擎） | 网关侧 | 转发前压缩窗口外的旧消息：JSON 保结构删噪声、日志只留首尾、其余截断；最近 N 条原文保留，请求日志记录原始请求可对比 |
| **Say less** | 网关侧 | system 注入电报体规则，输出先结论后解释；代码 / 命令原样保留 |
| **RTK** | 客户端侧 | [rtk-ai/rtk](https://github.com/rtk-ai/rtk) 把 `git` / 测试 / 构建命令输出压缩 60–90%（`winget install rtk-ai.rtk`） |

请求日志「优化」列会标注每次请求实际瘦身的字符数、节省率与模式；详情页有完整的压缩统计。

### 🧩 片段保护与配对保护

压缩最容易伤到代码，所以引擎内置两层保护：

- **片段保护（tombstone）**：压缩前先把围栏代码块、行内代码、URL、文件路径、
  全大写常量摘成占位符，压缩完再原样缝回——代码块永远不会被腰斩
- **tool 配对保护**：含 `tool_use` / `tool_result` 的消息不参与压缩，
  避免出现「有调用无结果」的悬空配对（部分 provider 会直接报错）

> 实现参考：OmniRoute 的 tombstone 思路与「结构完整性守卫」。

### 🧊 缓存感知（Cache-Aware）

压缩旧消息会改写「system + 早期消息」这段**可缓存前缀**，上游的 prompt cache 随之失效。
对命中价差大的上游（如 DeepSeek 命中价约为原价 1/10），缓存失效的损失可能大于压缩收益。

因此「Token 优化」页提供**缓存感知**开关（默认开启）：当目标上游支持 prompt cache 时
（Anthropic / OpenAI / DeepSeek / Moonshot / 智谱 / 火山方舟 / Agnes / SiliconFlow 等），
引擎自动进入缓存优先模式——

- 保留窗口自动放大一倍、压缩阈值提高一倍
- 改用**轻量压缩**：只折叠空白 + 保留头尾，不动 JSON / 日志结构
- **不压缩 system**（system 通常是缓存主体）
- **默认不注入 Say less**（改写 system 同样会破坏前缀；需要时可在页面单独开启）

这样压缩对缓存前缀的扰动降到最低，缓存重建后能更快恢复稳定。

> 实现参考：OmniRoute 的 `cachingAware.ts`（`detectCachingContext` + `getCacheAwareStrategy`）。
> 它同样强调：**光看有没有 `cache_control` 标记是不够的**——OpenAI / Codex 这类
> 自动前缀缓存的 provider 不带任何标记，只靠标记判断会导致前缀被改写、缓存必然失效。

### 📐 设计取舍

引擎的设计目标不是「压得越多越好」，而是**在不伤到正确性的前提下压**。几个关键取舍：

| 决策 | 原因 |
|---|---|
| **从不改写用户当前提问** | 改写 prompt 会降低回答质量（这是 caveman 项目自己的研究结论） |
| **压缩是纯函数**（同一原文结果逐字节一致） | 不一致就无法命中 prompt cache |
| **窗口外一刀切，不做分级老化** | 分级老化每降一级就改一次前缀，缓存重建次数更多 |
| **不做语义压缩**（不删「基本上」「I think」这类填充词） | 改写自然语言有质量风险；本项目主场景是编码负载，结构化内容才是大头 |
| **不内置 SLM**（LLMLingua 之类） | 需要额外下载 ONNX 模型，缺依赖时静默失效；收益不抵复杂度 |
| **RTK 用官方 CLI 而非网关内嵌** | 规则由官方维护，更新及时；且客户端侧压缩省的是客户端的上下文 |

实测同一份混合负载（代码块 + 120 行错误日志 + 冗长散文）：

| 模式 | 压缩前 | 压缩后 | 节省 | 代码 / 路径 / URL |
|---|---|---|---|---|
| 常规 | 12,578 | 4,240 | 66% | ✅ 全部保留 |
| 缓存优先 | 12,578 | 10,743 | 15% | ✅ 全部保留 |

前缀稳定性：8 轮对话中，前缀仅在旧消息被挤出窗口时变化 **2 次**，之后完全稳定。

### 🔬 与 OmniRoute 的对比

| 维度 | Switchboard | OmniRoute |
|---|---|---|
| 代码块保护 | ✅ tombstone 摘出 + 缝回 | ✅ 墓碑化后原样缝合 |
| tool 配对保护 | ✅ 含 tool_use/tool_result 的消息跳过 | ✅ 结构完整性守卫 |
| 缓存感知 | ✅ 按上游域名识别 + 弱化模式 | ✅ 按 provider 判断 |
| 压缩统计 | ✅ 原始/压缩后/节省率/模式/能力 | ✅ 每次请求完整 stats |
| 前缀稳定性 | ✅ 挤出窗口后即稳定 | ⚠️ 分级老化，每降一级变一次 |
| 确定性 | ✅ 纯函数 | ⚠️ 含摘要器 / SLM |
| 语义压缩 | ❌ 只做结构感知 | ✅ 30+ 正则规则 + 可选 SLM |
| 引擎数量 | 1 + RTK（客户端侧） | 10+ |
| 依赖 | 纯 Python，零额外依赖 | 需 ONNX 模型 + pythonnet |

## 🔌 客户端一键接入

「客户端接入」页可以像 ccswitch 一样，**一键把本地 Agent 客户端指向本服务**——
不用手改配置文件。机制是改写客户端的配置（切换前自动备份到
`data/client_backups/`，随时可一键还原）：

| 客户端 | 配置文件 | 写入内容 |
|---|---|---|
| Claude Code | `~/.claude/settings.json` | `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_DEFAULT_*_MODEL` |
| Codex CLI | `~/.codex/config.toml` | `model_providers.switchboard`（`wire_api = "responses"`） |
| Gemini CLI | `~/.gemini/.env` | `GOOGLE_GEMINI_BASE_URL` / `GEMINI_API_KEY` |

用法：先建一个**档位**（选客户端 Key + 模型），再在对应客户端卡片上点「应用」。

> 只改写 `env` / 对应配置段，客户端的 `permissions`、`hooks` 等其余内容原样保留。

## 🔁 协议支持

| 协议 | 入站 | 上游 | 说明 |
|---|---|---|---|
| Anthropic Messages | `/v1/messages` | ✅ | Claude Code 原生协议 |
| OpenAI Chat Completions | `/v1/chat/completions` | ✅ | 绝大多数兼容端点 |
| OpenAI Responses | `/v1/responses` | ✅ | Codex / OpenAI 新 SDK / OpenCode Go 的 Grok·GPT Luna·Muse 系列 |

任意两种协议之间可互相转换（请求 / 响应 / 流式都支持）；
上游协议在「上游管理」里按节点选择。

## 📈 Plan 实时查询

「用量」页可实时展示 **OpenCode Go** 与 **火山方舟** Coding Plan 的官方配额。登录 Cookie 无需手动复制——

1. 控制台「设置」→ 下载 **Switchboard Cookie 助手** 浏览器扩展
2. `chrome://extensions` 开发者模式加载解压目录
3. 登录对应控制台后，点扩展按钮一键写回网关

## 🧩 上游接入

「上游管理」→ 新增上游时，先选**提供商**——内置 35 家，选中即自动填好 Base URL、
协议与 Key 申请页；选「自定义」则全部手填，并可填「Key 获取网址」（导出模板时会带上，
方便对方自行申请）。

内置：CommandCode（GOAT）、OpenCode Go / Zen、Agnes AI、SenseNova、DeepSeek、Moonshot、
SiliconFlow、火山方舟、阿里百炼、智谱 GLM、百川智能、MiniMax、百度千帆、阶跃星辰、
无问芯穹、OpenRouter、AiHubMix、AnyRouter、Oatify、HuggingFace、Anthropic 官方、
OpenAI 官方、Google Gemini、Mistral、xAI、Groq、Together AI、Cerebras、Fireworks AI、
NVIDIA NIM、LM Studio / Ollama（本地）+ 自定义。

每个节点还可**导出分享模板**（JSON，含 Base URL / 模型 / 备注 / Key 申请网址，**不含 Key**），
对方导入即预填新建表单。

## 📦 打包分发

提供两个版本，功能完全一致，区别只在界面形态：

| 版本 | 打包命令 | 产物 | 行为 |
|---|---|---|---|
| **客户端版** | `python scripts/build_exe.py --variant gui` | `release/Switchboard.exe`（66 MB） | 双击弹出独立桌面窗口，无需浏览器 |
| **网页版** | `python scripts/build_exe.py --variant web` | `release/Switchboard-Web.exe`（29 MB） | 双击后自动打开浏览器控制台 |

两个版本的数据目录都是 exe 同级的 `data/`，配置与日志各自独立（同端口不能同时跑，
可给其中一个 `--port` 换端口）。

桌面窗口基于 [pywebview](https://pywebview.org)（Windows 上走 WebView2）。
未安装 pywebview 时自动降级为浏览器并提示。

## 🧪 开发

```bash
python tests/test_converter.py     # 协议转换单元测试
python tests/test_usage.py         # 用量窗口单元测试
python tests/test_caveman.py       # 压缩引擎：片段保护 / tool 配对 / 缓存感知 / 统计
python tests/test_responses_converter.py  # Responses 协议转换（请求 / 响应 / 流式）
```

需求与设计文档见 [docs/](docs/README.md)，改代码前先读对应 PRD。

## 📄 License

[MIT](LICENSE) © 2026
