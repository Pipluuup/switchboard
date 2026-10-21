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

- 🔀 **协议互转** — Anthropic ⇄ OpenAI 双向转换，流式 / 工具调用 / 图片全支持
- 🔁 **多上游调度** — 优先级 + 权重，失败自动切换，健康检查 + 故障冷却
- 🏷️ **模型映射** — 别名 → 上游实际模型，可绑定指定节点
- 📋 **上游模板** — 每个节点可导出分享模板（含 Base URL / 模型 / 备注，不含 Key），导入即预填新建表单
- 🔑 **密钥分发** — 多客户端独立 Key，单独限速 / 限模型 / 随时停用
- 📊 **用量统计** — Token / 延迟 / 成功率，5h · 7d · 30d 滚动窗口
- 🗜️ **Token 优化** — Caveman 引擎转发前压缩输入（Read less）+ Say less 输出约束 + 缓存感知（保护 prompt cache）+ 代码片段保护，日志逐条展示节省
- 🔍 **请求详情日志** — 每次请求的完整请求体 / 响应摘要可随时回看（默认可关）
- 📈 **Plan 实时查询** — OpenCode Go / 火山方舟官方配额实时展示，登录 Cookie 由浏览器扩展一键写入
- 🖥️ **Web 控制台** — 全图形化配置，改端口即改即生效

## 🚀 Quick Start

```bash
git clone https://github.com/<you>/switchboard.git
cd switchboard
pip install -r requirements.txt
python run.py
```

打开 `http://127.0.0.1:8000`，三步完成配置：

| Step | 操作 |
|:---:|---|
| 1️⃣ | **上游管理** → 新增上游（Base URL + Key，协议按上游端点选） |
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

## 📈 Plan 实时查询

「用量」页可实时展示 **OpenCode Go** 与 **火山方舟** Coding Plan 的官方配额。登录 Cookie 无需手动复制——

1. 控制台「设置」→ 下载 **Switchboard Cookie 助手** 浏览器扩展
2. `chrome://extensions` 开发者模式加载解压目录
3. 登录对应控制台后，点扩展按钮一键写回网关

## 📦 打包分发

```bash
python scripts/build_exe.py    # PyInstaller 单文件 exe → release/，免 Python 双击即用
```

## 🧪 开发

```bash
python tests/test_converter.py     # 协议转换单元测试
python tests/test_usage.py         # 用量窗口单元测试
```

需求与设计文档见 [docs/](docs/README.md)，改代码前先读对应 PRD。

## 📄 License

[MIT](LICENSE) © 2026
