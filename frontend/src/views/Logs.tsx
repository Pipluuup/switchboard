import { useEffect, useState } from 'react'
import { Card, Table, Button, Input, Select, Tag, App, Space, Modal } from 'antd'
import { SearchOutlined, ClearOutlined, LeftOutlined, RightOutlined } from '@ant-design/icons'
import { GET, DEL } from '../api/client'
import type { CavemanStats, LogDetail, LogItem, LogPage } from '../api/types'
import { fmtTime } from '../utils/format'

const PAGE_SIZE = 50

/** 请求体 / 响应体格式化：JSON 则美化缩进，否则原文展示 */
function pretty(s: string | undefined): string {
  if (!s) return ''
  try {
    return JSON.stringify(JSON.parse(s), null, 2)
  } catch {
    return s
  }
}

/** 解析压缩统计（caveman_stats 是 JSON 字符串） */
function parseStats(raw?: string): CavemanStats | null {
  if (!raw) return null
  try {
    return JSON.parse(raw) as CavemanStats
  } catch {
    return null
  }
}

/** 请求日志：对应旧版 loadLogs（筛选 / 搜索 / 分页 / 清空）+ 请求详情 */
export default function Logs() {
  const { message, modal } = App.useApp()
  const [items, setItems] = useState<LogItem[] | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [okFilter, setOkFilter] = useState<string>('')
  const [keyword, setKeyword] = useState('')
  const [kwQuery, setKwQuery] = useState('')
  const [detail, setDetail] = useState<LogDetail | null>(null)

  const load = () => {
    const q = `/api/logs?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}` +
      (okFilter !== '' ? `&ok=${okFilter}` : '') +
      (kwQuery ? `&keyword=${encodeURIComponent(kwQuery)}` : '')
    GET<LogPage>(q)
      .then((d) => {
        setItems(d.items || [])
        setTotal(d.total || 0)
      })
      .catch((e) => message.error('加载日志失败：' + e.message))
  }
  // kwQuery / okFilter 变化时回到第一页再加载
  useEffect(load, [message, page, okFilter, kwQuery])
  useEffect(() => { setPage(0) }, [okFilter, kwQuery])

  const openDetail = async (r: LogItem) => {
    try {
      setDetail(await GET<LogDetail>('/api/logs/' + r.id))
    } catch (e) {
      message.error('加载详情失败：' + (e as Error).message)
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <Card
      size="small"
      title="请求日志"
      extra={
        <Space wrap>
          <Select
            value={okFilter}
            onChange={setOkFilter}
            style={{ width: 100 }}
            options={[
              { value: '', label: '全部' },
              { value: '1', label: '仅成功' },
              { value: '0', label: '仅失败' },
            ]}
          />
          <Input
            placeholder="搜索模型 / 上游 / 错误"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onPressEnter={() => setKwQuery(keyword.trim())}
            style={{ width: 200 }}
            allowClear
          />
          <Button icon={<SearchOutlined />} onClick={() => setKwQuery(keyword.trim())}>搜索</Button>
          <Button
            danger
            icon={<ClearOutlined />}
            onClick={() =>
              modal.confirm({
                title: '清空全部请求日志？此操作不可恢复。',
                onOk: async () => { await DEL('/api/logs'); message.success('已清空'); setPage(0); setKwQuery(''); setKeyword(''); },
              })
            }
          >
            清空
          </Button>
        </Space>
      }
    >
      <Table<LogItem>
        size="small"
        rowKey="id"
        loading={!items}
        dataSource={items || []}
        pagination={false}
        scroll={{ x: 1240 }}
        columns={[
          { title: '时间', dataIndex: 'ts', width: 130, render: (v: number) => <span style={{ fontSize: 12, color: '#93a0b8', whiteSpace: 'nowrap' }}>{fmtTime(v)}</span> },
          { title: '状态', dataIndex: 'ok', width: 80, render: (v: boolean) => (v ? <Tag color="green">成功</Tag> : <Tag color="red">失败</Tag>) },
          { title: '模型', dataIndex: 'request_model', width: 160, ellipsis: true, render: (v: string) => <span className="mono" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>{v || '-'}</span> },
          { title: '上游', dataIndex: 'upstream_name', width: 140, render: (v: string) => <span style={{ fontSize: 12 }}>{v || '-'}</span> },
          { title: '客户端', dataIndex: 'client_name', width: 110, render: (v: string) => <span style={{ fontSize: 12 }}>{v || '-'}</span> },
          {
            title: '协议',
            dataIndex: 'out_protocol',
            width: 100,
            render: (v: string) => <Tag style={{ fontSize: 10.5 }} color={v === 'anthropic' ? 'purple' : 'blue'}>{v || '-'}</Tag>,
          },
          { title: 'Tokens 入/出', width: 110, render: (_: unknown, r: LogItem) => `${r.input_tokens || 0} / ${r.output_tokens || 0}` },
          {
            title: '优化',
            width: 110,
            render: (_: unknown, r: LogItem) => {
              const tags = []
              const st = parseStats(r.caveman_stats)
              if (r.caveman_saved) {
                tags.push(
                  <Tag key="c" color="cyan" style={{ fontSize: 10.5, marginInlineEnd: 4 }}>
                    瘦身 {(r.caveman_saved / 1000).toFixed(1)}k
                    {st ? ` ${st.saved_pct}%` : ''}
                  </Tag>,
                )
                if (st?.mode === 'cache-aware') {
                  tags.push(<Tag key="ca" color="gold" style={{ fontSize: 10.5, marginInlineEnd: 4 }}>缓存优先</Tag>)
                }
              }
              if (r.voice_injected) tags.push(<Tag key="v" color="geekblue" style={{ fontSize: 10.5, marginInlineEnd: 4 }}>Say less</Tag>)
              return tags.length ? tags : <span style={{ color: '#3a4356' }}>-</span>
            },
          },
          { title: '耗时', dataIndex: 'latency_ms', width: 90, render: (v: number) => v + ' ms' },
          {
            title: '错误',
            dataIndex: 'error',
            width: 200,
            ellipsis: true,
            render: (v: string) =>
              v ? (
                <span title={v} style={{ fontSize: 11.5, color: '#f2706b', display: 'block', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</span>
              ) : (
                '-'
              ),
          },
          {
            title: '详情',
            width: 60,
            render: (_: unknown, r: LogItem) => <Button size="small" type="text" onClick={() => openDetail(r)}>详情</Button>,
          },
        ]}
        locale={{ emptyText: '暂无日志' }}
      />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 12, paddingTop: 12, fontSize: 12.5, color: '#5c6880' }}>
        <Button size="small" icon={<LeftOutlined />} disabled={page <= 0} onClick={() => setPage((p) => p - 1)}>上一页</Button>
        <span>第 {page + 1} / {totalPages} 页 · 共 {total} 条</span>
        <Button size="small" disabled={page + 1 >= totalPages} onClick={() => setPage((p) => p + 1)}>下一页 <RightOutlined /></Button>
      </div>

      <Modal title="请求详情" open={!!detail} onCancel={() => setDetail(null)} footer={null} width={780}>
        {detail && (
          <div style={{ display: 'grid', gap: 12, fontSize: 12.5 }}>
            <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr', gap: '6px 10px' }}>
              <span style={{ color: '#5c6880' }}>时间</span><span>{fmtTime(detail.ts)}</span>
              <span style={{ color: '#5c6880' }}>模型</span><span className="mono">{detail.request_model}{detail.actual_model && detail.actual_model !== detail.request_model ? ` → ${detail.actual_model}` : ''}</span>
              <span style={{ color: '#5c6880' }}>上游</span><span>{detail.upstream_name || '-'}</span>
            </div>
            {parseStats(detail.caveman_stats) && (
              <div>
                <div style={{ color: '#5c6880', marginBottom: 4 }}>压缩统计</div>
                <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr', gap: '4px 10px', fontSize: 12 }}>
                  <span style={{ color: '#5c6880' }}>原始大小</span>
                  <span className="mono">{(parseStats(detail.caveman_stats)!.orig_chars / 1000).toFixed(1)}k 字符</span>
                  <span style={{ color: '#5c6880' }}>压缩后</span>
                  <span className="mono">{(parseStats(detail.caveman_stats)!.new_chars / 1000).toFixed(1)}k 字符</span>
                  <span style={{ color: '#5c6880' }}>节省</span>
                  <span className="mono">
                    {parseStats(detail.caveman_stats)!.saved_chars} 字符（
                    {parseStats(detail.caveman_stats)!.saved_pct}%）
                  </span>
                  <span style={{ color: '#5c6880' }}>模式</span>
                  <span>
                    {parseStats(detail.caveman_stats)!.mode === 'cache-aware' ? '缓存优先' : '常规'}
                    {parseStats(detail.caveman_stats)!.capabilities.length > 0 && (
                      <span style={{ color: '#5c6880' }}> · {parseStats(detail.caveman_stats)!.capabilities.join(' / ')}</span>
                    )}
                  </span>
                </div>
              </div>
            )}
            <div>
              <div style={{ color: '#5c6880', marginBottom: 4 }}>请求体（客户端 → 网关）</div>
              <pre className="code-block" style={{ maxHeight: 300, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
                {pretty(detail.request_body) || '（未记录——在设置页开启「记录请求/响应详情」后生效）'}
              </pre>
            </div>
            <div>
              <div style={{ color: '#5c6880', marginBottom: 4 }}>响应体（上游原始返回）</div>
              <pre className="code-block" style={{ maxHeight: 300, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
                {pretty(detail.response_body) || '（无）'}
              </pre>
            </div>
          </div>
        )}
      </Modal>
    </Card>
  )
}
