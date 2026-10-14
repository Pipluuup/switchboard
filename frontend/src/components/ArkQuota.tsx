import { useEffect, useState } from 'react'
import { Card, App, Progress, Tag, Tooltip, Typography, Alert } from 'antd'
import { GET } from '../api/client'

/** 火山方舟配额：Agent Plan（AFP 点数）+ Coding Plan（百分比），控制台官方数据 */

interface AgentMeter {
  key: string
  label: string
  used: number
  quota: number
  pct: number
  resets_in: number
}

interface CodingMeter {
  key: string
  label: string
  pct: number
  cap: number | null
  resets_in: number
}

interface ArkData {
  configured: boolean
  ok: boolean
  error: string
  fetched_at: number
  agent: null | { plan_type: string; meters: AgentMeter[] }
  coding: null | { status: string; meters: CodingMeter[] }
}

const PLAN_LABELS: Record<string, string> = {
  small: 'Small 档',
  medium: 'Medium 档',
  large: 'Large 档',
  max: 'Max 档',
}

function fmtResetIn(sec: number): string {
  if (sec <= 0) return '—'
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d > 0) return `${d}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function fmtNum(v: number): string {
  return v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : v >= 10_000 ? `${(v / 1000).toFixed(1)}k` : String(v)
}

function barColor(pct: number): string {
  return pct >= 95 ? '#f2706b' : pct >= 80 ? '#e0b25a' : '#45c98a'
}

function MeterRow({ label, resetsIn, tooltip, pct, children }: {
  label: string
  resetsIn: number
  tooltip: string
  pct: number
  children: React.ReactNode
}) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr 170px', alignItems: 'center', gap: 14 }}>
      <div>
        <div style={{ fontWeight: 550 }}>{label}</div>
        <div style={{ fontSize: 11.5, color: '#5c6880' }}>Resets in {fmtResetIn(resetsIn)}</div>
      </div>
      <Tooltip title={tooltip}>
        <Progress percent={pct} showInfo={false} size={['100%', 8]} strokeColor={barColor(pct)} trailColor="#181e2b" />
      </Tooltip>
      <div style={{ textAlign: 'right', fontSize: 12.5 }}>{children}</div>
    </div>
  )
}

export default function ArkQuota() {
  const { message } = App.useApp()
  const [data, setData] = useState<ArkData | null>(null)

  useEffect(() => {
    const load = () => GET<ArkData>('/api/ark-console')
      .then(setData)
      .catch(() => setData(null))
    load()
    const t = setInterval(load, 5 * 60 * 1000)
    return () => clearInterval(t)
  }, [message])

  if (!data) return null

  return (
    <Card size="small" title="火山方舟 · 编码计划配额">
      {!data.configured && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 0 }}
          message="未配置火山方舟凭证"
          description={
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              到「设置 → 火山方舟」粘贴控制台 Cookie，即可显示官方面板的精确用量。
            </Typography.Text>
          }
        />
      )}
      {data.configured && !data.ok && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 0 }}
          message="拉取官方数据失败"
          description={<Typography.Text style={{ fontSize: 12 }}>{data.error}</Typography.Text>}
        />
      )}
      {data.configured && data.ok && data.error && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 14 }}
          message="部分数据拉取失败"
          description={<Typography.Text style={{ fontSize: 12 }}>{data.error}</Typography.Text>}
        />
      )}

      {data.coding && (
        <div style={data.agent ? { marginBottom: 20 } : undefined}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <Tag color="blue">官方数据</Tag>
            <span style={{ fontWeight: 550 }}>Coding Plan（编码套餐）</span>
            {data.coding.status && (
              <span style={{ fontSize: 12, color: '#5c6880' }}>
                {data.coding.status === 'Running' ? '生效中' : data.coding.status}
              </span>
            )}
            <span style={{ fontSize: 12, color: '#5c6880' }}>百分比计量</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {data.coding.meters.map((m) => (
              <MeterRow
                key={m.key}
                label={m.label}
                resetsIn={m.resets_in}
                pct={m.pct}
                tooltip={`${m.pct}% / Cap ${m.cap ?? '-'}`}
              >
                <span style={{ color: barColor(m.pct), fontWeight: 550 }}>{m.pct}% used</span>
              </MeterRow>
            ))}
          </div>
        </div>
      )}

      {data.agent && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
            <Tag color="blue">官方数据</Tag>
            <span style={{ fontWeight: 550 }}>Agent Plan</span>
            <span style={{ fontSize: 12, color: '#5c6880' }}>
              {PLAN_LABELS[data.agent.plan_type] || data.agent.plan_type} · AFP 点数计量
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {data.agent.meters.map((m) => (
              <MeterRow
                key={m.key}
                label={m.label}
                resetsIn={m.resets_in}
                pct={m.pct}
                tooltip={`${fmtNum(m.used)} / ${fmtNum(m.quota)} 点`}
              >
                <span style={{ color: barColor(m.pct), fontWeight: 550 }}>{m.pct}% used</span>
                <div style={{ fontSize: 11, color: '#5c6880' }}>{fmtNum(m.used)} / {fmtNum(m.quota)}</div>
              </MeterRow>
            ))}
          </div>
        </div>
      )}
    </Card>
  )
}
