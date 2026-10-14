import { useEffect, useState } from 'react'
import { Card, Table, Button, Modal, Form, Input, InputNumber, Select, Tag, App, Space, Typography, Checkbox } from 'antd'
import { PlusOutlined, CopyOutlined } from '@ant-design/icons'
import { GET, POST, PUT, DEL } from '../api/client'
import type { ClientKey, ClientKeyInput, Upstream } from '../api/types'
import { fmtNum, fmtAgo } from '../utils/format'

/** 客户端 Key：对应旧版 loadKeys + editKey（复制/重置密钥/删除） */

type KeyFormValues = ClientKeyInput & { models_text?: string }

export default function Keys() {
  const { message, modal } = App.useApp()
  const [list, setList] = useState<ClientKey[] | null>(null)
  const [upstreams, setUpstreams] = useState<Upstream[]>([])
  const [editing, setEditing] = useState<ClientKey | null>(null)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm<KeyFormValues>()

  const load = () => {
    GET<ClientKey[]>('/api/keys')
      .then(setList)
      .catch((e) => message.error('加载密钥失败：' + e.message))
  }
  useEffect(load, [message])

  const openEditor = async (k: ClientKey | null) => {
    try {
      setUpstreams(await GET<Upstream[]>('/api/upstreams'))
    } catch (e) {
      message.error((e as Error).message)
      return
    }
    if (k) {
      setEditing(k)
      form.setFieldsValue({
        name: k.name,
        rate_limit_rpm: k.rate_limit_rpm,
        daily_token_limit: k.daily_token_limit,
        models_text: (k.allowed_models || []).join(','),
        upstream_id: k.upstream_id || '',
        note: k.note,
        enabled: k.enabled,
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
    const data = {
      name: v.name || '未命名',
      rate_limit_rpm: Number(v.rate_limit_rpm) || 0,
      daily_token_limit: Number(v.daily_token_limit) || 0,
      allowed_models: v.models_text ? v.models_text.split(',').map((s: string) => s.trim()).filter(Boolean) : [],
      upstream_id: v.upstream_id || '',
      note: v.note || '',
      enabled: !!v.enabled,
    }
    try {
      if (editing) await PUT('/api/keys/' + editing.id, data)
      else await POST('/api/keys', data)
      message.success('已保存')
      close()
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const copyKey = async (k: ClientKey) => {
    try {
      await navigator.clipboard.writeText(k.key)
      message.success('已复制到剪贴板')
    } catch {
      message.warning('复制失败，请手动复制：' + k.key, 6)
    }
  }

  return (
    <Card
      size="small"
      title="客户端密钥"
      extra={
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor(null)}>
          新增 Key
        </Button>
      }
    >
      <Table<ClientKey>
        size="small"
        rowKey="id"
        loading={!list}
        dataSource={list || []}
        pagination={false}
        scroll={{ x: 1000 }}
        columns={[
          {
            title: '名称',
            dataIndex: 'name',
            render: (_: unknown, k: ClientKey) => (
              <>
                <div style={{ fontWeight: 500 }}>{k.name}</div>
                <div style={{ fontSize: 11, color: '#5c6880' }}>{k.note}</div>
              </>
            ),
          },
          {
            title: 'Key',
            dataIndex: 'masked',
            width: 220,
            render: (_: unknown, k: ClientKey) => (
              <Space size={6}>
                <span className="mono">{k.masked}</span>
                <Button size="small" type="text" icon={<CopyOutlined />} onClick={() => copyKey(k)} />
              </Space>
            ),
          },
          {
            title: '状态',
            dataIndex: 'enabled',
            width: 90,
            render: (v: boolean) => (v ? <Tag color="green">启用</Tag> : <Tag color="default">停用</Tag>),
          },
          {
            title: '速率',
            dataIndex: 'rate_limit_rpm',
            width: 100,
            render: (v: number) => (v ? v + ' /分钟' : <span style={{ color: '#5c6880' }}>不限</span>),
          },
          {
            title: '每日额度',
            dataIndex: 'daily_token_limit',
            width: 150,
            render: (v: number, k: ClientKey) => (
              <>
                {v ? fmtNum(v) : <span style={{ color: '#5c6880' }}>不限</span>}
                <div style={{ fontSize: 11, color: '#5c6880' }}>今日已用 {fmtNum(k.used_today)}</div>
              </>
            ),
          },
          {
            title: '允许模型',
            dataIndex: 'allowed_models',
            render: (v: string[]) =>
              v && v.length ? (
                <Space size={4}>{v.slice(0, 2).map((x) => <Tag key={x} color="blue">{x}</Tag>)}</Space>
              ) : (
                <span style={{ color: '#5c6880' }}>全部</span>
              ),
          },
          {
            title: '用量',
            width: 130,
            render: (_: unknown, k: ClientKey) => (
              <span style={{ fontSize: 12, color: '#93a0b8' }}>
                {fmtNum(k.total_requests)} 次
                <br />
                <span style={{ color: '#5c6880' }}>{fmtAgo(k.last_used_at)}</span>
              </span>
            ),
          },
          {
            title: '操作',
            width: 220,
            render: (_: unknown, k: ClientKey) => (
              <Space size={2}>
                <Button size="small" type="text" onClick={() => openEditor(k)}>编辑</Button>
                <Button
                  size="small"
                  type="text"
                  onClick={() =>
                    modal.confirm({
                      title: `重置「${k.name}」的密钥？旧密钥将立即失效。`,
                      onOk: async () => { await POST('/api/keys/' + k.id + '/regenerate'); message.success('已重置密钥'); load() },
                    })
                  }
                >
                  重置密钥
                </Button>
                <Button
                  size="small"
                  type="text"
                  danger
                  onClick={() =>
                    modal.confirm({
                      title: `删除「${k.name}」？`,
                      onOk: async () => { await DEL('/api/keys/' + k.id); message.success('已删除'); load() },
                    })
                  }
                >
                  删除
                </Button>
              </Space>
            ),
          },
        ]}
        locale={{ emptyText: '还没有密钥，点击右上角「新增 Key」' }}
      />
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 10 }}>
        为每个本地工具（Claude Code、其他 IDE 插件）分发独立 Key，可单独限速、限模型、停用。留空允许模型即表示不限制。
      </Typography.Paragraph>

      <Modal
        title={editing ? '编辑 Key' : '新增 Key'}
        open={creating || !!editing}
        onOk={submit}
        onCancel={close}
        okText="保存"
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" initialValue={editing?.name}>
            <Input placeholder="如：VS Code Claude Code" />
          </Form.Item>
          <Form.Item name="rate_limit_rpm" label="速率限制（次/分钟，0 = 不限）" initialValue={editing?.rate_limit_rpm ?? 0}>
            <InputNumber style={{ width: '100%' }} min={0} />
          </Form.Item>
          <Form.Item name="daily_token_limit" label="每日 Token 额度（0 = 不限）" initialValue={editing?.daily_token_limit ?? 0}>
            <InputNumber style={{ width: '100%' }} min={0} />
          </Form.Item>
          <Form.Item name="models_text" label="允许的模型（逗号分隔，留空 = 全部）" initialValue={editing ? (editing.allowed_models || []).join(',') : ''}>
            <Input />
          </Form.Item>
          <Form.Item name="upstream_id" label="绑定上游" initialValue={editing?.upstream_id || ''}>
            <Select
              options={[{ value: '', label: '自动调度' }, ...upstreams.map((u) => ({ value: u.id, label: u.name }))]}
            />
          </Form.Item>
          <Form.Item name="note" label="备注" initialValue={editing?.note}>
            <Input />
          </Form.Item>
          <Form.Item name="enabled" label=" " valuePropName="checked" initialValue={editing ? editing.enabled : true}>
            <Checkbox>启用</Checkbox>
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}
