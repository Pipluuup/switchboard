import { useEffect, useState } from 'react'
import { Card, App, Progress, Tag, Tooltip, Typography, Alert } from 'antd'
import { GET } from '../api/client'

/** OpenCode Go 配额：Console 官方计量数据（Rolling / Weekly / Monthly） */

interface ConsoleMeter {
  key: string
  label: string
  used_usd: number
  limit_usd: number
  pct: number
  resets_at: string | null
  resets_in: number
}

interface ConsoleData {
  configured: boolean
  ok: boolean
  error: string
  fetched_at: number
  data: null | {
    product: string
    access_ends_at: string | null
    cancel_at_period_end: boolean
    meters: ConsoleMeter[]
  }
}

function fmtResetIn(sec: number): string {
  if (sec <= 0) return '即将重置'
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function fmtUsd(v: number): string {
  return `$${v >= 100 ? v.toFixed(0) : v.toFixed(2)}`
}

function barColor(pct: number): string {
  return pct >= 95 ? '#f2706b' : pct >= 80 ? '#e0b25a' : '#45c98a'
}

function productLabel(p: string): string {
  if (p === 'go-plus') return 'Go Plus 计划'
  if (p === 'go') return 'Go 计划'
  return p
}

export default function GoQuota() {
  const { message } = App.useApp()
  const [official, setOfficial] = useState<ConsoleData | null>(null)

  useEffect(() => {
    const load = () => GET<ConsoleData>('/api/opencode-console')
      .then(setOfficial)
      .catch(() => setOfficial(null))
    load()
    const t = setInterval(load, 5 * 60 * 1000)
    return () => clearInterval(t)
  }, [message])

  if (!official) return null
  const off = official.ok ? official.data : null

  return (
    <Card size="small" title="OpenCode Go 配额">
      {!official.configured && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 14 }}
          message="未配置 OpenCode Console 凭证"
          description={
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              到「设置 → OpenCode Console」填入工作区 ID 与会话 cookie（浏览器 F12 → 应用 → Cookie → 复制 __Host-console_session），即可显示官方面板的精确用量。
            </Typography.Text>
          }
        />
      )}
      {official.configured && !official.ok && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 14 }}
          message="拉取官方数据失败"
          description={<Typography.Text style={{ fontSize: 12 }}>{official.error}</Typography.Text>}
        />
      )}

      {off && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <Tag color="blue">官方数据</Tag>
            <span style={{ fontWeight: 550 }}>{productLabel(off.product)}</span>
            {off.access_ends_at && (
              <span style={{ fontSize: 12, color: '#5c6880' }}>
                {off.cancel_at_period_end ? 'Ends ' : 'Renews '}
                {new Date(off.access_ends_at).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}
              </span>
            )}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {off.meters.map((m) => (
              <div key={m.key} style={{ display: 'grid', gridTemplateColumns: '110px 1fr 170px', alignItems: 'center', gap: 14 }}>
                <div>
                  <div style={{ fontWeight: 550 }}>{m.label}</div>
                  <div style={{ fontSize: 11.5, color: '#5c6880' }}>Resets in {fmtResetIn(m.resets_in)}</div>
                </div>
                <Tooltip title={`${fmtUsd(m.used_usd)} / ${fmtUsd(m.limit_usd)}`}>
                  <Progress percent={m.pct} showInfo={false} size={['100%', 8]} strokeColor={barColor(m.pct)} trailColor="#181e2b" />
                </Tooltip>
                <div style={{ textAlign: 'right', fontSize: 12.5 }}>
                  <span style={{ color: barColor(m.pct), fontWeight: 550 }}>{m.pct}% used</span>
                  <div style={{ fontSize: 11, color: '#5c6880' }}>{fmtUsd(m.used_usd)} / {fmtUsd(m.limit_usd)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  )
}
