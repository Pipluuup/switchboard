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
  avg_latency_ms: number
  last_ts: number | null
  by_model: { model: string; n: number; tin: number; tout: number }[]
}

export interface Usage {
  windows: UsageWindow[]
  retention_days: number
}

export interface Upstream {
  id: string
  name: string
  protocol: 'anthropic' | 'openai'
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
}

export interface UpstreamInput {
  name: string
  protocol: string
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

/** 压缩统计（caveman_stats 字段解析后） */
export interface CavemanStats {
  orig_chars: number
  new_chars: number
  saved_chars: number
  saved_pct: number
  mode: 'normal' | 'cache-aware'
  capabilities: string[]
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
  voice_enabled: boolean
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
