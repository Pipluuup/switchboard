/** 全部 API 载荷类型（与 backend/admin_routes.py 的响应对应） */

export interface OverviewStats {
  total_requests: number
  requests_24h: number
  success_rate: number
  failed_requests: number
  total_input_tokens: number
  total_output_tokens: number
  avg_latency_ms: number
  by_upstream: { upstream_name: string; n: number; ok_n: number }[]
  by_model: { request_model: string; n: number; tin: number; tout: number }[]
}

export interface TimelineBucket {
  bucket: number
  n: number
  ok_n: number
}

export interface Overview {
  stats: OverviewStats
  timeline: TimelineBucket[]
  upstream_count: number
  enabled_upstream_count: number
  mapping_count: number
  key_count: number
}

export interface UsageWindow {
  key: '5h' | '7d' | '30d'
  label: string
  requests: number
  ok_requests: number
  failed_requests: number
  success_rate: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  /** 缓存命中 token（input 不含这部分） */
  cache_read_tokens?: number
  /** 缓存写入 token */
  cache_write_tokens?: number
  avg_latency_ms: number
  last_ts: number | null
  by_model: { model: string; n: number; tin: number; tout: number }[]
  /** 数据来源：rollup=日聚合表 / detail=明细表 */
  source?: 'rollup' | 'detail'
}

export interface Usage {
  windows: UsageWindow[]
  retention_days: number
}

/** 上游协议：与后端 backend/config.py 的 Protocol 字面量保持一致 */
export type UpstreamProtocol = 'anthropic' | 'openai' | 'responses' | 'gemini'

export interface Upstream {
  id: string
  name: string
  protocol: UpstreamProtocol
  base_url: string
  api_key_masked: string
  priority: number
  weight: number
  timeout: number
  max_retries: number
  models: string[]
  note: string
  key_url?: string
  enabled: boolean
  healthy: boolean
  in_cooldown: boolean
  total_requests: number
  failed_requests: number
  last_latency_ms: number | null
  last_error: string
  /** 熔断器状态：closed=正常 / open=已拉黑 / half_open=冷却到期后的试探期 */
  circuit_state?: 'closed' | 'open' | 'half_open'
  /** 熔断还要多久进入半开（秒） */
  circuit_remaining?: number
  /** 熔断已到期、下一个请求可以试探（但还没试探过） */
  circuit_probe_ready?: boolean
}

export interface UpstreamInput {
  name: string
  protocol: UpstreamProtocol
  base_url: string
  api_key: string
  priority: number
  weight: number
  timeout: number
  max_retries: number
  models: string[]
  note: string
  key_url?: string
  enabled: boolean
}

export interface Mapping {
  id: string
  alias: string
  target: string
  upstream_id: string | null
  upstream_name: string | null
  note: string
  enabled: boolean
}

export interface MappingInput {
  alias: string
  target: string
  upstream_id: string
  note: string
  enabled: boolean
}

export interface ClientKey {
  id: string
  name: string
  key: string
  masked: string
  enabled: boolean
  rate_limit_rpm: number
  daily_token_limit: number
  used_today: number
  allowed_models: string[]
  upstream_id: string | null
  note: string
  total_requests: number
  last_used_at: number | null
}

export interface ClientKeyInput {
  name: string
  rate_limit_rpm: number
  daily_token_limit: number
  allowed_models: string[]
  upstream_id: string
  note: string
  enabled: boolean
}

export interface LogItem {
  id: number
  ts: number
  ok: boolean
  request_model: string
  actual_model: string
  upstream_name: string
  client_name: string
  out_protocol: string
  input_tokens: number
  output_tokens: number
  latency_ms: number
  error: string
  caveman_saved?: number
  voice_injected?: number
  caveman_stats?: string
}

/** Say less 三态模式 */
export type VoiceMode = 'off' | 'on' | 'auto'

/** Say less 本次请求的决策结果（caveman_stats.voice） */
export interface VoiceDecision {
  inject: boolean
  mode: VoiceMode
  reason: string
  in_tokens: number
  messages: number
  cache_aware: boolean
}

/** 压缩统计（caveman_stats 字段解析后） */
export interface CavemanStats {
  orig_chars: number
  new_chars: number
  saved_chars: number
  saved_pct: number
  /** 压缩后、注入规则前的体积（老日志没有这个字段） */
  compressed_chars?: number
  /** 注入 Say less 规则带来的额外字符数（老日志没有这个字段） */
  voice_added_chars?: number
  mode: 'normal' | 'cache-aware'
  capabilities: string[]
  voice?: VoiceDecision
}

export interface LogDetail extends LogItem {
  request_body: string
  response_body: string
}

export interface LogPage {
  items: LogItem[]
  total: number
}

export interface Settings {
  host: string
  port: number
  admin_token: string
  request_timeout: number
  failure_threshold: number
  cooldown_seconds: number
  /** 熔断器开关 */
  circuit_enabled?: boolean
  /** 窗口内错误率超此值就熔断（0~1） */
  circuit_error_rate_threshold?: number
  /** 最小样本数：样本不足时不按错误率判定 */
  circuit_min_requests?: number
  /** 熔断持续秒数，到期进入半开试探 */
  circuit_open_seconds?: number
  /** 试探期需连续成功几次才恢复 */
  circuit_half_open_success?: number
  /** 错误率统计窗口（秒） */
  circuit_window_seconds?: number
  health_check_interval: number
  log_retention_days: number
  health_check_enabled: boolean
  enable_log: boolean
  auto_open_browser: boolean
  opencode_org_id: string
  opencode_console_cookie: string
  ark_console_cookie: string
  show_usage_opencode: boolean
  show_usage_ark: boolean
  log_payloads: boolean
  payload_max_bytes: number
  caveman_enabled: boolean
  caveman_keep_recent: number
  caveman_min_chars: number
  caveman_compress_system: boolean
  caveman_cache_aware?: boolean
  voice_mode: VoiceMode
  voice_auto_min_tokens: number
  voice_auto_min_messages: number
  voice_ignore_cache?: boolean
}

export interface Quickstart {
  base_url: string
  api_key: string
  snippets: { env: string; powershell: string; ccswitch: string }
}

/** 客户端接入状态 */
export interface ClientSetup {
  client: string
  label: string
  config_path: string
  exists: boolean
  hint: string
  base_url: string
  model: string
  attached: boolean
  has_backup: boolean
  error?: string
}

/** 客户端接入档位 */
export interface ClientProfile {
  id: string
  name: string
  client: string
  client_key_id: string
  client_key_name?: string
  client_key_masked?: string
  model: string
  enabled: boolean
}

export interface ConfigExport {
  upstreams: unknown[]
  mappings: unknown[]
  keys: unknown[]
  [k: string]: unknown
}

export interface TestResult {
  ok: boolean
  latency_ms?: number
  model?: string
  status?: number
  error?: string
  preview?: string
}
