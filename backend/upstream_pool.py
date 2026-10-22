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
        # 熔断半开期：正在试探的上游 id（防止并发同时涌向刚恢复的上游）
        self._half_open_inflight: set[str] = set()

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
        # 熔断中的上游直接排除；冷却到期的转半开，且只放行一个试探请求
        ups = [u for u in ups if self._circuit_admits(u, now)]
        ups.sort(key=lambda u: (u.priority, -u.weight))
        return ups

    def _circuit_admits(self, up: Upstream, now: float) -> bool:
        """熔断器是否放行这个上游。"""
        if not store.config.settings.circuit_enabled:
            return True
        if up.circuit_state == "open":
            if now < up.circuit_opened_at + store.config.settings.circuit_open_seconds:
                return False
            # 冷却到期 → 半开，只放行一个试探请求
            up.circuit_state = "half_open"
            up.circuit_half_open_success = 0
        if up.circuit_state == "half_open":
            # 已有试探在途就不再放行，避免并发同时涌向刚恢复的上游
            if up.id in self._half_open_inflight:
                return False
            self._half_open_inflight.add(up.id)
            return True
        return True

    def pick(self, model: str = "", upstream_id: str = "") -> Upstream | None:
        ups = self.candidates(model, upstream_id)
        return ups[0] if ups else None

    # ---------------- 结果反馈 ----------------
    def report_success(self, up: Upstream, latency_ms: int) -> None:
        """请求成功。**不清零失败计数**——这是熔断器能识别「时好时坏」的关键。"""
        st = store.config.settings
        up.total_requests += 1
        up.last_latency_ms = latency_ms
        now = time.time()
        if st.circuit_enabled:
            up.circuit_events.append((now, False))
            self._trim_events(up, now)
            if up.circuit_state == "half_open":
                # 半开期的成功：连续够多次才算真恢复
                up.circuit_half_open_success += 1
                if up.circuit_half_open_success >= max(1, st.circuit_half_open_success):
                    self._close_circuit(up)
                else:
                    up.healthy = True
                    up.cooldown_until = 0.0
                self._half_open_inflight.discard(up.id)
                return
            if up.circuit_state == "open":
                # 熔断期内居然成功了（比如是别的并发请求）：
                # 不改状态，但要记下来——恢复判定只认半开期的连续成功
                up.last_error = ""
                self._half_open_inflight.discard(up.id)
                return
            # closed：正常路径，清零连续失败
            up.consecutive_failures = 0
            up.cooldown_until = 0.0
            up.healthy = True
            up.last_error = ""
            return
        # 熔断关闭：沿用旧行为
        up.consecutive_failures = 0
        up.cooldown_until = 0.0
        up.healthy = True
        up.last_error = ""

    def report_failure(self, up: Upstream, error: str,
                       status_code: int = 0) -> None:
        """请求失败。`status_code` 用来区分「上游的错」与「客户端的错」。

        - 401/403：Key 失效，重试无意义 → 立即熔断
        - 400/404/422：客户端请求本身有问题，上游是好的 → **不计入**熔断
        - 429：限流 → 计入，但冷却更久
        - 5xx / 网络异常：上游真出问题了 → 正常计入
        """
        st = store.config.settings
        up.total_requests += 1
        up.failed_requests += 1
        up.last_error = error[:500]
        now = time.time()

        # 鉴权失败：Key 失效或被拒，重试无意义 → 立即拉黑
        if status_code in (401, 403):
            self._half_open_inflight.discard(up.id)
            if st.circuit_enabled:
                up.circuit_events.append((now, True))
                self._trim_events(up, now)
                self._open_circuit(up, now)
            else:
                up.healthy = False
                up.cooldown_until = now + st.cooldown_seconds
            return

        # 客户端错误：上游是好的，不该被熔断。
        # 但仍要释放半开试探的「在途」标记——否则该上游会被永久排除。
        if status_code and status_code < 500 and status_code != 429:
            self._half_open_inflight.discard(up.id)
            return

        up.consecutive_failures += 1
        if st.circuit_enabled:
            up.circuit_events.append((now, True))
            self._trim_events(up, now)
            self._half_open_inflight.discard(up.id)

            if up.circuit_state == "half_open":
                # 试探失败 → 回到 open，重新计时
                self._open_circuit(up, now)
                return

            # 判定一：连续失败达阈值
            if up.consecutive_failures >= max(1, st.failure_threshold):
                self._open_circuit(up, now)
                return
            # 判定二：窗口内错误率超阈值（样本数不足则不看）
            if self._error_rate(up) > st.circuit_error_rate_threshold:
                self._open_circuit(up, now)
                return
            return

        # 熔断关闭：沿用旧行为
        threshold = max(1, st.failure_threshold)
        if up.consecutive_failures >= threshold:
            up.healthy = False
            up.cooldown_until = now + st.cooldown_seconds

    # ---- 熔断器内部工具 ----
    def _trim_events(self, up: Upstream, now: float) -> None:
        """裁掉统计窗口外的事件。"""
        window = max(1, store.config.settings.circuit_window_seconds)
        up.circuit_events = [(t, ok) for t, ok in up.circuit_events if now - t <= window]

    def _error_rate(self, up: Upstream) -> float:
        """窗口内错误率。样本数不足最小样本量时返回 0（不判定）。

        `circuit_events` 存的是 (时间戳, 是否失败)。
        """
        st = store.config.settings
        if len(up.circuit_events) < max(1, st.circuit_min_requests):
            return 0.0
        fails = sum(1 for _t, failed in up.circuit_events if failed)
        return fails / len(up.circuit_events)

    def _open_circuit(self, up: Upstream, now: float) -> None:
        """打开熔断。"""
        up.circuit_state = "open"
        up.circuit_opened_at = now
        up.circuit_half_open_success = 0
        up.healthy = False
        up.cooldown_until = now + store.config.settings.circuit_open_seconds

    def _close_circuit(self, up: Upstream) -> None:
        """关闭熔断，恢复正常。"""
        up.circuit_state = "closed"
        up.circuit_opened_at = 0.0
        up.circuit_half_open_success = 0
        up.circuit_events = []
        up.consecutive_failures = 0
        up.cooldown_until = 0.0
        up.healthy = True

    def report_health_ok(self, up: Upstream, latency_ms: int) -> None:
        """健康检查探活成功。

        **不动熔断状态**——`/models` 探活成功不代表真实请求成功
        （限流中的上游 `/models` 往往正常返回）。若这里调 report_success，
        熔断器会被「探活成功」误判为已恢复。
        """
        up.last_latency_ms = latency_ms

    def reset_state(self, uid: str = "") -> None:
        for u in store.list_upstreams():
            if not uid or u.id == uid:
                u.healthy = True
                u.consecutive_failures = 0
                u.cooldown_until = 0.0
                u.last_error = ""
                # 熔断状态一并重置
                u.circuit_state = "closed"
                u.circuit_opened_at = 0.0
                u.circuit_half_open_success = 0
                u.circuit_events = []
        self._half_open_inflight.clear()

    # ---------------- 请求构造 ----------------
    def endpoint(self, up: Upstream, in_protocol: str,
                 session_id: str | None = None, model: str = "",
                 stream: bool = False) -> tuple[str, dict[str, str]]:
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
        elif up.protocol == "responses":
            url = f"{base}/responses"
        elif up.protocol == "gemini":
            # Gemini Native generateContent：模型名在 URL 里，不在请求体里；
            # 官方鉴权头是 x-goog-api-key（Authorization 对某些代理也有用，保留）
            if (up.api_key or "").strip():
                headers["x-goog-api-key"] = up.api_key
            act = "streamGenerateContent?alt=sse" if stream else "generateContent"
            url = f"{base}/models/{model}:{act}"
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
            if up.protocol == "gemini":
                headers["x-goog-api-key"] = up.api_key
        if up.protocol == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        t0 = time.time()
        try:
            r = await self.get_client(up).get(url, headers=headers, timeout=15.0)
            cost = int((time.time() - t0) * 1000)
            ok = r.status_code < 500
            if ok:
                # 探活成功不等于真实请求成功——不动熔断状态
                self.report_health_ok(up, cost)
                return {"ok": True, "status": r.status_code, "latency_ms": cost}
            self.report_failure(up, f"HTTP {r.status_code}: {r.text[:200]}",
                                status_code=r.status_code)
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
        model: str = "",
    ) -> httpx.Response:
        """向上游发起请求（不读取 body，交由调用方流式消费）。"""
        model = str(payload.get("model") or model or "")
        url, headers = self.endpoint(up, "", session_id=session_id,
                                     model=model, stream=stream)
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
        payload: dict[str, Any]
        if up.protocol == "gemini":
            payload = {
                "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                "generationConfig": {"maxOutputTokens": 16},
            }
        else:
            payload = {"model": model, "max_tokens": 16,
                       "messages": [{"role": "user", "content": "hi"}]}
        t0 = time.time()
        try:
            url, headers = self.endpoint(up, "", session_id=f"ccp-test-{up.id[:8]}",
                                         model=model, stream=False)
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
            if up.protocol == "gemini":
                headers["x-goog-api-key"] = up.api_key
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
                    mid = str(it.get("id") or it.get("name") or "")
                    if up.protocol == "gemini":
                        # gemini 列表给的是 "models/gemini-2.5-flash"，剥掉前缀
                        mid = mid.removeprefix("models/")
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
