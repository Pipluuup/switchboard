"""把 OpenCode Go 接入为上游（一条命令搞定）。

OpenCode Go 把模型分散在三个端点上，本代理只支持其中两个，因此这里建**两条上游**
（同一 Base URL，不同协议），靠「支持的模型」白名单分流：

  /v1/chat/completions → ChatGPT 协议上游（GLM / Kimi / LongCat / DeepSeek / MiMo / Hy / Space Bunny）
  /v1/messages         → Anthropic 协议上游（MiniMax / Qwen）

  /v1/responses        → 本代理不支持，Grok / GPT Luna / Muse Spark 无法接入

脚本是幂等的：按名称查找已有上游并更新，不会重复创建。

用法：
    python scripts/import_opencode_go.py --api-key sk-xxxx
    python scripts/import_opencode_go.py --api-key sk-xxxx --enable
    python scripts/import_opencode_go.py --api-key sk-xxxx --base http://127.0.0.1:9000
    python scripts/import_opencode_go.py --api-key sk-xxxx --admin-token 你的管理口令
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

GO_BASE_URL = "https://opencode.ai/zen/go/v1"

# 走 /v1/chat/completions 的模型（上游协议填 openai）
CHAT_MODELS = [
    "glm-5.3", "glm-5.3-flash", "glm-5.2",
    "kimi-k3", "kimi-k2.7-code", "kimi-k2.6",
    "longcat-2.0", "longcat-2.5-preview-free",
    "deepseek-v4.1-flash", "deepseek-v4-pro", "deepseek-v4-flash",
    "deepseek-v4-flash-vision-exp",
    "mimo-v2.6-flash", "mimo-v2.6-pro", "mimo-v2.5", "mimo-v2.5-pro",
    "hy4-preview", "hy3",
    "space-bunny-free",
]

# 走 /v1/messages 的模型（上游协议填 anthropic）
MESSAGES_MODELS = ["minimax-m3", "minimax-m2.7", "qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus"]

CHAT_UPSTREAM = "OpenCode Go (Chat)"
MESSAGES_UPSTREAM = "OpenCode Go (Messages)"

# 方便在 Claude Code / opencode 里用短别名调用 Go 的模型
ALIASES = [
    ("go-glm", "glm-5.3", "Go: GLM-5.3"),
    ("go-glm-flash", "glm-5.3-flash", "Go: GLM-5.3-Flash（便宜）"),
    ("go-kimi", "kimi-k2.7-code", "Go: Kimi K2.7 Code"),
    ("go-deepseek", "deepseek-v4-pro", "Go: DeepSeek V4 Pro"),
    ("go-minimax", "minimax-m3", "Go: MiniMax M3"),
    ("go-qwen", "qwen3.8-max", "Go: Qwen3.8 Max"),
]


def _request(base: str, token: str, method: str, path: str, body: dict | None = None):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Admin-Token", token)
    # 空 ProxyHandler：本机常驻 HTTP_PROXY（Clash 等）会把 127.0.0.1 的请求也劫持走，
    # 必须显式禁用代理，否则这里会莫名 502。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as r:
            raw = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"[错误] {method} {path} -> HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"[错误] 无法连接 {base}（服务没启动？）：{e.reason}")
    return json.loads(raw) if raw else None


def upsert_upstream(base: str, token: str, spec: dict, enable: bool) -> str:
    existing = _request(base, token, "GET", "/api/upstreams")
    hit = next((u for u in existing if u.get("name") == spec["name"]), None)
    payload = dict(spec)
    if hit:
        # 已启用过的保持启用，避免重复执行脚本把节点退回停用
        payload["enabled"] = bool(hit.get("enabled")) or enable
        _request(base, token, "PUT", f"/api/upstreams/{hit['id']}", payload)
        return f"更新 {spec['name']}（{len(spec['models'])} 个模型）"
    payload["enabled"] = enable
    _request(base, token, "POST", "/api/upstreams", payload)
    return f"新建 {spec['name']}（{len(spec['models'])} 个模型）"


def upsert_mapping(base: str, token: str, alias: str, target: str, note: str) -> str:
    existing = _request(base, token, "GET", "/api/mappings")
    hit = next((m for m in existing if m.get("alias") == alias), None)
    payload = {"alias": alias, "target": target, "upstream_id": "", "enabled": True, "note": note}
    if hit:
        _request(base, token, "PUT", f"/api/mappings/{hit['id']}", payload)
        return f"更新映射 {alias} → {target}"
    _request(base, token, "POST", "/api/mappings", payload)
    return f"新建映射 {alias} → {target}"


def main() -> int:
    p = argparse.ArgumentParser(description="把 OpenCode Go 接入为上游")
    p.add_argument("--api-key", default="",
                   help="OpenCode Go 的 API Key（opencode.ai/auth 获取）；留空则先建好条目，稍后在界面里填")
    p.add_argument("--base", default="http://127.0.0.1:8000", help="本代理地址")
    p.add_argument("--admin-token", default="", help="设置了管理口令时需提供")
    p.add_argument("--enable", action="store_true",
                   help="立即启用这两个上游（默认建为停用，填好 Key 再手工启用）")
    p.add_argument("--no-aliases", action="store_true", help="不创建 go-* 短别名映射")
    args = p.parse_args()

    common = {
        "base_url": GO_BASE_URL.rstrip("/"),
        "api_key": args.api_key,
        "priority": 200,          # 数字比 CommandCode(100) 大 => 仅当模型名只匹配 Go 时才用得到
        "weight": 1,
        "timeout": 180,
        "max_retries": 2,
    }
    chat = dict(common, name=CHAT_UPSTREAM, protocol="openai", models=CHAT_MODELS,
                note="OpenCode Go：/v1/chat/completions 系列（GLM/Kimi/DeepSeek/MiMo/LongCat/Hy）")
    messages = dict(common, name=MESSAGES_UPSTREAM, protocol="anthropic", models=MESSAGES_MODELS,
                    note="OpenCode Go：/v1/messages 系列（MiniMax/Qwen）")

    print(f"目标服务：{args.base}")
    print("=" * 60)
    print("  " + upsert_upstream(args.base, args.admin_token, chat, args.enable))
    print("  " + upsert_upstream(args.base, args.admin_token, messages, args.enable))

    if not args.no_aliases:
        for alias, target, note in ALIASES:
            print("  " + upsert_mapping(args.base, args.admin_token, alias, target, note))

    print("=" * 60)
    print("  未支持的模型（本代理没有 /v1/responses 路由）：")
    print("    grok-4.7, grok-4.6, gpt-6-luna, gpt-5.6-luna,")
    print("    muse-spark-1.3-contributor, muse-spark-1.2-contributor")
    print()
    if not args.enable:
        print("  上游已建为「停用」状态。到管理界面「上游管理」逐条点「编辑」")
        print("  确认 API Key 后勾选「启用该节点」，再点「测试」验证连通性。")
    print("  在 Claude Code 里可用别名调用：go-glm / go-kimi / go-deepseek / go-minimax / go-qwen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
