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
  health_check_interval: number
  log_retention_days: number
  health_check_enabled: boolean
  enable_log: boolean
  auto_open_browser: boolean
  opencode_org_id: string
  opencode_console_cookie: string
  ark_console_cookie: string
  show_plan_usage: boolean
  log_payloads: boolean
  payload_max_bytes: number
  caveman_enabled: boolean
}

export default function SettingsView() {
  const { message } = App.useApp()
  const [cache, setCache] = useState<Settings | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [tip, setTip] = useState<{ text: string; err?: boolean }>({ text: '' })
  const [planOpen, setPlanOpen] = useState(false)
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
          health_check_interval: s.health_check_interval,
          log_retention_days: s.log_retention_days,
          health_check_enabled: !!s.health_check_enabled,
          enable_log: !!s.enable_log,
          auto_open_browser: !!s.auto_open_browser,
          opencode_org_id: s.opencode_org_id || '',
          opencode_console_cookie: s.opencode_console_cookie || '',
          ark_console_cookie: s.ark_console_cookie || '',
          show_plan_usage: s.show_plan_usage !== false,
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
        health_check_interval: Number(draft.health_check_interval) || 120,
        log_retention_days: Number(draft.log_retention_days) || 0,
        health_check_enabled: draft.health_check_enabled,
        enable_log: draft.enable_log,
        auto_open_browser: draft.auto_open_browser,
        opencode_org_id: draft.opencode_org_id.trim(),
        opencode_console_cookie: draft.opencode_console_cookie.trim(),
        ark_console_cookie: draft.ark_console_cookie.trim(),
        show_plan_usage: draft.show_plan_usage,
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

  return (
    <>
      <Row gutter={[14, 14]}>
        <Col xs={24} lg={12}>
          <Card size="small" title="服务设置">
            <Space direction="vertical" size={13} style={{ width: '100%' }}>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>监听地址
                <Input style={{ marginTop: 4 }} value={draft.host} placeholder="127.0.0.1" onChange={(e) => set('host', e.target.value)} />
              </label>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>监听端口
                <InputNumber style={{ width: '100%', marginTop: 4 }} value={draft.port} onChange={(v) => set('port', v ?? 8000)} />
              </label>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>管理口令（留空即不鉴权）
                <Input style={{ marginTop: 4 }} value={draft.admin_token} placeholder="设置后前端需输入口令" onChange={(e) => set('admin_token', e.target.value)} />
              </label>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>请求超时（秒）{num('request_timeout')}</label>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="调度与容错">
            <Space direction="vertical" size={13} style={{ width: '100%' }}>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>失败阈值（连续失败几次进入冷却）{num('failure_threshold')}</label>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>冷却时间（秒）{num('cooldown_seconds')}</label>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>健康检查间隔（秒）{num('health_check_interval')}</label>
              {check('health_check_enabled', '启用后台健康检查')}
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="日志">
            <Space direction="vertical" size={13} style={{ width: '100%' }}>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>日志保留天数（0 = 永久）{num('log_retention_days')}</label>
              {check('enable_log', '记录请求日志')}
              {check('log_payloads', '记录请求/响应详情（日志页可查看原文，占空间，按需开启）')}
              {draft.log_payloads && (
                <label style={{ fontSize: 12.5, color: '#93a0b8' }}>详情截断长度（字符）{num('payload_max_bytes')}</label>
              )}
              {check('auto_open_browser', '启动时自动打开浏览器')}
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="配置备份">
            <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
              导出为 JSON，可在另一台机器一键导入（含上游、映射、Key）。为安全起见导入不含监听地址与端口。
            </Typography.Paragraph>
            <Space>
              <Button icon={<DownloadOutlined />} onClick={exportCfg}>导出配置</Button>
              <Button icon={<UploadOutlined />} onClick={() => fileRef.current?.click()}>导入配置</Button>
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
            title={
              <Space size={14} wrap>
                Plan 实时查询（配额官方数据）
                <Checkbox checked={planOpen} onChange={(e) => setPlanOpen(e.target.checked)}>
                  展开配置
                </Checkbox>
                <Switch
                  size="small"
                  checked={draft.show_plan_usage}
                  checkedChildren="展示用量"
                  unCheckedChildren="隐藏用量"
                  onChange={(v) => set('show_plan_usage', v)}
                />
              </Space>
            }
          >
            {planOpen && (
            <Space direction="vertical" size={13} style={{ width: '100%' }}>
              <Typography.Paragraph type="secondary" style={{ fontSize: 11.5, marginBottom: 0, lineHeight: 1.7 }}>
                配置后「用量」页实时显示各 Coding Plan 的官方配额（OpenCode Go + 火山方舟）。
              </Typography.Paragraph>

              <div style={{ fontWeight: 550, fontSize: 12.5 }}>① OpenCode Console（Go 计划）</div>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>工作区 ID
                <Input className="mono" style={{ marginTop: 4 }} value={draft.opencode_org_id}
                  placeholder="wrk_xxxxxxxxxx（Console 地址 /console/&lt;id&gt;/go 里的 id）"
                  onChange={(e) => set('opencode_org_id', e.target.value)} />
              </label>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>会话 Cookie
                <Input.Password className="mono" style={{ marginTop: 4 }} value={draft.opencode_console_cookie}
                  placeholder="__Host-console_session 的值"
                  onChange={(e) => set('opencode_console_cookie', e.target.value)} />
              </label>
              <Typography.Paragraph type="secondary" style={{ fontSize: 11.5, marginBottom: 0, lineHeight: 1.7 }}>
                获取方式：登录 opencode.ai/console 后按 F12 → 应用(Application) → Cookie →
                opencode.ai，复制 <code>__Host-console_session</code> 的值。约 30 天有效，过期后用量页会提示。
              </Typography.Paragraph>

              <div style={{ fontWeight: 550, fontSize: 12.5, marginTop: 6 }}>② 火山方舟（编码计划）</div>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>控制台 Cookie
                <Input.Password className="mono" style={{ marginTop: 4 }} value={draft.ark_console_cookie}
                  placeholder="api/top/ark 请求头里的 Cookie 整串"
                  onChange={(e) => set('ark_console_cookie', e.target.value)} />
              </label>
              <Typography.Paragraph type="secondary" style={{ fontSize: 11.5, marginBottom: 0, lineHeight: 1.7 }}>
                获取方式：登录 console.volcengine.com/ark 打开编码计划页面，按 F12 → 网络(Network) →
                随便找一条 <code>api/top/ark</code> 请求 → 请求标头 → 复制
                <code>Cookie:</code> 后面的整串内容。会话失效后（用量页出现提示）重新复制即可。
              </Typography.Paragraph>
            </Space>
            )}
          </Card>
        </Col>
      </Row>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, paddingTop: 20 }}>
        <Button type="primary" icon={<SaveOutlined />} onClick={save}>保存设置</Button>
        {tip.text && (
          <Typography.Text type={tip.err ? 'danger' : 'success'} style={{ fontSize: 12.5 }}>{tip.text}</Typography.Text>
        )}
      </div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 10 }}>
        修改监听地址 / 端口后保存，服务会自动重启到新地址并生效；若只改监听地址不改端口，需先停止服务再启动。
      </Typography.Paragraph>
    </>
  )
}
