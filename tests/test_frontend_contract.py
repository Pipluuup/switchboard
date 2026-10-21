"""前端 ↔ 后端契约测试（React 版）。

旧版这个文件检查的是 `web/app.js` 里的 DOM id，但前端迁到 React 之后
`web/app.js` 已不存在，测试长期处于 FileNotFoundError 状态、形同虚设。
现在改为检查**真正的契约面**：

1. **API 路径对齐**：前端调用的 `/api/*` 路径必须都在后端 `admin_routes.py`
   里注册过（反向前端用、后端没有的路径会直接 404）。
2. **后端可达性**：后端注册的管理面路径必须至少被前端用到一处
   （反向后端有、前端从不调用 = 死代码或漏做的功能）。
3. **协议枚举一致**：前后端对 `protocol` 取值的定义必须一致
   （前端类型窄化会让「Responses」这种合法值在 TS 层就被拒）。
4. **视图 ↔ 路由对齐**：App.tsx 的 ViewKey 必须在 MENU_ITEMS 里出现，
   且每个视图组件文件真实存在。

运行：python tests/test_frontend_contract.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_SRC = os.path.join(ROOT, "frontend", "src")
ADMIN_ROUTES = os.path.join(ROOT, "backend", "admin_routes.py")
PROXY_ROUTES = os.path.join(ROOT, "backend", "proxy_routes.py")

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if not cond else ""))


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _frontend_files() -> list[str]:
    out: list[str] = []
    for dirpath, _dirs, files in os.walk(FRONTEND_SRC):
        for fn in files:
            if fn.endswith((".ts", ".tsx")):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


# ---------------------------------------------------------------- 采集


def frontend_api_paths() -> set[str]:
    """前端所有硬编码的 /api/... 路径。

    两种写法都要覆盖：
    - 完整字面量：GET('/api/keys')
    - 拼接前缀：PUT('/api/keys/' + id) → 记为 /api/keys/{param}
    """
    paths: set[str] = set()
    for f in _frontend_files():
        src = _read(f)
        # 拼接前缀：引号内的路径以 / 结尾，后面跟着字符串拼接
        for m in re.finditer(r"['\"`](/api/[^'\"`?]*)/['\"`]\s*\+", src):
            paths.add(m.group(1).rstrip("/") + "/{param}")
        # 模板字符串：/api/logs/${id}
        for m in re.finditer(r"['\"`](/api/[^'\"`?]*)\$\{", src):
            paths.add(m.group(1).rstrip("/") + "/{param}")
        # 完整字面量（排除上面两类，避免重复与误判）
        for m in re.finditer(r"['\"`](/api/[^'\"`?$]*)['\"`]", src):
            raw = m.group(1).rstrip("/")
            if raw and not raw.endswith("/"):
                paths.add(raw)
    return paths


def backend_admin_paths() -> set[str]:
    """后端 admin_routes.py 注册的管理面路径（已拼上 /api 前缀）。"""
    src = _read(ADMIN_ROUTES)
    out: set[str] = set()
    for m in re.finditer(r'@router\.(get|post|put|delete)\("([^"]+)"', src):
        out.add(("/api" + m.group(2)).rstrip("/"))
    return out


def backend_proxy_paths() -> set[str]:
    src = _read(PROXY_ROUTES)
    out: set[str] = set()
    for m in re.finditer(r'@router\.(get|post|put|delete)\("([^"]+)"', src):
        out.add(m.group(2).rstrip("/"))
    return out


def _matches_backend(path: str, backend: set[str]) -> bool:
    """前端路径能否命中后端某个注册路径。

    后端用 FastAPI 路径参数（/keys/{kid}），前端用字符串拼接
    （'/api/keys/' + id），所以要按段比较：把后端的花括号段当通配。
    """
    segs = path.strip("/").split("/")
    for b in backend:
        bsegs = b.strip("/").split("/")
        if len(bsegs) != len(segs):
            continue
        ok = True
        for a, bb in zip(segs, bsegs):
            if bb.startswith("{") and bb.endswith("}"):
                continue
            if a != bb:
                ok = False
                break
        if ok:
            return True
    return False


# ---------------------------------------------------------------- 1. 前端 → 后端


def test_frontend_paths_exist_in_backend():
    """前端用到的管理面路径，后端必须真的注册了。"""
    fe = frontend_api_paths()
    be = backend_admin_paths()
    missing = sorted(p for p in fe if not _matches_backend(p, be))
    check("前端 API 路径均有后端实现", not missing, "缺失: " + ", ".join(missing))


# ---------------------------------------------------------------- 2. 后端 → 前端


def test_backend_paths_used_by_frontend():
    """后端注册的管理面路径应至少被前端调用一次（否则是死接口）。

    匹配方式：取后端路径里第一个路径参数之前的**静态前缀**，
    去看前端源码里有没有出现这个前缀的字面量。
    这样 '/api/keys/' + id + '/regenerate' 这种多段拼接也能命中
    后端的 '/api/keys/{kid}/regenerate'。
    """
    fe_src = "".join(_read(f) for f in _frontend_files())
    be = backend_admin_paths()
    unused = []
    for p in sorted(be):
        # 静态前缀：第一个 { 之前的那段
        prefix = p.split("{")[0].rstrip("/")
        if not prefix:
            continue
        # 前端要么整路径字面量，要么以该前缀 + 拼接出现
        if f"'{prefix}'" in fe_src or f'"{prefix}"' in fe_src or f"`{prefix}`" in fe_src:
            continue
        if prefix in fe_src:
            continue
        unused.append(p)
    check("后端管理面路径均被前端使用", not unused, "未被使用: " + ", ".join(unused))


# ---------------------------------------------------------------- 3. 协议枚举


def test_protocol_values_match():
    """前后端的 protocol 取值集合必须一致。"""
    upstreams_src = _read(os.path.join(FRONTEND_SRC, "views", "Upstreams.tsx"))

    # 前端协议下拉：<Form.Item name="protocol"> 里的 Select options。
    # 只在该 Form.Item 块内找，避免把「提供商」条目（同样有 value/label）误当协议。
    pm = re.search(r'name="protocol"[^>]*>(.*?)</Form\.Item>', upstreams_src, re.S)
    fe_options: set[str] = set()
    if pm:
        fe_options = set(re.findall(r"value: '([a-z]+)'", pm.group(1)))

    # 前端类型声明里的联合类型（Upstream.protocol / Provider.protocol）。
    # 协议可能被声明成具名别名（如 type UpstreamProtocol = 'anthropic' | ...），
    # 这里把别名一并解析出来。
    fe_types: set[str] = set()
    alias_defs: dict[str, set[str]] = {}
    for f in _frontend_files():
        src = _read(f)
        for m in re.finditer(r"type\s+(\w+)\s*=\s*([^;\n]+)", src):
            alias_defs[m.group(1)] = set(re.findall(r"'([^']+)'", m.group(2)))
        for m in re.finditer(r"protocol\??:\s*'([^']+)'", src):
            fe_types.add(m.group(1))
        # protocol 指向具名别名时，取别名的成员
        for m in re.finditer(r"protocol\??:\s*(\w+)", src):
            name = m.group(1)
            if name in alias_defs:
                fe_types |= alias_defs[name]

    # 后端真实支持的协议：endpoint() 里分别拼了哪几种 URL。
    # 注意要读「上游协议」那几行，别把入站协议判断也算进来。
    pool_src = _read(os.path.join(ROOT, "backend", "upstream_pool.py"))
    ep = re.search(r"def endpoint\(.*?(?=\n    # ---|\n    def )", pool_src, re.S)
    ep_src = ep.group(0) if ep else pool_src
    be_supported: set[str] = set()
    for m in re.finditer(r'up\.protocol == "([a-z]+)"', ep_src):
        be_supported.add(m.group(1))
    be_supported.add("openai")  # else 分支：默认 chat/completions

    check("找到前端协议下拉块", pm is not None, "未找到 name=\"protocol\" 的 Form.Item")
    check("前端协议下拉选项 = 后端支持协议",
          fe_options == be_supported,
          f"前端: {sorted(fe_options)} 后端: {sorted(be_supported)}")
    check("前端类型声明覆盖后端支持的协议",
          be_supported <= fe_types,
          f"后端: {sorted(be_supported)} 前端类型: {sorted(fe_types)}")


def test_backend_protocol_literal_is_implemented():
    """config.py 声明的协议取值必须都有真实实现。

    只在 Literal 里声明、但 endpoint() 没有对应分支 = 静默走错端点：
    用户以为在用某个协议，实际请求打到了默认的 /chat/completions。
    """
    config_src = _read(os.path.join(ROOT, "backend", "config.py"))
    lit = re.search(r"Protocol\s*=\s*Literal\[([^\]]+)\]", config_src)
    declared = set(re.findall(r'"([a-z]+)"', lit.group(1))) if lit else set()

    pool_src = _read(os.path.join(ROOT, "backend", "upstream_pool.py"))
    ep = re.search(r"def endpoint\(.*?(?=\n    # ---|\n    def )", pool_src, re.S)
    ep_src = ep.group(0) if ep else pool_src
    implemented = set(re.findall(r'up\.protocol == "([a-z]+)"', ep_src))
    implemented.add("openai")  # else 分支

    unimplemented = declared - implemented
    check("Protocol 字面量的每个取值都有实现",
          not unimplemented,
          "未实现: " + ", ".join(sorted(unimplemented)))


# ---------------------------------------------------------------- 4. 视图 ↔ 路由


def test_views_aligned():
    """App.tsx 的每个 ViewKey 都要有菜单项、有对应组件文件，且能渲染。"""
    app_src = _read(os.path.join(FRONTEND_SRC, "App.tsx"))

    # ViewKey 联合类型
    m = re.search(r"type ViewKey\s*=\s*([^=]+?)\n", app_src)
    view_keys = set()
    if m:
        view_keys = set(re.findall(r"'([a-z]+)'", m.group(1)))
    check("ViewKey 解析成功", bool(view_keys), str(sorted(view_keys)))

    # MENU_ITEMS 里的 key
    menu_block = app_src[app_src.find("const MENU_ITEMS"):app_src.find("function viewFromHash")]
    menu_keys = set(re.findall(r"key: '([a-z]+)'", menu_block))
    check("每个 ViewKey 都有菜单项", view_keys <= menu_keys,
          "缺菜单: " + ", ".join(sorted(view_keys - menu_keys)))
    check("没有多余的菜单项", menu_keys <= view_keys,
          "多余: " + ", ".join(sorted(menu_keys - view_keys)))

    # 渲染处：{view === 'xxx' && <Comp />} —— 直接从渲染语句取组件名，
    # 不要靠「key 与文件名相似」去猜（optimize → TokenOpt 这种会误判）。
    rendered = dict(re.findall(r"\{view === '([a-z]+)' && <(\w+)", app_src))

    # import <Comp> from './views/<File>'
    imports = dict(re.findall(r"import (\w+) from '\./views/(\w+)'", app_src))

    for key in sorted(view_keys):
        comp = rendered.get(key)
        if not comp:
            check(f"视图 {key} 在 App 中渲染", False, "未找到渲染语句")
            continue
        fname = imports.get(comp)
        if not fname:
            check(f"视图 {key} 组件已导入", False, f"<{comp}> 未 import")
            continue
        fpath = os.path.join(FRONTEND_SRC, "views", f"{fname}.tsx")
        check(f"视图 {key} 组件文件存在", os.path.isfile(fpath), fpath)
        check(f"视图 {key} 组件已导入", True)

    # 反向：有 import 的视图组件都要被渲染（否则是死视图）
    used = set(rendered.values())
    dead = sorted(set(imports) - used)
    check("没有未被渲染的视图组件", not dead, "死视图: " + ", ".join(dead))

    # VIEW_META 必须覆盖所有 ViewKey（否则标题会是 undefined）
    meta_block = app_src[app_src.find("const VIEW_META"):app_src.find("const MENU_ITEMS")]
    meta_keys = set(re.findall(r"^\s{2}(\w+):\s*\{", meta_block, re.M))
    check("VIEW_META 覆盖所有视图", view_keys <= meta_keys,
          "缺: " + ", ".join(sorted(view_keys - meta_keys)))


# ---------------------------------------------------------------- 5. 代理面


def test_proxy_routes_unchanged_shape():
    """代理面入口路由的形状（防止改协议时把入口改没了）。"""
    paths = backend_proxy_paths()
    for p in ("/v1/messages", "/v1/chat/completions", "/v1/responses", "/v1/models"):
        check(f"代理入口 {p} 存在", p in paths, str(sorted(paths)))


def main() -> int:
    print("前端 ↔ 后端契约测试")
    test_frontend_paths_exist_in_backend()
    test_backend_paths_used_by_frontend()
    test_protocol_values_match()
    test_views_aligned()
    test_proxy_routes_unchanged_shape()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("失败项：")
        for name, _ok, detail in failed:
            print(f"  - {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
