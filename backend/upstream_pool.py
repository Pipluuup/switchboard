"""上游节点池：优先级调度、健康状态、故障冷却与自动切换。"""
from __future__ import annotations

import contextlib
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import Upstream, store
from .database import db

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "[::1]"}

# OpenCode Go 要求：客户端用自有 UA（不能是 python-httpx 这类通用库名），
# 并在 x-opencode-session 里携带稳定会话 ID 用于路由与提示缓存（docs/console/go）。
OPENCODE_UA = "Switchboard/1.0"


def _is_opencode(url: str) -> bool:
    return "opencode.ai" in (urlparse(url).netloc.lower())


def _is_loopback(url: str) -> bool:
    """上游地址是否指向本机。本机地址必须绕过系统代理，否则会被 Clash 等
    勾选「代理局域网/本地」的代理软件劫持，导致间歇 404/502。"""
    host = urlparse(url).hostname or ""
    return host in _LOOPBACK_HOSTS


class UpstreamPool:
    """按 (priority 升序, weight 降序) 挑选当前可用的上游。"""

    def __init__(self):
        # 两个连接池：一个尊重系统代理（用于真实公网上游），一个永不走代理
        # （用于 127.0.0.1 / localhost 等本机上游）。
        self._env_client: httpx.AsyncClient | None = None
        self._direct_client: httpx.AsyncClient | None = None
        self._want: bool | None = None

    def _make_client(self, trust_env: bool) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=15.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            follow_redirects=True,
            trust_env=trust_env,
        )

    def get_client(self, up: Upstream) -> httpx.AsyncClient:
        """按「是否本机地址」与「使用系统代理」开关选择连接池。"""
        want = bool(store.config.settings.use_system_proxy)
        if self._want != want:
            # 开关变化：丢弃旧池，按新策略重建
            self._want = want
            for c in (self._env_client, self._direct_client):
                if c is not None and not c.is_closed:
                    try:
                        c.close()
                    except Exception:  # noqa: BLE001
                        pass
            self._env_client = None
            self._direct_client = None

        if self._direct_client is None:
            self._direct_client = self._make_client(trust_env=False)
        use_env = want and not _is_loopback(up.base_url)
        if use_env:
            if self._env_client is None:
                self._env_client = self._make_client(trust_env=True)
            return self._env_client
        return self._direct_client

    @property
    def client(self) -> httpx.AsyncClient:
        # 便捷属性：默认按「使用系统代理」开关返回一个公网池（保留向后兼容）
        want = bool(store.config.settings.use_system_proxy)
        return self.get_client(Upstream(base_url="http://example.com"))

    # ---------------- 选择 ----------------
    def candidates(self, model: str = "", upstream_id: str = "") -> list[Upstream]:
        ups = store.list_upstreams()
        if upstream_id:
            ups = [u for u in ups if u.id == upstream_id]
        elif model:
            filtered = [u for u in ups if not u.models or model in u.models]
            if filtered:
                ups = filtered
        now = time.time()
        ups = [u for u in ups if u.enabled and u.cooldown_until <= now]
        ups.sort(key=lambda u: (u.priority, -u.weight))
        return ups

    def pick(self, model: str = "", upstream_id: str = "") -> Upstream | None:
        ups = self.candidates(model, upstream_id)
        return ups[0] if ups else None

    # ---------------- 结果反馈 ----------------
    def report_success(self, up: Upstream, latency_ms: int) -> None:
        up.consecutive_failures = 0
        up.cooldown_until = 0.0
        up.healthy = True
        up.last_error = ""
        up.last_latency_ms = latency_ms
        up.total_requests += 1

    def report_failure(self, up: Upstream, error: str) -> None:
        up.consecutive_failures += 1
        up.failed_requests += 1
        up.total_requests += 1
        up.last_error = error[:500]
        threshold = max(1, store.config.settings.failure_threshold)
        if up.consecutive_failures >= threshold:
            up.healthy = False
            up.cooldown_until = time.time() + store.config.settings.cooldown_seconds

    def reset_state(self, uid: str = "") -> None:
        for u in store.list_upstreams():
            if not uid or u.id == uid:
                u.healthy = True
                u.consecutive_failures = 0
                u.cooldown_until = 0.0
                u.last_error = ""

    # ---------------- 请求构造 ----------------
    def endpoint(self, up: Upstream, in_protocol: str,
                 session_id: str | None = None) -> tuple[str, dict[str, str]]:
        base = up.base_url.rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
        }
        # Key 为空时不带 Authorization：让上游返回明确的 401「未提供令牌」，
        # 而不是 httpx 因非法头值直接抛 LocalProtocolError
        if (up.api_key or "").strip():
            headers["Authorization"] = f"Bearer {up.api_key}"
        if up.protocol == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
            url = f"{base}/messages"
        else:
            url = f"{base}/chat/completions"
        if _is_opencode(base):
            headers["User-Agent"] = OPENCODE_UA
            headers["x-opencode-session"] = session_id or f"ccp-{up.id[:8]}"
        return url, headers

    # ---------------- 健康检查 ----------------
    async def health_check(self, up: Upstream) -> dict[str, Any]:
        base = up.base_url.rstrip("/")
        url = f"{base}/models"
        headers = {}
        if (up.api_key or "").strip():
            headers["Authorization"] = f"Bearer {up.api_key}"
        if up.protocol == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        t0 = time.time()
        try:
            r = await self.get_client(up).get(url, headers=headers, timeout=15.0)
            cost = int((time.time() - t0) * 1000)
            ok = r.status_code < 500
            if ok:
                self.report_success(up, cost)
                return {"ok": True, "status": r.status_code, "latency_ms": cost}
            self.report_failure(up, f"HTTP {r.status_code}: {r.text[:200]}")
            return {"ok": False, "status": r.status_code, "latency_ms": cost,
                    "error": r.text[:200]}
        except Exception as e:  # noqa: BLE001
            cost = int((time.time() - t0) * 1000)
            self.report_failure(up, f"{type(e).__name__}: {e}")
            return {"ok": False, "status": 0, "latency_ms": cost, "error": str(e)[:300]}

    async def health_check_all(self) -> list[dict[str, Any]]:
        out = []
        for up in store.list_upstreams():
            if not up.enabled:
                out.append({"id": up.id, "name": up.name, "ok": None, "skipped": True})
                continue
            res = await self.health_check(up)
            res.update({"id": up.id, "name": up.name})
            out.append(res)
        return out

    # ---------------- 真实调用 ----------------
    async def call(
        self,
        up: Upstream,
        payload: dict,
        stream: bool,
        session_id: str | None = None,
    ) -> httpx.Response:
        """向上游发起请求（不读取 body，交由调用方流式消费）。"""
        url, headers = self.endpoint(up, "", session_id=session_id)
        timeout = up.timeout or store.config.settings.request_timeout
        cl = self.get_client(up)  # 与 build_request 必须用同一个连接池
        req = cl.build_request(
            "POST", url, headers=headers, json=payload,
            timeout=httpx.Timeout(timeout, connect=15.0),
        )
        return await cl.send(req, stream=True)

    async def test_chat(self, up: Upstream, model: str) -> dict[str, Any]:
        """连通性测试：发一条极短消息，验证 Key 与协议是否正确。"""
        if not (up.api_key or "").strip():
            return {"ok": False, "status": 0, "latency_ms": 0,
                    "error": "该上游未配置 API Key，请先点「编辑」填入 Key 再测试"}
        payload: dict[str, Any] = (
            {"model": model, "max_tokens": 16,
             "messages": [{"role": "user", "content": "hi"}]}
            if up.protocol == "anthropic"
            else {"model": model, "max_tokens": 16,
                  "messages": [{"role": "user", "content": "hi"}]}
        )
        t0 = time.time()
        try:
            url, headers = self.endpoint(up, "", session_id=f"ccp-test-{up.id[:8]}")
            r = await self.get_client(up).post(
                url, headers=headers, json=payload,
                timeout=httpx.Timeout(min(60, up.timeout or 60), connect=15.0),
            )
            cost = int((time.time() - t0) * 1000)
            if r.status_code < 400:
                self.report_success(up, cost)
                body = r.text[:600]
                return {"ok": True, "status": r.status_code, "latency_ms": cost, "preview": body}
            self.report_failure(up, f"HTTP {r.status_code}: {r.text[:300]}")
            return {"ok": False, "status": r.status_code, "latency_ms": cost,
                    "error": r.text[:400]}
        except Exception as e:  # noqa: BLE001
            cost = int((time.time() - t0) * 1000)
            self.report_failure(up, f"{type(e).__name__}: {e}")
            return {"ok": False, "status": 0, "latency_ms": cost,
                    "error": f"{type(e).__name__}: {e}"}

    async def fetch_models(self, up: Upstream) -> list[str]:
        base = up.base_url.rstrip("/")
        headers = {}
        if (up.api_key or "").strip():
            headers["Authorization"] = f"Bearer {up.api_key}"
        if up.protocol == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        try:
            r = await self.get_client(up).get(f"{base}/models", headers=headers, timeout=20.0)
            if r.status_code >= 400:
                return []
            data = r.json()
            items = data.get("data") or data.get("models") or []
            names = []
            for it in items:
                if isinstance(it, dict):
                    mid = it.get("id") or it.get("name")
                    if mid:
                        names.append(mid)
                elif isinstance(it, str):
                    names.append(it)
            return sorted(set(names))
        except Exception:  # noqa: BLE001
            return []

    async def aclose(self) -> None:
        """关闭所有连接池。"""
        self._want = None
        for c in (self._env_client, self._direct_client):
            if c is not None and not c.is_closed:
                with contextlib.suppress(Exception):
                    await c.aclose()
        self._env_client = None
        self._direct_client = None


pool = UpstreamPool()
