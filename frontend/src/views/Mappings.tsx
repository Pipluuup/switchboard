import { useEffect, useState } from 'react'
import { Card, Table, Button, Modal, Form, Input, AutoComplete, Select, Tag, App, Space, Typography, Checkbox, Tooltip } from 'antd'
import { PlusOutlined, GiftOutlined } from '@ant-design/icons'
import { GET, POST, PUT, DEL } from '../api/client'
import type { ChainHop, Mapping, MappingInput, Upstream } from '../api/types'

/** 模型映射：对应旧版 loadMappings + editMapping；带故障转移链编辑 */
const isFree = (u: Upstream) => /free/i.test((u.name || '') + ' ' + (u.note || ''))

/** 模型候选共用组装逻辑：同名模型只列一次，标签列出拥有它的上游（免费/优先级靠前在前） */
function buildModelOptions(pool: Upstream[]) {
  const byModel = new Map<string, { names: string[]; free: boolean; prio: number }>()
  for (const u of [...pool].sort((a, b) => Number(isFree(b)) - Number(isFree(a)) || a.priority - b.priority)) {
    for (const m of u.models || []) {
      const e = byModel.get(m)
      if (e) {
        e.names.push(u.name)
        e.free = e.free || isFree(u)
        e.prio = Math.min(e.prio, u.priority)
      } else {
        byModel.set(m, { names: [u.name], free: isFree(u), prio: u.priority })
      }
    }
  }
  return [...byModel.entries()]
    .sort((a, b) => Number(b[1].free) - Number(a[1].free) || a[1].prio - b[1].prio)
    .map(([m, e]) => ({
      value: m,
      searchText: m,
      label: (
        <Space size={6}>
          <span className="mono">{m}</span>
          <span style={{ fontSize: 11, color: '#5c6880' }}>
            {e.names[0]}{e.names.length > 1 ? ` 等 ${e.names.length} 个上游` : ''}
          </span>
          {e.free && <GiftOutlined style={{ color: '#45c98a', fontSize: 12 }} />}
        </Space>
      ),
    }))
}

/** 上游下拉候选：免费节点排前 */
function upstreamOptions(upstreams: Upstream[]) {
  return [
    { value: '', label: '自动调度' },
    ...[...upstreams]
      .sort((a, b) => Number(isFree(b)) - Number(isFree(a)))
      .map((u) => ({
        value: u.id,
        label: (
          <Space size={6}>
            {u.name}
            {isFree(u) && (
              <Tooltip title="免费节点">
                <GiftOutlined style={{ color: '#45c98a', fontSize: 12 }} />
              </Tooltip>
            )}
          </Space>
        ),
      })),
  ]
}

/** 新链的空白模板：默认三档 */
const EMPTY_CHAIN: ChainHop[] = [
  { upstream_id: '', target: '' },
  { upstream_id: '', target: '' },
  { upstream_id: '', target: '' },
]

