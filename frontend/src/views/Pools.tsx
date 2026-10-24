import { useEffect, useState } from 'react'
import {
  Row, Col, Card, Button, Tag, Space, App, Modal, Form, Input, InputNumber,
  Select, Switch, Typography, Alert, Empty, Progress,
} from 'antd'
import {
  PlusOutlined, TeamOutlined, DeleteOutlined, ThunderboltOutlined, ClusterOutlined,
} from '@ant-design/icons'
import { GET, POST, PUT, DEL } from '../api/client'
import type { AccountPool, PooledAccount, UpstreamProtocol } from '../api/types'

/** 账号池：同一家上游服务的多份凭证，按顺序轮换（优先第一个，用尽换下一个） */

const KIND_LABELS: Record<string, string> = {
  workbuddy: 'WorkBuddy AI',
  multikey: '多 Key 轮换',
}

const PROTOCOLS: { value: UpstreamProtocol; label: string }[] = [
  { value: 'openai', label: 'OpenAI Chat' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'responses', label: 'OpenAI Responses' },
  { value: 'gemini', label: 'Gemini' },
]

const fmtTime = (t: number) => (t ? new Date(t * 1000).toLocaleString() : '—')

export default function Pools() {
  const { message, modal } = App.useApp()
  const [pools, setPools] = useState<AccountPool[]>([])
  const [poolForm] = Form.useForm()
  const [acctForm] = Form.useForm()
  const [poolEditing, setPoolEditing] = useState<AccountPool | null>(null)
  const [poolCreating, setPoolCreating] = useState(false)
  const [acctTarget, setAcctTarget] = useState<{ pool: AccountPool; editing: PooledAccount | null } | null>(null)
  const [probe, setProbe] = useState<Record<string, { ok: boolean; status: number; error: string }>>({})

  const load = () => {
    GET<AccountPool[]>('/api/pools')
      .then(setPools)
      .catch((e) => message.error('加载账号池失败：' + e.message))
  }
  useEffect(load, [message])

  // ---------------- 池编辑 ----------------
  const openPoolEditor = (p: AccountPool | null) => {
    if (p) {
      setPoolEditing(p)
      poolForm.setFieldsValue(p)
    } else {
      setPoolCreating(true)
      poolForm.resetFields()
    }
  }
  const closePoolEditor = () => {
    setPoolEditing(null)
    setPoolCreating(false)
  }

  const submitPool = async () => {
    const v = await poolForm.validateFields()
    try {
      if (poolEditing) await PUT('/api/pools/' + poolEditing.id, v)
      else await POST('/api/pools', v)
      message.success('已保存，池组上游节点已同步')
      closePoolEditor()
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const removePool = (p: AccountPool) => {
    modal.confirm({
      title: `删除账号池「${p.name}」？`,
      content: '池里的账号配置会被删除；自动创建的池组上游节点会保留，可自行在上游管理中处理。',
      okText: '删除',
      okButtonProps: { danger: true },
      onOk: async () => {
        await DEL('/api/pools/' + p.id)
        message.success('已删除')
        load()
      },
    })
  }

  // ---------------- 账号编辑 ----------------
  const openAcctEditor = (pool: AccountPool, a: PooledAccount | null) => {
    setAcctTarget({ pool, editing: a })
    acctForm.resetFields()
    if (a) acctForm.setFieldsValue(a)
  }

  const submitAcct = async () => {
    if (!acctTarget) return
    const v = await acctForm.validateFields()
    const key = (v.api_key || '').trim()
    const cookie = (v.cookie || '').trim()
    if (!key && !cookie) {
      message.error('API Key 与 Cookie 至少填一个')
      return
    }
    const { pool: p, editing } = acctTarget
    const accounts = p.accounts.map((a) => (a.id === editing?.id ? { ...a, ...v } : a))
    try {
      if (!editing) {
        accounts.push({
          id: '', label: v.label || '', api_key: key, cookie,
          enabled: true, usage_used: 0, usage_limit: Number(v.usage_limit) || 0,
          note: v.note || '', last_used_at: 0, total_requests: 0,
          failed_requests: 0, last_error: '',
        })
      }
      await PUT('/api/pools/' + p.id, { accounts })
      message.success('已保存')
      setAcctTarget(null)
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const toggleAcct = async (p: AccountPool, a: PooledAccount, enabled: boolean) => {
    try {
      await PUT('/api/pools/' + p.id, {
        accounts: p.accounts.map((x) => (x.id === a.id ? { ...x, enabled } : x)),
      })
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const removeAcct = (p: AccountPool, a: PooledAccount) => {
    modal.confirm({
      title: `删除账号「${a.label || a.id}」？`,
      okText: '删除',
      okButtonProps: { danger: true },
      onOk: async () => {
        await PUT('/api/pools/' + p.id, { accounts: p.accounts.filter((x) => x.id !== a.id) })
        message.success('已删除')
        load()
      },
    })
  }

  const probePool = async (p: AccountPool) => {
    const hide = message.loading('正在逐账号探活…', 0)
    try {
      const r = await POST<{ items: { account_id: string; ok: boolean; status: number; error: string }[] }>(
        '/api/pools/' + p.id + '/probe', {})
      const next: Record<string, { ok: boolean; status: number; error: string }> = {}
      for (const it of r.items) next[it.account_id] = it
      setProbe(next)
      const bad = r.items.filter((x) => !x.ok).length
      if (bad) message.warning(`探活完成：${r.items.length - bad} 可用 / ${bad} 失败`)
      else message.success(`探活完成：${r.items.length} 个账号全部可用`)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      hide()
    }
  }

  /** 额度条：usage_limit=0 表示未配上限 */
  const quotaBar = (a: PooledAccount) => {
    if (!a.usage_limit) {
      return <span style={{ fontSize: 11, color: '#5c6880' }}>已用 {a.usage_used} · 未配上限</span>
    }
    const pct = Math.min(100, Math.round((a.usage_used / a.usage_limit) * 100))
    return (
      <Space size={6}>
        <Progress percent={pct} size="small" style={{ width: 110, marginBottom: 0 }} showInfo={false} />
        <span style={{ fontSize: 11, color: '#93a0b8' }}>{a.usage_used} / {a.usage_limit}</span>
      </Space>
    )
  }

  const probeTag = (a: PooledAccount) => {
    const r = probe[a.id]
    if (!r) return null
    return r.ok
      ? <Tag color="green" style={{ marginInlineEnd: 0 }}>探活 200</Tag>
      : <Tag color="red" style={{ marginInlineEnd: 0 }} title={r.error}>探活 {r.status || '网络异常'}</Tag>
  }

  return (
    <Row gutter={[14, 14]}>
      <Col xs={24}>
        <Alert
          type="info"
          showIcon
          style={{ fontSize: 12 }}
          message={
            <span>
              同一家上游服务的多枚账号放进一个池，网关<b>优先用第一个账号</b>；
              凭证失效（401/403）自动停用、前面额度用完自动换下一个。
              创建池时会在「上游管理」同步生成一个带 <TeamOutlined /> 徽章的同名池组节点。
            </span>
          }
        />
      </Col>

      <Col xs={24}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openPoolEditor(null)}>
          新建账号池
        </Button>
      </Col>

      {pools.length === 0 && (
        <Col xs={24}>
          <Card size="small">
            <Empty description="还没有账号池" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </Card>
        </Col>
      )}

      {pools.map((p) => {
        const active = p.accounts.filter((a) => a.enabled).length
        return (
          <Col xs={24} key={p.id}>
            <Card
              size="small"
              title={
                <Space size={8}>
                  <TeamOutlined style={{ color: '#d9a84e' }} />
                  {p.name}
                  <Tag color="gold" style={{ marginInlineEnd: 0 }}>{KIND_LABELS[p.kind] || p.kind}</Tag>
                  <Tag style={{ marginInlineEnd: 0 }}>{PROTOCOLS.find((x) => x.value === p.protocol)?.label || p.protocol}</Tag>
                  <Tag color={active ? 'green' : 'red'} style={{ marginInlineEnd: 0 }}>
                    {active}/{p.accounts.length} 可用
                  </Tag>
                </Space>
              }
              extra={
                <Space size={4}>
                  <Button size="small" icon={<ThunderboltOutlined />} onClick={() => probePool(p)}>探活</Button>
                  <Button size="small" type="text" onClick={() => openPoolEditor(p)}>编辑</Button>
                  <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removePool(p)} />
                </Space>
              }
            >
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <div style={{ fontSize: 12, color: '#93a0b8', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                  <span>Base URL：<span className="mono">{p.base_url || '（未配置）'}</span></span>
                  {p.upstream_id && (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      池组节点：
                      <a
                        onClick={() => { location.hash = '#/upstreams' }}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
                      >
                        <ClusterOutlined /> {p.name}（{p.upstream_id}）
                      </a>
                    </span>
                  )}
                  {p.notes && <span>{p.notes}</span>}
                </div>

                {p.accounts.length === 0 ? (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    还没有账号——点下面「添加另一个账号」录入第一枚凭证。
                  </Typography.Text>
                ) : (
                  <Space direction="vertical" size={6} style={{ width: '100%' }}>
                    {p.accounts.map((a) => (
                      <div
                        key={a.id}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                          padding: '7px 10px', borderRadius: 7,
                          border: '1px solid #232a3a', background: '#11151f',
                          opacity: a.enabled ? 1 : 0.55,
                        }}
                      >
                        <Switch size="small" checked={a.enabled} onChange={(v) => toggleAcct(p, a, v)} />
                        <div style={{ minWidth: 160, flex: '0 1 auto' }}>
                          <div style={{ fontSize: 12.5 }}>
                            {a.label || <span style={{ color: '#5c6880' }}>（未命名）</span>}
                            {a.cookie && <Tag style={{ marginInlineStart: 6 }}>Cookie</Tag>}
                            {a.api_key && !a.cookie && <Tag style={{ marginInlineStart: 6 }}>Key</Tag>}
                          </div>
                          <div style={{ fontSize: 11, color: '#5c6880' }}>
                            最近使用 {fmtTime(a.last_used_at)} · 共 {a.total_requests} 次
                            {a.failed_requests > 0 && (
                              <span style={{ color: '#e07b53' }}> · 失败 {a.failed_requests}{a.last_error ? `（${a.last_error}）` : ''}</span>
                            )}
                          </div>
                        </div>
                        <div style={{ flex: 1, minWidth: 140 }}>{quotaBar(a)}</div>
                        {probeTag(a)}
                        <Space size={0}>
                          <Button size="small" type="text" onClick={() => openAcctEditor(p, a)}>编辑</Button>
                          <Button size="small" type="text" danger onClick={() => removeAcct(p, a)}>删除</Button>
                        </Space>
                      </div>
                    ))}
                  </Space>
                )}

                <div>
                  <Button size="small" icon={<PlusOutlined />} onClick={() => openAcctEditor(p, null)}>
                    添加另一个账号
                  </Button>
                </div>
              </Space>
            </Card>
          </Col>
        )
      })}

      {/* 池编辑 */}
      <Modal
        title={poolEditing ? '编辑账号池' : '新建账号池'}
        open={poolCreating || !!poolEditing}
        onOk={submitPool}
        onCancel={closePoolEditor}
        okText="保存"
        destroyOnClose
      >
        <Form form={poolForm} layout="vertical" initialValues={{ kind: 'workbuddy', protocol: 'anthropic', auto_upstream: true }}>
          <Form.Item name="name" label="池名称" rules={[{ required: true, message: '名称不能为空' }]}>
            <Input placeholder="如：WorkBuddy AI（国际版）" />
          </Form.Item>
          <Form.Item name="kind" label="类型">
            <Select options={Object.entries(KIND_LABELS).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL（池背后服务的公共地址）">
            <Input placeholder="如：https://api.workbuddy.ai/v1" />
          </Form.Item>
          <Form.Item name="protocol" label="协议">
            <Select options={PROTOCOLS} />
          </Form.Item>
          <Form.Item name="notes" label="备注">
            <Input placeholder="可选" />
          </Form.Item>
          <Form.Item name="auto_upstream" label="自动维护池组上游节点" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      {/* 账号编辑 */}
      <Modal
        title={acctTarget?.editing ? '编辑账号' : '添加账号'}
        open={!!acctTarget}
        onOk={submitAcct}
        onCancel={() => setAcctTarget(null)}
        okText="保存"
        destroyOnClose
      >
        <Form form={acctForm} layout="vertical">
          <Form.Item name="label" label="展示名（一般是邮箱）">
            <Input placeholder="如：user@example.com" />
          </Form.Item>
          <Form.Item name="api_key" label="API Key（多 Key 轮换型填这里）">
            <Input.Password placeholder="留空则必须填 Cookie" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="cookie" label="Cookie（WorkBuddy 等登录即授权型填这里）">
            <Input.Password placeholder="如：__Host-console_session=…" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="usage_limit" label="额度上限（0 = 不设上限，仅本地记账）">
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="note" label="备注">
            <Input placeholder="可选" />
          </Form.Item>
        </Form>
      </Modal>
    </Row>
  )
}
