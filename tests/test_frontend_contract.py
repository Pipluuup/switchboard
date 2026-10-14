"""前后端契约一致性检查（无需浏览器）。

前端白屏/报错绝大多数源于两类问题，这里用静态分析提前抓出来：
  1. 前端引用了 index.html 里不存在的 DOM id        -> null 引用崩溃
  2. 前端调用了后端不存在的 API 路径                 -> 404 / 数据为空

运行：python tests/test_frontend_contract.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond else ""))


def norm_path(p: str) -> str:
    """把 ${id} 与 {uid} 统一成 {}，便于前后端路径对比。"""
    p = re.sub(r"\$\{[^}]*\}", "{}", p)
    p = re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", "{}", p)
    return p.rstrip("/") or "/"


def main() -> int:
    app_js = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    index_html = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
    style_css = open(os.path.join(ROOT, "web", "style.css"), encoding="utf-8").read()

    # ---------------------------------------------------------------- DOM id
    print("\n[1] DOM id 引用一致性")
    html_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', index_html))
    # 前端运行时动态注入的 id（弹窗表单等模板字符串里定义的）
    dynamic_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', app_js))
    # 代码里 $('#xxx') 形式的引用。
    # 用 (?=[\).]) 前瞻排除 $('#view-' + v) 这类拼接——它引号后跟的是 " + v"。
    referenced = set(re.findall(r"\$\$?\('#([A-Za-z0-9_-]+)'(?=[\)\.])", app_js))
    referenced |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", app_js))
    # 通过 set('#id', v) 辅助函数引用的（设置页字段）
    referenced |= set(re.findall(r"\bset\('#([A-Za-z0-9_-]+)'", app_js))

    static_refs = referenced - dynamic_ids
    missing = sorted(static_refs - html_ids)
    check("静态 DOM 引用全部存在于 index.html", not missing,
          "缺失: " + ", ".join(missing))

    # view-* 由 $('#view-' + v) 动态拼接引用，不算孤立
    unused = sorted(i for i in html_ids - referenced - dynamic_ids
                    if not i.startswith("view-"))
    check("index.html 中无未被引用的孤立 id", not unused, "未引用: " + ", ".join(unused))

    # ---------------------------------------------------------------- API 路径
    print("\n[2] API 路径一致性")
    fe_paths = set()
    for m in re.findall(r"['\"`](/api/[A-Za-z0-9_/{}$.\-]*)['\"`]", app_js):
        fe_paths.add(norm_path(m))

    from backend.admin_routes import router as admin_router  # noqa: PLC0415
    from backend.main import app  # noqa: PLC0415

    be_paths = set()
    for r in admin_router.routes:
        for _ in (getattr(r, "methods", None) or {"GET"}):
            be_paths.add(norm_path(r.path))
    for r in app.routes:
        p = getattr(r, "path", None)
        if p and p.startswith("/api"):
            be_paths.add(norm_path(p))

    # 前端可能只用到部分接口，这里只检查「前端用到的后端必须有」
    missing_api = sorted(fe_paths - be_paths)
    check("前端调用的 API 后端均已实现", not missing_api,
          "后端缺失: " + ", ".join(missing_api))
    print(f"        前端调用 {len(fe_paths)} 个接口，后端实现 {len(be_paths)} 个")

    # ---------------------------------------------------------------- 视图
    print("\n[3] 视图与导航一致性")
    nav_views = set(re.findall(r'data-view="([a-z]+)"', index_html))
    view_sections = set(re.findall(r'id="view-([a-z]+)"', index_html))
    check("每个导航项都有对应内容区", nav_views == view_sections,
          f"导航 {sorted(nav_views)} vs 内容区 {sorted(view_sections)}")

    js_views = set(re.findall(r"^\s{4}([a-z]+):\s*(?:load\w+)?,?\s*$", app_js, re.M))
    # 非贪婪匹配到对象结尾的 "};"（中间各行以 "}," 结尾，不会被误截断）
    declared = re.findall(r"VIEWS\s*=\s*\{(.*?)\};", app_js, re.S)
    if declared:
        keys = set(re.findall(r"([a-z]+):\s*\{", declared[0]))
        check("VIEWS 定义覆盖全部导航项", nav_views <= keys,
              f"VIEWS {sorted(keys)} vs 导航 {sorted(nav_views)}")
        loaders = re.findall(r"loaders\s*=\s*\{([^}]*)\}", app_js, re.S)
        if loaders:
            lk = set(re.findall(r"([a-z]+):\s*load\w+", loaders[0]))
            check("每个视图都有对应的数据加载函数", keys <= lk,
                  f"缺少加载器: {sorted(keys - lk)}")

    # ---------------------------------------------------------------- 资源引用
    print("\n[4] 静态资源与样式")
    for asset in ("style.css", "app.js"):
        check(f"index.html 引用了 {asset}", asset in index_html)
    check("样式表定义了 .hidden（视图切换依赖它）", ".hidden" in style_css)
    for cls in ("nav-item", "panel", "card", "badge", "modal-mask", "toast"):
        check(f"样式表定义了 .{cls}", f".{cls}" in style_css)

    # ---------------------------------------------------------------- 关键后端契约
    print("\n[5] 后端返回字段与前端读取一致性")
    import httpx  # noqa: PLC0415

    client = httpx.Client(trust_env=False, timeout=15)
    base = "http://127.0.0.1:8000"
    try:
        ov = client.get(f"{base}/api/overview").json()
        for f in ("stats", "upstream_count", "mapping_count", "key_count", "timeline"):
            check(f"overview 返回字段 {f}", f in ov, str(list(ov)[:8]))
        for f in ("total_requests", "success_rate", "by_upstream", "by_model"):
            check(f"overview.stats 返回字段 {f}", f in ov["stats"], str(list(ov["stats"])[:8]))

        us = client.get(f"{base}/api/usage").json()
        for f in ("windows", "retention_days", "generated_at"):
            check(f"usage 返回字段 {f}", f in us, str(list(us)[:8]))
        check("usage.windows 是 5h/7d/30d 三个窗口",
              [w.get("key") for w in us.get("windows", [])] == ["5h", "7d", "30d"],
              str([w.get("key") for w in us.get("windows", [])]))
        for w in us.get("windows", [])[:1]:
            for f in ("key", "label", "requests", "ok_requests", "failed_requests",
                      "success_rate", "input_tokens", "output_tokens", "total_tokens",
                      "avg_latency_ms", "by_model", "by_upstream"):
                check(f"usage.window 返回字段 {f}", f in w, str(sorted(w)[:16]))

        ups = client.get(f"{base}/api/upstreams").json()
        if ups:
            u = ups[0]
            for f in ("id", "name", "base_url", "protocol", "priority",
                      "healthy", "in_cooldown", "api_key_masked", "total_requests"):
                check(f"upstream 返回字段 {f}", f in u, str(sorted(u)[:12]))

        ks = client.get(f"{base}/api/keys").json()
        if ks:
            for f in ("id", "name", "key", "masked", "used_today", "enabled"):
                check(f"key 返回字段 {f}", f in ks[0], str(sorted(ks[0])[:12]))

        lg = client.get(f"{base}/api/logs?limit=1").json()
        for f in ("items", "total"):
            check(f"logs 返回字段 {f}", f in lg)

        qs = client.get(f"{base}/api/quickstart").json()
        for f in ("base_url", "api_key", "snippets"):
            check(f"quickstart 返回字段 {f}", f in qs)
    except Exception as e:  # noqa: BLE001
        check("后端服务可访问（需先启动 run.py）", False, f"{type(e).__name__}: {e}")

    ok = sum(1 for _, s, _ in results if s)
    print("\n" + "=" * 56)
    print(f"  契约检查：{ok}/{len(results)} 通过")
    print("=" * 56)
    for n, s, d in results:
        if not s:
            print(f"  - {n}: {d}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
