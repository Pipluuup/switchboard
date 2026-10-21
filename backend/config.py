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
from typing import Any, Literal

from pydantic import BaseModel, Field

from .paths import data_dir

DATA_DIR = data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

Protocol = Literal["anthropic", "openai", "responses", "gemini"]


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
        }


class ModelMapping(BaseModel):
    """请求模型名 -> 上游实际模型名（可绑定指定上游）。"""

    id: str = Field(default_factory=lambda: new_id("mm_"))
    alias: str = ""
    target: str = ""
    upstream_id: str = ""
    enabled: bool = True
    note: str = ""


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
    # 缓存优先时仍注入 Say less（默认否：改写 system 会破坏缓存前缀）
    voice_ignore_cache: bool = False
    # Say less（voice）：注入「电报体回复」规则到 system，减少输出 token
    voice_enabled: bool = False


class ClientProfile(BaseModel):
    """客户端接入档位：一键把本地客户端指向本服务。"""
    id: str = Field(default_factory=lambda: new_id("cp_"))
    name: str = "新档位"
    client: str = "claude"        # claude / codex / gemini
    client_key_id: str = ""       # 用哪个客户端 Key
    model: str = ""               # 映射到网关的哪个模型（写进客户端的模型配置）
    enabled: bool = True


class Config(BaseModel):
    version: int = 1
    upstreams: list[Upstream] = Field(default_factory=list)
    mappings: list[ModelMapping] = Field(default_factory=list)
    client_keys: list[ClientKey] = Field(default_factory=list)
    client_profiles: list[ClientProfile] = Field(default_factory=list)
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
                cfg = Config.model_validate(raw)
                # 运行期状态不继承旧值
                for up in cfg.upstreams:
                    up.healthy = True
                    up.consecutive_failures = 0
                    up.cooldown_until = 0.0
                return cfg
            except Exception as e:  # noqa: BLE001
                backup = self.path + f".broken.{int(time.time())}"
                try:
                    os.replace(self.path, backup)
                except OSError:
                    pass
                print(f"[config] 配置文件解析失败，已备份为 {backup}: {e}")
        return self._default_config()

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

    def resolve_model(self, model: str) -> tuple[str, str]:
        """返回 (实际模型名, 指定上游 id 或空)。无映射则原样返回。"""
        with self._lock:
            for m in self.config.mappings:
                if m.enabled and m.alias and m.alias == model:
                    return (m.target or model, m.upstream_id)
        return (model, "")

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

    # ---------- settings ----------
    def update_settings(self, data: dict[str, Any]) -> Settings:
        with self._lock:
            for k, v in data.items():
                if k in Settings.model_fields:
                    setattr(self.config.settings, k, v)
            self.save()
            return self.config.settings


store = ConfigStore()