export default function Mappings() {
  const { message, modal } = App.useApp()
  const [list, setList] = useState<Mapping[] | null>(null)
  const [upstreams, setUpstreams] = useState<Upstream[]>([])
  const [editing, setEditing] = useState<Mapping | null>(null)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm<MappingInput>()

  const openEditor = async (m: Mapping | null) => {
    try {
      setUpstreams(await GET<Upstream[]>('/api/upstreams'))
    } catch (e) {
      message.error((e as Error).message)
      return
    }
    if (m) {
      setEditing(m)
      // 老式映射（无 chain）退化成单档进编辑器，补足 3 档空位；保存时会写回完整链
      const chain = [...(m.chain?.length ? m.chain : [{ upstream_id: m.upstream_id || '', target: m.target }])]
      while (chain.length < 3) chain.push({ upstream_id: '', target: '' })
      form.setFieldsValue({ alias: m.alias, note: m.note, enabled: m.enabled, chain })
    } else {
      setCreating(true)
      form.resetFields()
      form.setFieldsValue({ enabled: true, chain: EMPTY_CHAIN })
    }
  }

  const close = () => {
    setEditing(null)
    setCreating(false)
  }

  const submit = async () => {
    const v = await form.validateFields()
    // 去掉整档全空（既没上游也没模型）的档；链上不空就写回老字段（第 1 档镜像）
    const chain = (v.chain || [])
      .map((h) => ({ upstream_id: (h.upstream_id || '').trim(), target: (h.target || '').trim() }))
      .filter((h) => h.upstream_id || h.target)
    if (!chain.length) {
      message.error('至少要配一档：选上游或填模型')
      return
    }
    const payload: MappingInput = {
      alias: v.alias,
      target: chain[0].target,
      upstream_id: chain[0].upstream_id,
      chain,
      note: v.note,
      enabled: v.enabled,
    }
    try {
      if (editing) await PUT('/api/mappings/' + editing.id, payload)
      else await POST('/api/mappings', payload)
      message.success('已保存')
      close()
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const load = () => {
    GET<Mapping[]>('/api/mappings')
      .then(setList)
      .catch((e) => message.error('加载映射失败：' + e.message))
  }
  // 首次进入拉取列表；message 来自 App.useApp()，是稳定引用
  useEffect(load, [message])
  /** 表格里的链展示：每档一行「模型 @ 上游」；无 chain 的老映射退化单档 */
  const renderChain = (m: Mapping) => {
    const hops = m.chain?.length ? m.chain : [{ upstream_id: m.upstream_id || '', target: m.target, upstream_name: m.upstream_name || '' }]
    return (
      <div style={{ display: 'grid', gap: 2 }}>
        {hops.map((h, i) => (
          <span key={i} style={{ fontSize: 11.5, whiteSpace: 'nowrap' }}>
            <span style={{ color: '#5c6880', display: 'inline-block', width: 30 }}>第{i + 1}档</span>
            <span className="mono">{h.target || '(空)'}</span>
            <span style={{ color: '#8a94a8' }}>  @ {h.upstream_name || (h.upstream_id ? '' : '自动调度')}</span>
          </span>
        ))}
      </div>
    )
  }

  return (
    <Card
      size="small"
      title="模型别名映射"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor(null)}>
          新增映射
        </Button>
      }
    >
      <Table<Mapping>
        size="small"
        rowKey="id"
        loading={!list}
        dataSource={list || []}
        pagination={false}
        columns={[
          { title: '请求别名', dataIndex: 'alias', render: (v: string) => <span className="mono">{v}</span> },
          { title: '', width: 40, render: () => <span style={{ color: '#5c6880' }}>→</span> },
          {
            title: '故障转移链',
            render: (_: unknown, m: Mapping) => renderChain(m),
          },
          {
            title: '状态',
            dataIndex: 'enabled',
            width: 90,
            render: (v: boolean) => (v ? <Tag color="green">启用</Tag> : <Tag color="default">停用</Tag>),
          },
          { title: '备注', dataIndex: 'note', render: (v: string) => <span style={{ fontSize: 12, color: '#5c6880' }}>{v}</span> },
          {
            title: '操作',
            width: 140,
            render: (_: unknown, m: Mapping) => (
              <Space size={2}>
                <Button size="small" type="text" onClick={() => openEditor(m)}>编辑</Button>
                <Button
                  size="small"
                  type="text"
                  danger
                  onClick={() => modal.confirm({ title: `删除映射「${m.alias}」？`, onOk: async () => { await DEL('/api/mappings/' + m.id); message.success('已删除'); load() } })}
                >
                  删除
                </Button>
              </Space>
            ),
          },
        ]}
        locale={{ emptyText: '还没有映射，点击右上角「新增映射」' }}
      />
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 10 }}>
        客户端请求 <code>别名</code> 时，按链从上到下依次尝试：第 1 档失败（5xx / 429 / 404 / 网络错误）就切下一档，
        400/401 这类请求本身的错误不切。档里留空上游 = 按调度策略自动选择；留空模型 = 沿用请求的原始模型名。
      </Typography.Paragraph>

      <Modal
        title={editing ? '编辑映射' : '新增映射'}
        open={creating || !!editing}
        onOk={submit}
        onCancel={close}
        okText="保存"
        width={640}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="alias" label="请求别名（客户端传来的模型名）" rules={[{ required: true, message: '别名不能为空' }]}>
            <Input placeholder="claude-sonnet-4-5" />
          </Form.Item>
          <Form.List name="chain">
            {(fields) => (
              <>
                {fields.map((field) => {
                  const uid = form.getFieldValue(['chain', field.name, 'upstream_id']) as string
                  const pool = uid ? upstreams.filter((u) => u.id === uid) : upstreams
                  return (
                    <div key={field.key}>
                      <div style={{ fontSize: 12, color: '#5c6880', marginBottom: 4 }}>
                        第 {field.name + 1} 档
                        {field.name === 0 ? '（首选）' : '（前面失败后才尝试）'}
                      </div>
                      <div style={{ display: 'flex', gap: 8 }}>
                        <Form.Item name={[field.name, 'upstream_id']} noStyle style={{ width: '42%' }}>
                          <Select
                            style={{ width: '42%', flexShrink: 0 }}
                            options={upstreamOptions(upstreams)}
                            onChange={() => {
                              // 换上游后该档模型多半不在新上游的候选里，清掉重选
                              form.setFieldValue(['chain', field.name, 'target'], '')
                            }}
                          />
                        </Form.Item>
                        <Form.Item name={[field.name, 'target']} noStyle>
                          <AutoComplete
                            style={{ flex: 1, minWidth: 0 }}
                            options={buildModelOptions(pool)}
                            filterOption={(input, option) =>
                              String((option as { searchText?: string }).searchText || '').toLowerCase().includes(input.toLowerCase())
                            }
                            placeholder="模型（留空 = 沿用请求的原模型名）"
                          />
                        </Form.Item>
                      </div>
                    </div>
                  )
                })}
              </>
            )}
          </Form.List>
          <Form.Item name="note" label="备注">
            <Input />
          </Form.Item>
          <Form.Item name="enabled" label=" " valuePropName="checked" initialValue>
            <Checkbox>启用</Checkbox>
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
