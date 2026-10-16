import { useEffect, useState } from 'react'
import { Card, Table, Select, App, Tag, Typography } from 'antd'
import { GET } from '../api/client'
import type { Usage, UsageWindow, Settings } from '../api/types'
import { fmtNum, fmtAgo } from '../utils/format'
import StatCard from '../components/StatCard'
import GoQuota from '../components/GoQuota'
import ArkQuota from '../components/ArkQuota'

const USAGE_WINDOWS = ['5h', '7d', '30d'] as const
const USAGE_COLORS = { '5h': 'accent', '7d': 'purple', '30d': 'cyan' } as const

function rateTag(w: UsageWindow) {
  if (w.requests === 0) return <Tag color="default">无数据</Tag>
  const color = w.success_rate >= 95 ? 'green' : w.success_rate >= 80 ? 'gold' : 'red'
  return <Tag color={color}>{w.success_rate}%</Tag>
}

/** 用量：对应旧版 loadUsage() + renderUsage() */
export default function Usage() {
  const { message } = App.useApp()
  const [data, setData] = useState<Usage | null>(null)
  const [win, setWin] = useState<(typeof USAGE_WINDOWS)[number]>('5h')
  const [showGo, setShowGo] = useState(false)
  const [showArk, setShowArk] = useState(false)

  useEffect(() => {
    GET<Usage>('/api/usage')
      .then(setData)
      .catch((e) => message.error('加载用量失败：' + e.message))
    GET<Settings>('/api/settings').then((s) => { setShowGo(s.show_usage_opencode !== false); setShowArk(s.show_usage_ark !== false) }).catch(() => {})
  }, [message])

  if (!data) return null
  const wins = data.windows || []
  const byKey = Object.fromEntries(wins.map((w) => [w.key, w]))
  const hasAny = wins.some((w) => w.requests > 0)

  // 保留天数提示（移植旧逻辑）
  const ret = Number(data.retention_days || 0)
  const hints: React.ReactNode[] = []
  if (ret > 0 && ret < 30) {
    hints.push(
      <span key="ret">
        <Tag color="gold">注意</Tag> 日志只保留 <code>{ret}</code> 天，「近 30 天」窗口实际最多只能看到 {ret} 天的数据。
        到「设置 → 日志保留天数」改成 <code>0</code>（永久）或 <code>30</code> 以上才能看满。
      </span>,
    )
  } else {
    hints.push(
      <span key="roll">
        三个窗口都是<b>滚动</b>统计（从当前时刻往前回溯），不是自然日 / 自然月。
      </span>,
    )
  }
  if (!hasAny) hints.push(<span key="empty">还没有任何请求记录，先用客户端发一条消息。</span>)

  const selected = byKey[win]
  const modelRows = selected?.by_model || []
  const totalTok = modelRows.reduce((a, m) => a + Number(m.tin || 0) + Number(m.tout || 0), 0) || 1

  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12, marginBottom: 18 }}>        {USAGE_WINDOWS.map((k) => {
          const w = byKey[k]
          if (!w) return null
          const rate = w.requests ? w.success_rate + '%' : '—'
          return (
            <StatCard
              key={k}
              color={USAGE_COLORS[k]}
              label={`${w.label} Tokens`}
              value={fmtNum(w.total_tokens)}
              foot={`输入 ${fmtNum(w.input_tokens)} · 输出 ${fmtNum(w.output_tokens)} · ${fmtNum(w.requests)} 次请求 · 成功率 ${rate}`}
            />
          )
        })}
      </div>

      <Card size="small" title="滚动窗口用量" extra={<Typography.Text type="secondary" style={{ fontSize: 11 }}>5 小时 / 7 天 / 30 天</Typography.Text>}>
        <Table<UsageWindow>
          size="small"
          rowKey="key"
          dataSource={wins}
          pagination={false}
          columns={[
            { title: '窗口', dataIndex: 'label', render: (v: string) => <b>{v}</b> },
            { title: '请求数', dataIndex: 'requests', render: (v: number) => fmtNum(v) },
            {
              title: '成功 / 失败',
              render: (_: unknown, w: UsageWindow) => (
                <>
                  <span style={{ color: '#45c98a' }}>{fmtNum(w.ok_requests)}</span> /{' '}
                  <span style={{ color: w.failed_requests ? '#f2706b' : undefined }}>{fmtNum(w.failed_requests)}</span>
                </>
              ),
            },
            { title: '成功率', dataIndex: 'success_rate', render: (_: unknown, w: UsageWindow) => rateTag(w) },
            { title: '输入 Tokens', dataIndex: 'input_tokens', render: (v: number) => fmtNum(v) },
            { title: '输出 Tokens', dataIndex: 'output_tokens', render: (v: number) => fmtNum(v) },
            { title: '合计 Tokens', dataIndex: 'total_tokens', render: (v: number) => <b>{fmtNum(v)}</b> },
            { title: '平均延迟', render: (_: unknown, w: UsageWindow) => (w.requests ? (w.avg_latency_ms || 0) + ' ms' : '-') },
            { title: '最近一次', render: (_: unknown, w: UsageWindow) => <span style={{ fontSize: 11.5, color: '#93a0b8' }}>{fmtAgo(w.last_ts)}</span> },
          ]}
          locale={{ emptyText: '暂无数据' }}
        />
        <p style={{ fontSize: 12, color: '#5c6880', marginTop: 10, lineHeight: 1.7 }}>{hints}</p>
      </Card>

      <Card
        size="small"
        title="窗口内模型用量"
        extra={
          <Select
            value={win}
            onChange={(v) => setWin(v)}
            style={{ width: 130 }}
            options={[
              { value: '5h', label: '近 5 小时' },
              { value: '7d', label: '近 7 天' },
              { value: '30d', label: '近 30 天' },
            ]}
          />
        }
      >
        <Table
          size="small"
          rowKey="model"
          dataSource={modelRows}
          pagination={false}
          columns={[
            { title: '模型', dataIndex: 'model', render: (v: string) => <span className="mono">{v || '-'}</span> },
            { title: '请求数', dataIndex: 'n', width: 110, render: (v: number) => fmtNum(v) },
            { title: '输入 Tokens', dataIndex: 'tin', width: 130, render: (v: number) => fmtNum(v) },
            { title: '输出 Tokens', dataIndex: 'tout', width: 130, render: (v: number) => fmtNum(v) },
            {
              title: '合计',
              width: 110,
              render: (_: unknown, m: { tin: number; tout: number }) => <b>{fmtNum(Number(m.tin || 0) + Number(m.tout || 0))}</b>,
            },
            {
              title: '占比',
              width: 220,
              render: (_: unknown, m: { tin: number; tout: number }) => {
                const tok = Number(m.tin || 0) + Number(m.tout || 0)
                const pct = ((tok / totalTok) * 100).toFixed(1)
                return (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 160 }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ height: 5, background: '#181e2b', borderRadius: 3, overflow: 'hidden', minWidth: 50 }}>
                        <div style={{ width: `${pct}%`, height: '100%', background: '#7e96ff' }} />
                      </div>
                    </div>
                    <span style={{ fontSize: 12, color: '#93a0b8', width: 46, textAlign: 'right' }}>{pct}%</span>
                  </div>
                )
              },
            },
          ]}
          locale={{ emptyText: `${selected?.label || '该窗口'}内没有请求` }}
        />
      </Card>

      {showGo && <GoQuota />}
      {showArk && <ArkQuota />}
    </>
  )
}
