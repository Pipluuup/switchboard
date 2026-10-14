import { useEffect, useMemo, useState } from 'react'
import { Card, Table, Button, Modal, Form, Input, AutoComplete, Select, Tag, App, Space, Typography, Checkbox, Tooltip } from 'antd'
import { PlusOutlined, GiftOutlined } from '@ant-design/icons'
import { GET, POST, PUT, DEL } from '../api/client'
import type { Mapping, MappingInput, Upstream } from '../api/types'

/** 模型映射：对应旧版 loadMappings + editMapping */

const isFree = (u: Upstream) => /free/i.test((u.name || '') + ' ' + (u.note || ''))

export default function Mappings() {
  const { message, modal } = App.useApp()
  const [list, setList] = useState<Mapping[] | null>(null)
  const [upstreams, setUpstreams] = useState<Upstream[]>([])
  const [editing, setEditing] = useState<Mapping | null>(null)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm<MappingInput>()
  const selUpstreamId = Form.useWatch('upstream_id', form)

  // 目标模型候选 = 所选上游的白名单；「自动调度」时取全部上游模型合集
  // 同名模型在多个上游都有时只列一次，标签列出全部拥有它的上游（免费/优先级靠前的在前）
  const targetOptions = useMemo(() => {
    const pool = selUpstreamId ? upstreams.filter((u) => u.id === selUpstreamId) : upstreams
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
  }, [selUpstreamId, upstreams])

  // 切换上游后若目标模型不在新候选里，清空提示重选
  useEffect(() => {
    const cur = form.getFieldValue('target')
    if (cur && targetOptions.length && !targetOptions.some((o) => o.value === cur)) {
      form.setFieldValue('target', '')
    }
  }, [selUpstreamId]) // eslint-disable-line react-hooks/exhaustive-deps

  const load = () => {
    GET<Mapping[]>('/api/mappings')
      .then(setList)
      .catch((e) => message.error('加载映射失败：' + e.message))
  }
  useEffect(load, [message])

  const openEditor = async (m: Mapping | null) => {
    try {
      setUpstreams(await GET<Upstream[]>('/api/upstreams'))
    } catch (e) {
      message.error((e as Error).message)
      return
    }
    if (m) {
      setEditing(m)
      form.setFieldsValue({
        alias: m.alias,
        target: m.target,
        upstream_id: m.upstream_id || '',
        note: m.note,
        enabled: m.enabled,
      })
    } else {
      setCreating(true)
      form.resetFields()
    }
  }

  const close = () => {
    setEditing(null)
    setCreating(false)
  }

  const submit = async () => {
    const v = await form.validateFields()
    try {
      if (editing) await PUT('/api/mappings/' + editing.id, v)
      else await POST('/api/mappings', v)
      message.success('已保存')
      close()
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
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
          { title: '目标模型', dataIndex: 'target', render: (v: string) => <span className="mono">{v}</span> },
          {
            title: '固定上游',
            dataIndex: 'upstream_name',
            render: (v: string) => v || <span style={{ color: '#5c6880' }}>自动调度</span>,
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
        客户端请求 <code>别名</code> 时，实际用 <code>目标模型</code> 请求上游；可指定固定走某个上游，留空则按调度策略自动选择。
      </Typography.Paragraph>

      <Modal
        title={editing ? '编辑映射' : '新增映射'}
        open={creating || !!editing}
        onOk={submit}
        onCancel={close}
        okText="保存"
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="alias" label="请求别名（客户端传来的模型名）" rules={[{ required: true, message: '别名不能为空' }]}>
            <Input placeholder="claude-sonnet-4-5" />
          </Form.Item>
          <Form.Item name="target" label="目标模型（实际发给上游）">
            <AutoComplete
              options={targetOptions}
              filterOption={(input, option) =>
                String((option as { searchText?: string }).searchText || '').toLowerCase().includes(input.toLowerCase())
              }
              placeholder="先选上游，再从下拉选模型（也可手输任意值）"
            />
          </Form.Item>
          <Form.Item name="upstream_id" label="固定上游" initialValue="">
            <Select
              options={[
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
              ]}
            />
          </Form.Item>
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
