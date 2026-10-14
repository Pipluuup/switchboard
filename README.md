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
- 🔑 **密钥分发** — 多客户端独立 Key，单独限速 / 限模型 / 随时停用
- 📊 **用量统计** — Token / 延迟 / 成功率，5h · 7d · 30d 滚动窗口
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
