import { useEffect, useState } from 'react'
import { Card, Table, Button, Form, Input, Select, Tag, App, Space, Typography, Modal, Alert, Row, Col } from 'antd'
import { PlusOutlined, LinkOutlined, UndoOutlined } from '@ant-design/icons'
import { GET, POST, PUT, DEL } from '../api/client'
import type { ClientSetup, ClientProfile } from '../api/types'

/** 客户端一键接入：像 ccswitch 一样改写客户端配置指向本服务 */

const CLIENT_LABELS: Record<string, string> = {
  claude: 'Claude Code',
  codex: 'Codex CLI',
  gemini: 'Gemini CLI',
  qwen: 'Qwen Code',
}

export default function Clients() {
  const { message, modal } = App.useApp()
  const [data, setData] = useState<{ clients: ClientSetup[]; profiles: ClientProfile[]; base_url: string } | null>(null)
  const [keys, setKeys] = useState<{ id: string; name: string; masked: string }[]>([])
  const [models, setModels] = useState<string[]>([])
  const [editing, setEditing] = useState<ClientProfile | null>(null)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm<ClientProfile>()

  const load = () => {
    GET<{ clients: ClientSetup[]; profiles: ClientProfile[]; base_url: string }>('/api/client-setups')
      .then(setData)
      .catch((e) => message.error('加载客户端状态失败：' + e.message))
    GET<{ id: string; name: string; masked: string }[]>('/api/keys')
      .then(setKeys)
      .catch(() => {})
    // 模型候选 = 模型映射的别名（客户端请求这些名字，网关负责映射到上游）
    GET<{ alias: string }[]>('/api/mappings')
      .then((ms) => setModels([...new Set(ms.map((m) => m.alias).filter(Boolean))]))
      .catch(() => {})
  }
  useEffect(load, [message])

  const openEditor = (p: ClientProfile | null) => {
    if (p) {
      setEditing(p)
      form.setFieldsValue(p)
    } else {
      setCreating(true)
      form.resetFields()
    }
  }
  const closeEditor = () => {
    setEditing(null)
    setCreating(false)
  }

  const submit = async () => {
    const v = await form.validateFields()
    try {
      if (editing) await PUT('/api/client-setups/' + editing.id, v)
      else await POST('/api/client-setups', v)
      message.success('已保存')
      closeEditor()
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const apply = (client: string, profileId: string) => {
    modal.confirm({
      title: '改写客户端配置？',
      content: (
        <div style={{ fontSize: 12.5, lineHeight: 1.7 }}>
          将改写 <code>{CLIENT_LABELS[client]}</code> 的配置文件，使其指向本服务。
          <br />
          原文件会先备份到 <code>data/client_backups/</code>，可随时一键还原。
        </div>
      ),
      okText: '应用接入',
      onOk: async () => {
        try {
          const r = await POST<{ message: string }>('/api/client-setups/apply', { client, profile_id: profileId })
          message.success(r.message || '已应用')
          load()
        } catch (e) {
          message.error((e as Error).message)
          throw e
        }
      },
    })
  }

  const restore = (client: string) => {
    modal.confirm({
      title: '还原客户端配置？',
      content: '将用最近一次备份覆盖当前配置文件。',
      okText: '还原',
      onOk: async () => {
        try {
          const r = await POST<{ message: string; ok: boolean }>('/api/client-setups/restore', { client })
          if (r.ok) message.success(r.message)
          else message.warning(r.message)
          load()
        } catch (e) {
          message.error((e as Error).message)
        }
      },
    })
  }

  if (!data) return null

  return (
    <Row gutter={[14, 14]}>
      <Col xs={24}>
        <Alert
          type="info"
          showIcon
          style={{ fontSize: 12 }}
          message={
            <span>
              一键把本地 Agent 客户端指向本服务（机制同 ccswitch）：改写客户端配置文件，
              切换前自动备份到 <code>data/client_backups/</code>，随时可还原。
              当前网关地址：<code>{data.base_url}</code>
            </span>
          }
        />
      </Col>

      {data.clients.map((c) => {
        const profiles = data.profiles.filter((p) => p.client === c.client)
        return (
          <Col xs={24} lg={12} key={c.client}>
            <Card
              size="small"
              title={
                <Space size={8}>
                  {c.label}
                  {c.attached ? <Tag color="green">已接入本服务</Tag> : <Tag color="default">未接入</Tag>}
                </Space>
              }
              extra={
                <Space size={4}>
                  {c.has_backup && (
                    <Button size="small" type="text" icon={<UndoOutlined />} onClick={() => restore(c.client)}>
                      还原
                    </Button>
                  )}
                </Space>
              }
            >
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <div style={{ fontSize: 12, color: '#93a0b8' }}>
                  <div>配置文件：<span className="mono">{c.config_path}</span>{!c.exists && '（不存在）'}</div>
                  {c.base_url && <div>当前 Base URL：<span className="mono">{c.base_url}</span></div>}
                  {c.model && <div>当前模型：<span className="mono">{c.model}</span></div>}
                  <div>{c.hint}</div>
                </div>

                {profiles.length === 0 ? (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    还没有档位——先在下面新建一个（选客户端 Key + 模型）。
                  </Typography.Text>
                ) : (
                  <Space direction="vertical" size={6} style={{ width: '100%' }}>
                    {profiles.map((p) => (
                      <div
                        key={p.id}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8,
                          padding: '7px 10px', borderRadius: 7,
                          border: '1px solid #232a3a', background: '#11151f',
                        }}
                      >
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 12.5 }}>{p.name}</div>
                          <div style={{ fontSize: 11, color: '#5c6880' }}>
                            {p.client_key_name || '未绑定 Key'}
                            {p.model ? ` · ${p.model}` : ''}
                          </div>
                        </div>
                        <Button size="small" type="primary" icon={<LinkOutlined />} onClick={() => apply(p.client, p.id)}>
                          应用
                        </Button>
                      </div>
                    ))}
                  </Space>
                )}
              </Space>
            </Card>
          </Col>
        )
      })}

      <Col xs={24}>
        <Card
          size="small"
          title="接入档位"
          extra={
            <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor(null)}>
              新增档位
            </Button>
          }
        >
          <Table<ClientProfile>
            size="small"
            rowKey="id"
            loading={!data}
            dataSource={data.profiles}
            pagination={false}
            scroll={{ x: 760 }}
            columns={[
              { title: '名称', dataIndex: 'name', width: 180 },
              {
                title: '客户端',
                dataIndex: 'client',
                width: 130,
                render: (v: string) => CLIENT_LABELS[v] || v,
              },
              {
                title: '客户端 Key',
                dataIndex: 'client_key_name',
                width: 160,
                render: (v: string) => v || <span style={{ color: '#5c6880' }}>未绑定</span>,
              },
              {
                title: '模型',
                dataIndex: 'model',
                width: 160,
                render: (v: string) => (v ? <span className="mono">{v}</span> : <span style={{ color: '#5c6880' }}>-</span>),
              },
              {
                title: '操作',
                width: 150,
                render: (_: unknown, p: ClientProfile) => (
                  <Space size={2}>
                    <Button size="small" type="text" onClick={() => openEditor(p)}>编辑</Button>
                    <Button
                      size="small"
                      type="text"
                      danger
                      onClick={() => modal.confirm({ title: `删除档位「${p.name}」？`, onOk: async () => { await DEL('/api/client-setups/' + p.id); message.success('已删除'); load() } })}
                    >
                      删除
                    </Button>
                  </Space>
                ),
              },
            ]}
            locale={{ emptyText: '还没有档位' }}
          />
        </Card>
      </Col>

      <Modal
        title={editing ? '编辑档位' : '新增档位'}
        open={creating || !!editing}
        onOk={submit}
        onCancel={closeEditor}
        okText="保存"
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '名称不能为空' }]}>
            <Input placeholder="如：默认Agent → 免费通道" />
          </Form.Item>
          <Form.Item name="client" label="客户端" initialValue="claude">
            <Select
              options={Object.entries(CLIENT_LABELS).map(([value, label]) => ({ value, label }))}
            />
          </Form.Item>
          <Form.Item name="client_key_id" label="客户端 Key">
            <Select
              placeholder="选一个网关的客户端 Key"
              options={keys.map((k) => ({ value: k.id, label: `${k.name}（${k.masked}）` }))}
            />
          </Form.Item>
          <Form.Item name="model" label="模型（写进客户端的模型配置，留空则不改）">
            <Select
              showSearch
              allowClear
              placeholder="选网关里的模型别名，如 Free"
              options={models.map((m) => ({ value: m, label: m }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Row>
  )
}
