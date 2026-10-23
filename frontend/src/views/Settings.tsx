import { useEffect, useRef, useState } from 'react'
import { Card, Button, Input, InputNumber, Checkbox, Switch, App, Space, Typography, Row, Col } from 'antd'
import { SaveOutlined, DownloadOutlined, UploadOutlined } from '@ant-design/icons'
import { GET, POST, PUT } from '../api/client'
import type { Settings } from '../api/types'

/** 设置：对应旧版 loadSettings + saveSettings（含改地址自动重启跳转）+ 导出/导入 */

type Draft = {
  host: string
  port: number
  admin_token: string
  request_timeout: number
  failure_threshold: number
  cooldown_seconds: number
  circuit_enabled: boolean
  circuit_error_rate_threshold: number
  circuit_min_requests: number
  circuit_open_seconds: number
  circuit_half_open_success: number
  circuit_window_seconds: number
  health_check_interval: number
  log_retention_days: number
  health_check_enabled: boolean
  enable_log: boolean
  auto_open_browser: boolean
  show_usage_opencode: boolean
  show_usage_ark: boolean
  log_payloads: boolean
  payload_max_bytes: number
  caveman_enabled: boolean
}

export default function SettingsView() {
  const { message } = App.useApp()
  const [cache, setCache] = useState<Settings | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [tip, setTip] = useState<{ text: string; err?: boolean }>({ text: '' })
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    GET<Settings>('/api/settings')
      .then((s) => {
        setCache(s)
        setDraft({
          host: s.host,
          port: s.port,
          admin_token: s.admin_token,
          request_timeout: s.request_timeout,
          failure_threshold: s.failure_threshold,
          cooldown_seconds: s.cooldown_seconds,
          circuit_enabled: s.circuit_enabled !== false,
          circuit_error_rate_threshold: s.circuit_error_rate_threshold ?? 0.5,
          circuit_min_requests: s.circuit_min_requests ?? 10,
          circuit_open_seconds: s.circuit_open_seconds ?? 60,
          circuit_half_open_success: s.circuit_half_open_success ?? 2,
          circuit_window_seconds: s.circuit_window_seconds ?? 120,
          health_check_interval: s.health_check_interval,
          log_retention_days: s.log_retention_days,
          health_check_enabled: !!s.health_check_enabled,
          enable_log: !!s.enable_log,
          auto_open_browser: !!s.auto_open_browser,
          show_usage_opencode: s.show_usage_opencode !== false,
          show_usage_ark: s.show_usage_ark !== false,
          log_payloads: !!s.log_payloads,
          payload_max_bytes: s.payload_max_bytes || 8192,
          caveman_enabled: !!s.caveman_enabled,
        })
      })
      .catch((e) => message.error('加载设置失败：' + e.message))
  }, [message])

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => (d ? { ...d, [k]: v } : d))

  const save = async () => {
    if (!draft || !cache) return
    const nextHost = (draft.host || '').trim() || '127.0.0.1'
    const nextPort = Number(draft.port) || 8000
    const addrChanged =
      nextHost.toLowerCase() !== String(cache.host).toLowerCase() || nextPort !== Number(cache.port)
    try {
      if (addrChanged) {
        const ok = window.confirm(
          `监听地址将改为 ${nextHost}:${nextPort}\n` +
            '服务会自动重启到新地址（几秒内完成，期间请求短暂中断），\n' +
            '成功后本页面会自动跳转到新地址。确定继续？',
        )
        if (!ok) return
        setTip({ text: '正在重启服务到新地址，请稍候…' })
        const r = await POST<{ restarted: boolean; base_url: string }>('/api/server/restart', { host: nextHost, port: nextPort })
        if (r.restarted === false) {
          setTip({ text: '' })
          message.info('监听地址未变化，无需重启')
          return
        }
        setTip({ text: '' })
        const navHost = ['', '0.0.0.0', '::'].includes(nextHost.toLowerCase()) ? location.hostname : nextHost
        message.success(`服务已在 ${r.base_url} 就绪，正在跳转…`, 6)
        setTimeout(() => { location.href = `http://${navHost}:${nextPort}/` }, 1200)
        return
      }
      await PUT('/api/settings', {
        admin_token: draft.admin_token,
        request_timeout: Number(draft.request_timeout) || 180,
        failure_threshold: Number(draft.failure_threshold) || 3,
        cooldown_seconds: Number(draft.cooldown_seconds) || 60,
        circuit_enabled: draft.circuit_enabled,
        circuit_error_rate_threshold: Number(draft.circuit_error_rate_threshold) || 0.5,
        circuit_min_requests: Number(draft.circuit_min_requests) || 10,
        circuit_open_seconds: Number(draft.circuit_open_seconds) || 60,
        circuit_half_open_success: Number(draft.circuit_half_open_success) || 2,
        circuit_window_seconds: Number(draft.circuit_window_seconds) || 120,
        health_check_interval: Number(draft.health_check_interval) || 120,
        log_retention_days: Number(draft.log_retention_days) || 0,
        health_check_enabled: draft.health_check_enabled,
        enable_log: draft.enable_log,
        auto_open_browser: draft.auto_open_browser,
        show_usage_opencode: draft.show_usage_opencode,
        show_usage_ark: draft.show_usage_ark,
        log_payloads: draft.log_payloads,
        payload_max_bytes: Number(draft.payload_max_bytes) || 8192,
        caveman_enabled: draft.caveman_enabled,
      })
      setTip({ text: '已保存' })
      setTimeout(() => setTip({ text: '' }), 4000)
      message.success('设置已保存')
    } catch (e) {
      setTip({ text: '保存失败：' + (e as Error).message, err: true })
    }
  }

  const exportCfg = async () => {
    try {
      const cfg = await GET<Record<string, unknown>>('/api/config/export')
      const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `ccproxy-config-${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      message.success('已导出')
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const importCfg = async (f: File) => {
    try {
      const cfg = JSON.parse(await f.text())
      if (!window.confirm('导入将覆盖现有上游、映射与 Key，确定继续？')) return
      const r = await POST<{ upstreams: number; mappings: number; keys: number }>('/api/config/import', cfg)
      message.success(`已导入：${r.upstreams} 个上游 / ${r.mappings} 条映射 / ${r.keys} 个 Key`)
    } catch (e) {
      message.error('导入失败：' + (e as Error).message)
    }
  }

  if (!draft) return null

  const num = (k: keyof Draft) => (
    <InputNumber style={{ width: '100%' }} value={draft[k] as number} min={0} onChange={(v) => set(k, (v ?? 0) as never)} />
  )
  const check = (k: keyof Draft, label: string) => (
    <Checkbox checked={draft[k] as boolean} onChange={(e) => set(k, e.target.checked as never)}>{label}</Checkbox>
  )

  /** 紧凑数值项：label 在上（13px, 4px 下距），下面接数值框 */
  const numRow = (label: string, k: keyof Draft, hint?: string) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12.5, color: '#93a0b8' }}>{label}</span>
      {num(k)}
      {hint && <span style={{ fontSize: 11, color: '#5c6880' }}>{hint}</span>}
    </div>
  )

  /** 紧凑复选项 */
  const checkRow = (k: keyof Draft, label: string) => (
    <div style={{ fontSize: 12.5 }}>
      {check(k, label)}
    </div>
  )

  return (
    <>
      <Row gutter={[10, 10]}>
        <Col xs={24} lg={12}>
          <Card size="small" title="服务与调度" styles={{ body: { padding: '12px 16px' } }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 12px' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, gridColumn: 'span 2' }}>
                <span style={{ fontSize: 12.5, color: '#93a0b8' }}>监听地址</span>
                <Input value={draft.host} placeholder="127.0.0.1" onChange={(e) => set('host', e.target.value)} />
              </div>
              {numRow('监听端口', 'port')}
              {numRow('请求超时（秒）', 'request_timeout')}
              <div style={{ gridColumn: 'span 2', display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ fontSize: 12.5, color: '#93a0b8' }}>管理口令（留空即不鉴权）</span>
                <Input value={draft.admin_token} placeholder="设置后前端需输入口令" onChange={(e) => set('admin_token', e.target.value)} />
              </div>
              <div style={{ gridColumn: 'span 2' }}>{checkRow('health_check_enabled', '启用后台健康检查')}</div>
              {numRow('健康检查间隔（秒）', 'health_check_interval')}
              {numRow('失败阈值（连续失败几次进入冷却）', 'failure_threshold')}
              {numRow('冷却时间（秒）', 'cooldown_seconds')}
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="熔断器" styles={{ body: { padding: '12px 16px' } }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 12px' }}>
              <div style={{ gridColumn: 'span 2' }}>
                {checkRow('circuit_enabled', '启用熔断器（上游异常时自动拉黑，避免每个请求都去撞一次）')}
              </div>
              {draft.circuit_enabled && (
                <>
                  {numRow('错误率阈值（0~1，窗口内失败占比超它就熔断）', 'circuit_error_rate_threshold')}
                  {numRow('最小样本数（样本不足时不按错误率判定）', 'circuit_min_requests')}
                  {numRow('熔断持续（秒，到期进入试探期）', 'circuit_open_seconds')}
                  {numRow('试探期需连续成功几次才恢复', 'circuit_half_open_success')}
                  {numRow('错误率统计窗口（秒）', 'circuit_window_seconds')}
                </>
              )}
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="日志" styles={{ body: { padding: '12px 16px' } }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 12px' }}>
              {numRow('日志保留天数（0 = 永久）', 'log_retention_days')}
              {draft.log_payloads && numRow('详情截断长度（字符）', 'payload_max_bytes')}
              <div style={{ gridColumn: 'span 2', display: 'flex', flexDirection: 'column', gap: 6 }}>
                {checkRow('enable_log', '记录请求日志')}
                {checkRow('log_payloads', '记录请求/响应详情（日志页可查看原文，占空间，按需开启）')}
                {checkRow('auto_open_browser', '启动时自动打开浏览器')}
              </div>
            </div>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="配置备份" styles={{ body: { padding: '12px 16px' } }}>
            <Typography.Paragraph type="secondary" style={{ fontSize: 11.5, marginBottom: 8, marginTop: 0 }}>
              导出为 JSON（含上游、映射、Key），另一台机器一键导入。导入不含监听地址与端口，出于安全。
            </Typography.Paragraph>
            <Space size={8}>
              <Button size="small" icon={<DownloadOutlined />} onClick={exportCfg}>导出配置</Button>
              <Button size="small" icon={<UploadOutlined />} onClick={() => fileRef.current?.click()}>导入配置</Button>
              <input
                ref={fileRef}
                type="file"
                accept=".json"
                style={{ display: 'none' }}
                onChange={async (e) => {
                  const f = e.target.files?.[0]
                  if (f) await importCfg(f)
                  e.target.value = ''
                }}
              />
            </Space>
          </Card>
        </Col>
        <Col xs={24}>
          <Card
            size="small"
            title="Plan 实时查询（配额官方数据）"
            styles={{ body: { padding: '12px 16px' } }}
          >
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Typography.Paragraph type="secondary" style={{ fontSize: 11.5, marginBottom: 0, lineHeight: 1.6 }}>
                配置后「用量」页显示各 Coding Plan 的官方配额。Cookie 通过<b> Switchboard Cookie 助手</b>浏览器扩展一键写入。
              </Typography.Paragraph>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5 }}>
                  <Switch size="small" checked={draft.show_usage_opencode} onChange={(v) => set('show_usage_opencode', v)} />
                  展示 OpenCode Go 用量
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5 }}>
                  <Switch size="small" checked={draft.show_usage_ark} onChange={(v) => set('show_usage_ark', v)} />
                  展示火山方舟用量
                </label>
              </div>
              <div style={{ borderTop: '1px solid #1d2434', paddingTop: 10 }}>
                <div style={{ fontWeight: 550, fontSize: 12.5, marginBottom: 6 }}>
                  Cookie 自动写入 · Switchboard Cookie 助手（浏览器扩展）
                </div>
                <Space direction="vertical" size={4} style={{ width: '100%', fontSize: 12 }}>
                  <div>1. <a href="/api/extension/download">下载扩展</a> 并解压</div>
                  <div>2. 打开 <code>chrome://extensions</code>，开启「开发者模式」→「加载已解压的扩展程序」→ 选 <code>switchboard-cookie-helper</code> 目录</div>
                  <div>3. 在本浏览器分别登录 OpenCode Console 与火山方舟控制台，点扩展图标 → 对应按钮一键写入</div>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                    Cookie 过期后（用量页出现提示）重新点一下扩展按钮即可。若后台设置了管理令牌，在扩展弹窗底部填入一次。
                  </Typography.Text>
                </Space>
              </div>
            </Space>
          </Card>
        </Col>
      </Row>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, paddingTop: 12 }}>
        <Button type="primary" icon={<SaveOutlined />} onClick={save}>保存设置</Button>
        {tip.text && (
          <Typography.Text type={tip.err ? 'danger' : 'success'} style={{ fontSize: 12.5 }}>{tip.text}</Typography.Text>
        )}
      </div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
        修改监听地址 / 端口后保存，服务会自动重启到新地址并生效；若只改监听地址不改端口，需先停止服务再启动。
      </Typography.Paragraph>
    </>
  )
}
