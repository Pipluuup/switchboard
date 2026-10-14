"""把 Agnes AI（api.agnes-ai.cn，免费 Omni-Modal 网关）接入为上游。

单条 OpenAI 协议上游（/v1/chat/completions），模型按「模板」批量建，
名称后带「 (free)」标注，方便在界面里一眼认出是免费节点。

脚本是幂等的：按名称查找已有上游并更新，不会重复创建。

用法：
    python scripts/import_agnes_free.py
    python scripts/import_agnes_free.py --api-key sk-xxx --enable
    python scripts/import_agnes_free.py --base http://127.0.0.1:9000
    python scripts/import_agnes_free.py --admin-token 你的管理口令
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

AGNES_BASE_URL = "https://api.agnes-ai.cn/v1"
UPSTREAM_NAME = "Agnes AI (free)"

# 模板模型（免费层常见的 omni 系列；填 Key 后可用 /models 接口核对并增删）
AGNES_MODELS = [
    "agnes-omni-flash",
    "agnes-omni-flash-lite",
    "agnes-omni-flash-2",
]

# 短别名 → 实际模型名
ALIASES = [
    ("agnes", "agnes-omni-flash", "Agnes 免费: Omni-Flash"),
    ("agnes-flash", "agnes-omni-flash-lite", "Agnes 免费: Omni-Flash Lite"),
]


def _request(base: str, token: str, method: str, path: str, body: dict | None = None):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Admin-Token", token)
    # 禁用系统代理（本机常驻 HTTP_PROXY 会劫持 127.0.0.1）
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
    p = argparse.ArgumentParser(description="把 Agnes AI 免费节点接入为上游")
    p.add_argument("--api-key", default="",
                   help="Agnes AI 的 API Key（agnes-ai.com 注册获取）；留空则先建条目，稍后在界面填")
    p.add_argument("--base", default="http://127.0.0.1:8000", help="本代理地址")
    p.add_argument("--admin-token", default="", help="设置了管理口令时需提供")
    p.add_argument("--enable", action="store_true",
                   help="立即启用（默认停用，填好 Key 再启用，避免空 Key 刷 401 进冷却）")
    p.add_argument("--no-aliases", action="store_true", help="不创建 agnes-* 别名映射")
    args = p.parse_args()

    spec = {
        "name": UPSTREAM_NAME,
        "protocol": "openai",
        "base_url": AGNES_BASE_URL,
        "api_key": args.api_key,
        "priority": 300,   # 低于 CommandCode(100) / Go(200)，仅当模型名唯一匹配它时启用
        "weight": 1,
        "timeout": 180,
        "max_retries": 2,
        "models": [f"{m} (free)" for m in AGNES_MODELS],
        "note": "Agnes AI 免费 Omni-Modal 网关（api.agnes-ai.cn），模型名带 (free) 标注",
    }

    print(f"目标服务：{args.base}")
    print("=" * 60)
    print("  " + upsert_upstream(args.base, args.admin_token, spec, args.enable))
    if not args.no_aliases:
        for alias, target, note in ALIASES:
            print("  " + upsert_mapping(args.base, args.admin_token, alias, target, note))
    print("=" * 60)
    if not args.enable:
        print("  上游建为「停用」状态。在 agnes-ai.com 注册拿 Key 后，到「上游管理」")
        print("  编辑填 Key → 勾选启用 → 测试。填好 Key 后可先 GET /v1/models 核对模型清单。")
    print("  在 Claude Code 里可用别名调用：agnes / agnes-flash")
    return 0


if __name__ == "__main__":
    sys.exit(main())
