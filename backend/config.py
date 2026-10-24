"""配置模型与持久化。

配置以 JSON 文件存放（便于备份 / 手工编辑），请求日志走 SQLite。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import uuid
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

from .paths import data_dir

DATA_DIR = data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

Protocol = Literal["anthropic", "openai", "responses", "gemini"]

# Say less（voice）三态：off=不注入 / on=总是注入 / auto=按条件自动判定
VoiceMode = Literal["off", "on", "auto"]
VOICE_MODES = ("off", "on", "auto")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class Upstream(BaseModel):
    """一个上游服务节点。"""

    id: str = Field(default_factory=lambda: new_id("up_"))
    name: str = "新上游"
    base_url: str = "https://api.commandcode.dev/v1"
    api_key: str = ""
    protocol: Protocol = "anthropic"
    enabled: bool = True
    priority: int = 100
    weight: int = 1
    timeout: int = 180
    max_retries: int = 2
    models: list[str] = Field(default_factory=list)
    note: str = ""
    key_url: str = ""  # 获取 / 申请 API Key 的网址（模板分享用，不含 Key 本身）

    # ---- 运行期状态（不持久化到 config.json） ----
    healthy: bool = True
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""
    last_latency_ms: int = 0
    total_requests: int = 0
    failed_requests: int = 0

    # ---- 熔断器状态（同样不持久化） ----
    # closed：正常放行 / open：已拉黑 / half_open：冷却到期后的试探期
    circuit_state: str = "closed"
    circuit_opened_at: float = 0.0
    circuit_half_open_success: int = 0
    # 错误率统计窗口内的时间戳列表（True=失败 False=成功）
    circuit_events: list[tuple[float, bool]] = Field(default_factory=list)

    def runtime_state(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "in_cooldown": self.cooldown_until > time.time(),
            "cooldown_remaining": max(0, int(self.cooldown_until - time.time())),
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_latency_ms": self.last_latency_ms,
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
            "circuit_state": self.circuit_state,
            "circuit_remaining": self.circuit_remaining(),
            "circuit_probe_ready": self.circuit_probe_ready(),
        }

    def circuit_remaining(self) -> int:
        """熔断还要多久才进入半开（秒）。非 open 状态返回 0。

        冷却已到期但还没人发起试探时返回 0——此时熔断**已可被试探**，
        只是还没有请求去触发（状态转换发生在 candidates() 筛选时）。

        `store` 是本模块的全局变量，方法体在调用时才求值——
        那时模块早已加载完毕，不会踩到循环导入。
        """
        if self.circuit_state != "open":
            return 0
        secs = store.config.settings.circuit_open_seconds
        return max(0, int(self.circuit_opened_at + secs - time.time()))

    def circuit_probe_ready(self) -> bool:
        """熔断已到期、下一个请求可以试探（但还没试探过）。"""
        return self.circuit_state == "open" and self.circuit_remaining() <= 0


class ChainHop(BaseModel):
    """故障转移链的一档：上游 + 该上游上的实际模型。

    `upstream_id` 为空表示「按调度策略自动选上游」，此时 `target`
    仍是发给上游的模型名。
    """

    upstream_id: str = ""
    target: str = ""


class ModelMapping(BaseModel):
    """请求模型名 -> 上游实际模型名（可绑定指定上游）。

    `chain` 是故障转移链：从上到下依次尝试，每档 = (上游, 模型)。
    为空时退化成老语义——用 `target` + `upstream_id` 这一档。
    """

    id: str = Field(default_factory=lambda: new_id("mm_"))
    alias: str = ""
    target: str = ""
    upstream_id: str = ""
    chain: list[ChainHop] = Field(default_factory=list)
    enabled: bool = True
    note: str = ""

    def hops(self) -> list[ChainHop]:
        """把映射解析成有序的故障转移链。

        链为空 → 用 `target`/`upstream_id` 造一档（老配置零迁移）。
        链非空 → 完全以链为准；`target`/`upstream_id` 只作元数据保留。
        链里全是空档（既没上游也没模型）→ 同样退回老字段，
        免得出现「配了链但一档都试不了」的哑配置。
        """
        if self.chain:
            usable = [h for h in self.chain if h.upstream_id or h.target]
            if usable:
                return usable
        return [ChainHop(upstream_id=self.upstream_id, target=self.target)]


class ClientKey(BaseModel):
    """分发给本地客户端（如 Claude Code）的 Key。"""

    id: str = Field(default_factory=lambda: new_id("ck_"))
    name: str = "新客户端"
    key: str = Field(default_factory=lambda: "sk-px-" + secrets.token_hex(16))
    enabled: bool = True
    rate_limit_rpm: int = 0  # 0 = 不限
    daily_token_limit: int = 0  # 0 = 不限
    allowed_models: list[str] = Field(default_factory=list)  # 空 = 全部
    upstream_id: str = ""  # 空 = 走公共调度
    note: str = ""
    created_at: float = Field(default_factory=time.time)
    last_used_at: float = 0.0
    total_requests: int = 0

    def masked(self) -> str:
        k = self.key
        if len(k) <= 12:
            return k
        return f"{k[:10]}{'*' * 6}{k[-4:]}"


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    admin_token: str = ""  # 空 = 不启用管理鉴权
    log_retention_days: int = 7
    enable_log: bool = True
    request_timeout: int = 180
    cooldown_seconds: int = 60
    failure_threshold: int = 3
    health_check_interval: int = 120
    health_check_enabled: bool = True
    # ---- 熔断器 ----
    # 熔断 = 上游异常时把它拉黑一段时间，而不是让每个请求都去撞一次。
    # 连续失败达 failure_threshold 会打开；此外窗口内错误率超阈值也会打开
    # （治「时好时坏」的上游：连续计数会被一次成功清零，永远打不中阈值）。
    circuit_enabled: bool = True
    # 错误率阈值（0~1）：窗口内失败占比超过它就打开熔断
    circuit_error_rate_threshold: float = 0.5
    # 最小样本数：窗口内样本不足这个量时**不**用错误率判定
    # （刚启动的节点 3 次全失败错误率 100%，但毫无统计意义）
    circuit_min_requests: int = 10
    # 熔断打开后多久进入半开（秒）
    circuit_open_seconds: int = 60
    # 半开期连续成功多少次才完全关闭熔断
    circuit_half_open_success: int = 2
    # 错误率统计窗口（秒）
    circuit_window_seconds: int = 120
    auto_open_browser: bool = True
    use_system_proxy: bool = True  # 请求上游时是否尊重 HTTP(S)_PROXY 环境变量
    # OpenCode Console（https://opencode.ai/console）凭证：用于拉取 Go 配额官方数据。
    # cookie 为浏览器里 __Host-console_session 的值（有效期约 30 天，过期需重新复制）。
    opencode_org_id: str = ""  # 工作区 ID，如 wrk_xxxx（Console URL 里 /console/<id>/go）
    opencode_console_cookie: str = ""
    # 火山方舟（console.volcengine.com）编码计划：粘贴控制台请求的完整 Cookie 串
    # （F12 → Network → 任意 api/top/ark 请求 → Request Headers → Cookie 整串）。
    ark_console_cookie: str = ""
    # 用量页 Plan 实时查询配额卡片的分项开关（设置页可关）
    show_usage_opencode: bool = True
    show_usage_ark: bool = True
    # 请求日志是否保存请求体 / 响应体详情（默认关：内容大且含隐私）
    log_payloads: bool = False
    # 详情单侧截断长度（字符），防止长提示词撑爆数据库
    payload_max_bytes: int = 8192
    # Caveman 引擎：转发前对输入做压缩改写以节省上游 token（有损，默认关）
    caveman_enabled: bool = False
    caveman_keep_recent: int = 4  # 最近 N 条消息原样保留，更早的做压缩
    caveman_min_chars: int = 400  # 单块文本超过该字符数才截断
    caveman_compress_system: bool = False  # 是否压缩 system（会降低 prompt cache 命中）
    # 缓存感知：对支持 prompt cache 的上游自动弱化压缩（放大保留窗口、提高阈值），
    # 避免改写可缓存前缀导致命中价失效
    caveman_cache_aware: bool = True
    # Say less（voice）：注入「电报体回复」规则到 system，减少输出 token
    #   off  = 永不注入
    #   on   = 总是注入（不管缓存）
    #   auto = 按请求实际情况自动判定（默认）：满足条件才注入
    voice_mode: VoiceMode = "auto"
    # auto 模式下：请求输入 token 低于该值时不注入（短问答不值得为 400 token 规则买单）
    voice_auto_min_tokens: int = 2000
    # auto 模式下：上游支持 prompt cache 时是否仍注入（默认否——改写 system 会破坏缓存前缀）
    voice_ignore_cache: bool = False
    # auto 模式下：会话消息数达到该值才注入（长会话输出多，规则更划算）
    voice_auto_min_messages: int = 4

    @field_validator("voice_mode", mode="before")
    @classmethod
    def _norm_voice_mode(cls, v: Any) -> str:
        """非法值一律回落到 auto（而不是静默变成「关」）。"""
        return v if v in VOICE_MODES else "auto"


class PooledAccount(BaseModel):
    """账号池里的一枚账号：同一家上游服务的多份凭证之一。

    凭证二选一：`api_key`（标准 API 服务的多 Key 轮换）或
    `cookie`（WorkBuddy 等「登录即授权」的服务）。
    `usage_limit` 为 0 表示未配上限（不受额度约束）。
    """

    id: str = Field(default_factory=lambda: new_id("ac_"))
    label: str = ""  # 展示名（一般为邮箱）
    api_key: str = ""
    cookie: str = ""
    enabled: bool = True  # 参与轮换的开关；401/403 会自动置 False
    usage_used: int = 0  # 本地记的额度用量（可选）
    usage_limit: int = 0  # 0 = 未配上限
    note: str = ""
    last_used_at: float = 0.0
    total_requests: int = 0
    failed_requests: int = 0
    last_error: str = ""

    def masked(self) -> str:
        s = self.api_key or self.cookie
        if not s:
            return ""
        if len(s) <= 12:
            return s
        return f"{s[:6]}{'*' * 6}{s[-4:]}"


class AccountPool(BaseModel):
    """账号池：同一家上游服务的多枚账号，按顺序轮换。

    语义（对齐 CC-Switch 的 WorkBuddy 国际版页）：
    网关优先用第一个账号；前一个的额度用尽/凭证失效后自动换下一个。
    `auto_upstream` 打开时，创建池会同步在「上游管理」里加一个同名的
    池组节点（note 带前缀 `account-pool:<pool_id>`），删除池时保留该节点。
    """

    id: str = Field(default_factory=lambda: new_id("ap_"))
    name: str = "新账号池"
    kind: str = "workbuddy"  # workbuddy / multikey / …（开放枚举，展示用）
    base_url: str = ""  # 池背后的公共上游地址
    protocol: Protocol = "openai"
    notes: str = ""
    auto_upstream: bool = True
    accounts: list[PooledAccount] = Field(default_factory=list)

    # 池组上游节点的 note 标记前缀（上游管理页据此显示池组徽章）
    UPSTREAM_TAG: ClassVar[str] = "account-pool:"

    def upstream_note(self) -> str:
        """给池组上游节点用的 note 标记。"""
        return f"{self.UPSTREAM_TAG}{self.id}"


class ClientProfile(BaseModel):
    """客户端接入档位：一键把本地客户端指向本服务。"""
    id: str = Field(default_factory=lambda: new_id("cp_"))
    name: str = "新档位"
    client: str = "claude"        # claude / codex / gemini
    client_key_id: str = ""       # 用哪个客户端 Key
    model: str = ""               # 默认模型（写进主模型与所有未单独配置的档位）
    # 档位差异化映射（Claude Code 专属）：按 SONNET/HAIKU/OPUS/FABLE 档位
    # 各写不同的网关模型别名。键用小写档位名（非法值忽略），
    # 未配置的档位回落到 model。主模型 ANTHROPIC_MODEL 始终用 model。
    tier_models: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


class Config(BaseModel):
    version: int = 1
    upstreams: list[Upstream] = Field(default_factory=list)
    mappings: list[ModelMapping] = Field(default_factory=list)
    client_keys: list[ClientKey] = Field(default_factory=list)
    client_profiles: list[ClientProfile] = Field(default_factory=list)
    account_pools: list[AccountPool] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)


class ConfigStore:
    """线程安全的配置读写。"""

    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self.config = self._load()

    def _load(self) -> Config:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                migrated = self._migrate_settings(raw.get("settings") or {})
                cfg = Config.model_validate(raw)
                # 运行期状态不继承旧值
                for up in cfg.upstreams:
                    up.healthy = True
                    up.consecutive_failures = 0
                    up.cooldown_until = 0.0
                if migrated:
                    # 把迁移结果落盘，免得每次启动都重新迁移一遍
                    self.config = cfg
                    self.save()
                return cfg
            except Exception as e:  # noqa: BLE001
                backup = self.path + f".broken.{int(time.time())}"
                try:
                    os.replace(self.path, backup)
                except OSError:
                    pass
                print(f"[config] 配置文件解析失败，已备份为 {backup}: {e}")
        return self._default_config()

    @staticmethod
    def _migrate_settings(s: dict[str, Any]) -> bool:
        """把旧版布尔设置迁移到新的三态设置（原地改 raw dict），返回是否改过。

        旧版 Say less 只有 voice_enabled 一个布尔：
        - 旧值 true  → 新模式 "on"（用户明确要开，别擅自改语义）
        - 旧值 false → 新模式 "off"（同上）
        - 没有该键  → 保持默认 "auto"

        旧版另有 voice_ignore_cache（缓存优先时仍注入），语义在新版保留。
        """
        if "voice_mode" in s:
            return False
        if "voice_enabled" not in s:
            return False
        s["voice_mode"] = "on" if s["voice_enabled"] else "off"
        s.pop("voice_enabled", None)
        print(f"[config] Say less 设置已迁移：voice_mode={s['voice_mode']}")
        return True

    def _default_config(self) -> Config:
        cfg = Config()
        # 两个内置免费节点：默认停用（Key 为空，避免健康检查刷 401 进冷却），填 Key 再启用
        cfg.upstreams.append(
            Upstream(
                name="Agnes AI (free)",
                base_url="https://api.agnes-ai.cn/v1",
                api_key="",
                protocol="openai",
                priority=300,
                models=["agnes-2.5-flash", "agnes-2.5-pro", "agnes-2.5-pro-alpha",
                        "agnes-2.5-pro-beta", "agnes-3.0-flash"],
                note="Agnes AI 免费 Omni-Modal 网关（api.agnes-ai.cn），体验金余额制",
                enabled=False,
            )
        )
        cfg.upstreams.append(
            Upstream(
                name="SenseNova (free)",
                base_url="https://token.sensenova.cn/v1",
                api_key="",
                protocol="openai",
                priority=300,
                models=["glm-5.2", "kimi-k3", "deepseek-v4-flash",
                        "sensenova-6.8-flash-lite", "deepseek-flash"],
                note="SenseNova 商汤免费网关（token.sensenova.cn），全 0 计费",
                enabled=False,
            )
        )
        cfg.mappings.append(
            ModelMapping(alias="Free", target="agnes-2.5-flash", note="默认直通（免费通道）")
        )
        cfg.client_keys.append(ClientKey(name="默认Agent", key="sk-px-local-dev"))
        self.config = cfg
        self.save()
        return cfg

    def save(self) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            data = self.config.model_dump(exclude={
                "upstreams": {"__all__": {
                    "healthy", "consecutive_failures", "cooldown_until", "last_error",
                    "last_latency_ms", "total_requests", "failed_requests",
                    "circuit_state", "circuit_opened_at", "circuit_half_open_success",
                    "circuit_events",
                }},
            })
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)

    # ---------- upstreams ----------
    def list_upstreams(self) -> list[Upstream]:
        with self._lock:
            return list(self.config.upstreams)

    def get_upstream(self, uid: str) -> Upstream | None:
        with self._lock:
            for u in self.config.upstreams:
                if u.id == uid:
                    return u
        return None

    def add_upstream(self, data: dict[str, Any]) -> Upstream:
        with self._lock:
            up = Upstream.model_validate(data)
            self.config.upstreams.append(up)
            self.save()
            return up

    def update_upstream(self, uid: str, data: dict[str, Any]) -> Upstream | None:
        with self._lock:
            up = self.get_upstream(uid)
            if not up:
                return None
            # 空字符串 = 前端未重新输入 Key（编辑表单出于安全不回显），保留原值
            data = {k: v for k, v in data.items() if not (k == "api_key" and not str(v or "").strip())}
            for k, v in data.items():
                if k in Upstream.model_fields and k not in {
                    "healthy", "consecutive_failures", "cooldown_until",
                    "last_error", "last_latency_ms", "total_requests", "failed_requests",
                    "circuit_state", "circuit_opened_at", "circuit_half_open_success",
                    "circuit_events",
                }:
                    setattr(up, k, v)
            self.save()
            return up

    def delete_upstream(self, uid: str) -> bool:
        with self._lock:
            before = len(self.config.upstreams)
            self.config.upstreams = [u for u in self.config.upstreams if u.id != uid]
            if len(self.config.upstreams) < before:
                self.save()
                return True
        return False

    # ---------- mappings ----------
    def list_mappings(self) -> list[ModelMapping]:
        with self._lock:
            return list(self.config.mappings)

    def add_mapping(self, data: dict[str, Any]) -> ModelMapping:
        with self._lock:
            m = ModelMapping.model_validate(data)
            self.config.mappings.append(m)
            self.save()
            return m

    def update_mapping(self, mid: str, data: dict[str, Any]) -> ModelMapping | None:
        with self._lock:
            for m in self.config.mappings:
                if m.id == mid:
                    for k, v in data.items():
                        if k in ModelMapping.model_fields:
                            # `chain` 是 list[ChainHop]——直接 setattr 进去的是
                            # 原始 dict，取属性时会炸。必须按 ModelMapping 重新校验，
                            # 让 Pydantic 把每档转成 ChainHop。
                            if k == "chain":
                                v = [ChainHop.model_validate(h) if isinstance(h, dict) else h
                                     for h in (v or [])]
                            setattr(m, k, v)
                    self.save()
                    return m
        return None

    def delete_mapping(self, mid: str) -> bool:
        with self._lock:
            before = len(self.config.mappings)
            self.config.mappings = [m for m in self.config.mappings if m.id != mid]
            if len(self.config.mappings) < before:
                self.save()
                return True
        return False

    def resolve_chain(self, model: str) -> list[ChainHop]:
        """返回别名对应的故障转移链。

        有映射 → 该映射的链（`hops()` 已处理老配置的退化）。
        无映射 → 单档「自动调度 + 原样模型名」，即沿用公共调度。
        """
        with self._lock:
            for m in self.config.mappings:
                if m.enabled and m.alias and m.alias == model:
                    return m.hops()
        return [ChainHop(upstream_id="", target=model)]

    # ---------- client keys ----------
    def list_keys(self) -> list[ClientKey]:
        with self._lock:
            return list(self.config.client_keys)

    # ---------- 客户端接入档位 ----------
    def list_profiles(self) -> list[ClientProfile]:
        with self._lock:
            return list(self.config.client_profiles)

    def get_profile(self, pid: str) -> ClientProfile | None:
        with self._lock:
            for p in self.config.client_profiles:
                if p.id == pid:
                    return p
        return None

    def add_profile(self, data: dict[str, Any]) -> ClientProfile:
        with self._lock:
            data = dict(data)
            data.pop("id", None)
            p = ClientProfile.model_validate(data)
            self.config.client_profiles.append(p)
            self.save()
            return p

    def update_profile(self, pid: str, data: dict[str, Any]) -> ClientProfile | None:
        with self._lock:
            for p in self.config.client_profiles:
                if p.id == pid:
                    for k, v in (data or {}).items():
                        if k in ClientProfile.model_fields and k != "id":
                            setattr(p, k, v)
                    self.save()
                    return p
        return None

    def delete_profile(self, pid: str) -> bool:
        with self._lock:
            before = len(self.config.client_profiles)
            self.config.client_profiles = [p for p in self.config.client_profiles if p.id != pid]
            if len(self.config.client_profiles) == before:
                return False
            self.save()
            return True

    def get_key(self, kid: str) -> ClientKey | None:
        with self._lock:
            for k in self.config.client_keys:
                if k.id == kid:
                    return k
        return None

    def find_key_by_value(self, key: str) -> ClientKey | None:
        with self._lock:
            for k in self.config.client_keys:
                if k.key and k.key == key:
                    return k
        return None

    def add_key(self, data: dict[str, Any]) -> ClientKey:
        with self._lock:
            ck = ClientKey.model_validate(data)
            self.config.client_keys.append(ck)
            self.save()
            return ck

    def update_key(self, kid: str, data: dict[str, Any]) -> ClientKey | None:
        with self._lock:
            ck = self.get_key(kid)
            if not ck:
                return None
            for k, v in data.items():
                if k in ClientKey.model_fields:
                    setattr(ck, k, v)
            self.save()
            return ck

    def delete_key(self, kid: str) -> bool:
        with self._lock:
            before = len(self.config.client_keys)
            self.config.client_keys = [k for k in self.config.client_keys if k.id != kid]
            if len(self.config.client_keys) < before:
                self.save()
                return True
        return False

    # ---------- 账号池 ----------
    def list_pools(self) -> list[AccountPool]:
        with self._lock:
            return list(self.config.account_pools)

    def get_pool(self, pid: str) -> AccountPool | None:
        with self._lock:
            for p in self.config.account_pools:
                if p.id == pid:
                    return p
        return None

    def add_pool(self, data: dict[str, Any]) -> AccountPool:
        with self._lock:
            p = AccountPool.model_validate(data)
            self.config.account_pools.append(p)
            self.save()
            return p

    def update_pool(self, pid: str, data: dict[str, Any]) -> AccountPool | None:
        with self._lock:
            for p in self.config.account_pools:
                if p.id != pid:
                    continue
                for k, v in (data or {}).items():
                    if k not in AccountPool.model_fields or k == "id":
                        continue
                    # accounts 里的 dict 要重新校验成 PooledAccount
                    if k == "accounts":
                        v = [PooledAccount.model_validate(a) if isinstance(a, dict) else a
                             for a in (v or [])]
                    setattr(p, k, v)
                self.save()
                return p
        return None

    def delete_pool(self, pid: str) -> bool:
        with self._lock:
            before = len(self.config.account_pools)
            self.config.account_pools = [p for p in self.config.account_pools if p.id != pid]
            if len(self.config.account_pools) == before:
                return False
            self.save()
            return True

    # ---------- settings ----------
    def update_settings(self, data: dict[str, Any]) -> Settings:
        with self._lock:
            # 旧布尔字段不再受理：前端已改三态，写进来只会污染配置
            data = {k: v for k, v in data.items() if k != "voice_enabled"}
            for k, v in data.items():
                if k in Settings.model_fields:
                    setattr(self.config.settings, k, v)
            self.save()
            return self.config.settings


store = ConfigStore()
