import { Card, Statistic } from 'antd'

export interface StatCardProps {
  label: string
  value: string | number
  foot: string
  color?: 'accent' | 'green' | 'yellow' | 'red' | 'purple' | 'cyan'
  /** 右上角补充说明（如「缓存命中 1.2M」） */
  extra?: string
}

const VALUE_COLORS: Record<NonNullable<StatCardProps['color']>, string> = {
  accent: '#96aaff',
  green: '#45c98a',
  yellow: '#e0b25a',
  red: '#f2706b',
  purple: '#b48cf2',
  cyan: '#56c8e8',
}

const EDGE_COLORS: Record<NonNullable<StatCardProps['color']>, string> = {
  ...VALUE_COLORS,
  accent: '#7e96ff',
}

/** 统计卡：对应旧版 .card 结构，顶部带渐变色带 */
export default function StatCard({ label, value, foot, color = 'accent', extra }: StatCardProps) {
  return (
    <Card size="small" styles={{ body: { padding: 0 } }} style={{ overflow: 'hidden' }}>
      <div
        style={{
          height: 2,
          background: `linear-gradient(90deg, transparent, ${EDGE_COLORS[color]}, transparent)`,
          opacity: 0.6,
        }}
      />
      <div style={{ padding: '13px 16px 12px' }}>
        <div style={{ fontSize: 12, color: '#5c6880' }}>
          {label}
          {extra && <span style={{ float: 'right', color: '#7fb3ff' }}>{extra}</span>}
        </div>
        <Statistic value={value} valueStyle={{ fontSize: 22, fontWeight: 600, color: VALUE_COLORS[color], lineHeight: 1.35 }} />
        <div style={{ fontSize: 11, color: '#5c6880', marginTop: 2 }}>{foot}</div>
      </div>
    </Card>
  )
}
