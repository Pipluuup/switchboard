# Switchboard

带 Web 管理界面的本地 LLM 中转网关，**为 WorkBuddy、Claude Code 等 Agent 客户端打造**。

把任意 Anthropic / OpenAI 格式的上游服务桥接给 Agent 使用：Agent 侧不用改任何配置习惯，后端想用哪家就接哪家。内置 WorkBuddy / Claude Code 接入指南——在控制台复制一段提示词发给 Agent，它会自行完成接入并自测连通。

## 功能

- **协议互转** — Anthropic ⇄ OpenAI 双向转换，流式、工具调用、图片全部支持
- **多上游调度** — 优先级 + 权重，失败自动切换，健康检查 + 故障冷却
- **模型映射** — 别名 → 上游实际模型，可绑定指定节点
- **密钥分发** — 多客户端独立 Key，可单独限速 / 限模型 / 随时停用
- **用量统计** — Token、延迟、成功率，5h / 7d / 30d 滚动窗口（对齐常见订阅额度周期）
- **Web 控制台** — 上游 / 映射 / Key / 日志 / 用量 / 设置全图形化，改端口即改即生效

## 快速开始

```bash
pip install -r requirements.txt
python run.py            # Windows 也可双击 start.bat
```

默认打开 `http://127.0.0.1:8000`，无需改代码，所有配置在界面上完成：

1. **上游管理** → 新增上游：填 Base URL 和 Key，协议按上游端点选（`/v1/messages` 选 Anthropic，`/v1/chat/completions` 选 OpenAI），点测试连通后保存
2. **客户端 Key** → 创建 Key，可设速率 / 额度 / 模型白名单
3. **接入指南** → 复制环境变量给客户端：

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8000/v1"
export ANTHROPIC_API_KEY="你的客户端Key"
# Windows PowerShell: $env:ANTHROPIC_BASE_URL="..."; $env:ANTHROPIC_API_KEY="..."
```

之后 Claude Code 直接 `claude` 即可；WorkBuddy 用界面里的接入提示词一键完成。

### 免 Python 运行

```bash
python scripts/build_exe.py    # 打包单文件 exe 到 release/，双击即用，可任意分发
```

## 更多

- **需求与设计文档**：[docs/](docs/README.md)（产品总览、架构决策、各模块 PRD）
- **测试**：`python tests/test_converter.py` 等独立脚本，见 [tests/](tests/)
- **常见问题**：401 查 Key、上游 404 查协议选型、局域网访问记得设管理口令

数据都在本地 `data/`（配置 JSON + SQLite），设置页支持一键导出/导入。
