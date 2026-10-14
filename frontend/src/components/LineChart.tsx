import { Tooltip } from 'antd'
import type { TimelineBucket } from '../api/types'
import { fmtNum } from '../utils/format'

/** 近 24 小时请求趋势：CSS 柱状 + SVG 折线叠加（文字用正常 HTML 排版，不随 viewBox 拉伸） */

const H = 24
const GUTTER = 36

export default function LineChart({ buckets }: { buckets: TimelineBucket[] }) {
  const arr = new Array<number>(H).fill(0)
  const okArr = new Array<number>(H).fill(0)
  ;(buckets || []).forEach((b) => {
    const i = Math.min(H - 1, Math.max(0, Number(b.bucket) || 0))
    arr[i] += Number(b.n) || 0
    okArr[i] += Number(b.ok_n) || 0
  })
  const max = Math.max(1, ...arr)

  // 折线：viewBox 0 0 24 100 + preserveAspectRatio none，靠 non-scaling-stroke 保持线宽
  const linePts = arr
    .map((v, i) => `${i + 0.5},${(97 - (v / max) * 90).toFixed(2)}`)
    .join(' ')

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        {/* Y 轴刻度 */}
        <div style={{ width: GUTTER - 10, position: 'relative', marginRight: 10, flexShrink: 0 }}>
          {[1, 0.5, 0].map((r) => (
            <span
              key={r}
              style={{
                position: 'absolute',
                right: 0,
                bottom: `calc(${r * 100}% - 6px)`,
                fontSize: 11,
                lineHeight: '12px',
                color: '#5c6880',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {fmtNum(Math.round(max * r))}
            </span>
          ))}
        </div>

        {/* 绘图区 */}
        <div style={{ flex: 1, position: 'relative' }}>
          {[0, 0.5, 1].map((r) => (
            <div
              key={r}
              style={{
                position: 'absolute',
                left: 0,
                right: 0,
                bottom: `${r * 100}%`,
                borderTop: r === 0 ? '1px solid #2a3348' : '1px dashed #1d2434',
              }}
            />
          ))}
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'grid',
              gridTemplateColumns: `repeat(${H}, 1fr)`,
              alignItems: 'end',
            }}
          >
            {arr.map((v, i) => (
              <Tooltip key={i} mouseEnterDelay={0.15} title={`第 ${i + 1} 小时前：${v} 次请求 · 成功 ${okArr[i]}`}>
                <div style={{ height: '100%', display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
                  <div
                    style={{
                      width: '54%',
                      height: v > 0 ? `${Math.max(2, (v / max) * 96)}%` : 3,
                      borderRadius: '3px 3px 0 0',
                      background:
                        v > 0
                          ? 'linear-gradient(180deg, #8ba0ff 0%, rgba(84, 104, 232, 0.28) 100%)'
                          : 'rgba(126, 150, 255, 0.12)',
                      transition: 'height 0.35s ease',
                    }}
                  />
                </div>
              </Tooltip>
            ))}
          </div>
          <svg
            viewBox="0 0 24 100"
            preserveAspectRatio="none"
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', overflow: 'visible', pointerEvents: 'none' }}
          >
            <polyline
              points={linePts}
              fill="none"
              stroke="#b48cf2"
              strokeWidth="1.5"
              strokeLinejoin="round"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
              opacity="0.9"
            />
          </svg>
        </div>
      </div>

      {/* X 轴刻度 */}
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${H}, 1fr)`, paddingLeft: GUTTER }}>
        {arr.map((_, i) => (
          <div key={i} style={{ textAlign: 'center', fontSize: 11, color: '#5c6880' }}>
            {i % 4 === 0 ? `-${H - i}h` : ''}
          </div>
        ))}
      </div>
    </div>
  )
}
