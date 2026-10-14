import { useEffect, useState } from 'react'
import { Card, Table, App, Progress, Typography } from 'antd'
import { GET } from '../api/client'
import type { Overview } from '../api/types'
import { fmtNum } from '../utils/format'
import StatCard from '../components/StatCard'
import LineChart from '../components/LineChart'
import BarChart from '../components/BarChart'

interface ModelRow {
  request_model: string
  n: number
  tin: number
  tout: number
}

/** 概览：对应旧版 loadDashboard() */
export default function Dashboard() {
  const { message } = App.useApp()
  const [data, setData] = useState<Overview | null>(null)

  useEffect(() => {
    GET<Overview>('/api/overview')
      .then(setData)
      .catch((e) => message.error('加载概览失败：' + e.message))
  }, [message])

  if (!data) return null
  const s = data.stats

  const successColor = (s.success_rate >= 95 ? 'green' : s.success_rate >= 80 ? 'yellow' : 'red') as
    | 'green'
    | 'yellow'
    | 'red'
  const cards = [
    { color: 'accent' as const, label: '总请求数', value: fmtNum(s.total_requests), foot: `近 24h ${fmtNum(s.requests_24h)} 次` },
    { color: successColor, label: '成功率', value: s.success_rate + '%', foot: `失败 ${fmtNum(s.failed_requests)} 次` },
    { color: 'purple' as const, label: '输入 Tokens', value: fmtNum(s.total_input_tokens), foot: '累计' },
    { color: 'cyan' as const, label: '输出 Tokens', value: fmtNum(s.total_output_tokens), foot: '累计' },
    { color: 'yellow' as const, label: '平均延迟', value: (s.avg_latency_ms || 0) + ' ms', foot: '仅统计成功请求' },
    {
      color: 'green' as const,
      label: '可用上游',
      value: `${data.enabled_upstream_count}/${data.upstream_count}`,
      foot: `${data.mapping_count} 条映射 · ${data.key_count} 个 Key`,
    },
  ]

  const rows = s.by_model || []
  const total = rows.reduce((a, b) => a + Number(b.n || 0), 0) || 1

  return (
    <>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(158px, 1fr))', gap: 12, marginBottom: 18 }}>
        {cards.map((c) => (
          <StatCard key={c.label} {...c} />
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))', gap: 14, marginBottom: 16 }}>
        <Card
          size="small"
          title="近 24 小时请求趋势"
          extra={<Typography.Text type="secondary" style={{ fontSize: 11 }}>按小时</Typography.Text>}
        >
          <div style={{ height: 208 }}>
            {(data.timeline || []).length ? (
              <LineChart buckets={data.timeline} />
            ) : (
              <div className="empty-chart">暂无请求数据，先用 Claude Code 发一条消息试试</div>
            )}
          </div>
        </Card>
        <Card
          size="small"
          title="上游负载分布"
          extra={<Typography.Text type="secondary" style={{ fontSize: 11 }}>请求数</Typography.Text>}
        >
          <div style={{ height: 208 }}>
            <BarChart items={s.by_upstream} />
          </div>
        </Card>
      </div>

      <Card size="small" title="模型用量 Top 10">
        <Table<ModelRow>
          size="small"
          rowKey={(r) => r.request_model}
          dataSource={rows}
          pagination={false}
          columns={[
            {
              title: '模型',
              dataIndex: 'request_model',
              render: (v: string) => <span className="mono">{v || '-'}</span>,
            },
            { title: '请求数', dataIndex: 'n', width: 110, render: (v: number) => fmtNum(v) },
            { title: '输入 Tokens', dataIndex: 'tin', width: 130, render: (v: number) => fmtNum(v) },
            { title: '输出 Tokens', dataIndex: 'tout', width: 130, render: (v: number) => fmtNum(v) },
            {
              title: '占比',
              width: 220,
              render: (_: unknown, r: ModelRow) => {
                const pct = ((r.n / total) * 100).toFixed(1)
                return (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 160 }}>
                    <div style={{ flex: 1 }}>
                      <Progress percent={Number(pct)} showInfo={false} size={['100%', 5]} strokeColor="#7e96ff" trailColor="#181e2b" />
                    </div>
                    <span style={{ fontSize: 12, color: '#93a0b8', width: 46, textAlign: 'right' }}>{pct}%</span>
                  </div>
                )
              },
            },
          ]}
          locale={{ emptyText: '暂无数据' }}
        />
      </Card>
    </>
  )
}
