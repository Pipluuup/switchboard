import { Tooltip } from 'antd'
import type { OverviewStats } from '../api/types'

/** 上游负载横向条形图：HTML 排版（标签正常字号），色带按成功率着色 */
export default function BarChart({ items }: { items: OverviewStats['by_upstream'] }) {
  const data = (items || []).slice(0, 6)
  if (!data.length) {
    return <div className="empty-chart">暂无数据</div>
  }
  const max = Math.max(1, ...data.map((d) => Number(d.n) || 0))

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        gap: 20,
        padding: '4px 0',
      }}
    >
      {data.map((d, i) => {
        const n = Number(d.n) || 0
        const rate = n ? Math.round((d.ok_n / n) * 100) : 0
        const color = rate >= 95 ? '#45c98a' : rate >= 80 ? '#e0b25a' : '#f2706b'
        return (
          <Tooltip key={i} title={`${d.upstream_name || '未命名'}：${n} 次请求 · 成功率 ${rate}%`}>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'minmax(88px, 150px) 1fr max-content',
                alignItems: 'center',
                gap: 14,
                cursor: 'default',
              }}
            >
              <div
                style={{
                  fontSize: 13,
                  color: '#c6cfdd',
                  textAlign: 'right',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {d.upstream_name || '未命名'}
              </div>
              <div style={{ height: 10, borderRadius: 5, background: '#141a26' }}>
                <div
                  style={{
                    width: `${n ? Math.max(2, (n / max) * 100) : 0}%`,
                    height: '100%',
                    borderRadius: 5,
                    background: `linear-gradient(90deg, ${color}66, ${color})`,
                    transition: 'width 0.35s ease',
                  }}
                />
              </div>
              <div style={{ fontSize: 12.5, color: '#5c6880', fontVariantNumeric: 'tabular-nums' }}>
                <span style={{ color: '#e8edf5', fontWeight: 550 }}>{n}</span>
                {' · '}
                {rate}%
              </div>
            </div>
          </Tooltip>
        )
      })}
    </div>
  )
}
