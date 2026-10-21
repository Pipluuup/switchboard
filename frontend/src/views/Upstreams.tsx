import { useEffect, useState } from 'react'
import { Card, Table, Button, Modal, Form, Input, Select, InputNumber, Tag, Tooltip, Segmented, App, Space, Typography, Checkbox, Upload, Alert } from 'antd'
import type { FormInstance } from 'antd'
import { PlusOutlined, ThunderboltOutlined, GiftOutlined, ImportOutlined } from '@ant-design/icons'
import { GET, POST, PUT, DEL } from '../api/client'
import type { Upstream, UpstreamInput, TestResult } from '../api/types'
import { fmtNum } from '../utils/format'

/** 上游管理：对应旧版 loadUpstreams + 编辑/测试/体检/模型勾选/重置/批量体检 */

type UpstreamFormValues = UpstreamInput & { models_text?: string }

/** 内置提供商：选中后自动填 Base URL 与 Key 申请页；「自定义」则全部手填 */
type Provider = {
  value: string
  label: string
  baseUrl: string
  keyUrl: string
  protocol?: 'anthropic' | 'openai'
  /** 反查用的域名片段（Base URL 命中即视为该提供商） */
  host: string
}

const PROVIDERS: Provider[] = [
  { value: 'commandcode', label: 'CommandCode（GOAT）', baseUrl: 'https://api.commandcode.ai/provider/v1', keyUrl: 'https://commandcode.ai/settings', protocol: 'openai', host: 'commandcode.ai' },
  { value: 'opencode-go', label: 'OpenCode Go / Zen', baseUrl: 'https://opencode.ai/inference/go/v1', keyUrl: 'https://opencode.ai/console', protocol: 'openai', host: 'opencode.ai' },
  { value: 'agnes', label: 'Agnes AI', baseUrl: 'https://api.agnes-ai.cn/v1', keyUrl: 'https://platform.agnes-ai.cn/login', protocol: 'openai', host: 'agnes-ai' },
  { value: 'sensenova', label: 'SenseNova（商汤）', baseUrl: 'https://token.sensenova.cn/v1', keyUrl: 'https://platform.sensenova.cn/login', protocol: 'openai', host: 'sensenova' },
  { value: 'deepseek', label: 'DeepSeek', baseUrl: 'https://api.deepseek.com/anthropic', keyUrl: 'https://platform.deepseek.com/api_keys', protocol: 'anthropic', host: 'deepseek' },
  { value: 'moonshot', label: 'Moonshot（Kimi）', baseUrl: 'https://api.moonshot.cn/v1', keyUrl: 'https://platform.moonshot.cn/console/api-keys', protocol: 'openai', host: 'moonshot' },
  { value: 'siliconflow', label: 'SiliconFlow', baseUrl: 'https://api.siliconflow.cn/v1', keyUrl: 'https://cloud.siliconflow.cn/account/ak', protocol: 'openai', host: 'siliconflow' },
  { value: 'volcengine', label: '火山方舟', baseUrl: 'https://ark.cn-beijing.volces.com/api/v3', keyUrl: 'https://console.volcengine.com/ark/region:cn-beijing/apiKey', protocol: 'openai', host: 'volces' },
  { value: 'dashscope', label: '阿里百炼', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', keyUrl: 'https://bailian.console.aliyun.com/?apiKey=1', protocol: 'openai', host: 'dashscope' },
  { value: 'openrouter', label: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1', keyUrl: 'https://openrouter.ai/keys', protocol: 'openai', host: 'openrouter' },
  { value: 'groq', label: 'Groq', baseUrl: 'https://api.groq.com/openai/v1', keyUrl: 'https://console.groq.com/keys', protocol: 'openai', host: 'groq' },
  { value: 'together', label: 'Together AI', baseUrl: 'https://api.together.xyz/v1', keyUrl: 'https://api.together.xyz/settings/api-keys', protocol: 'openai', host: 'together' },
  { value: 'zhipu', label: '智谱 GLM', baseUrl: 'https://open.bigmodel.cn/api/paas/v4', keyUrl: 'https://open.bigmodel.cn/usercenter/apikeys', protocol: 'openai', host: 'bigmodel.cn' },
  { value: 'baichuan', label: '百川智能', baseUrl: 'https://api.baichuan-ai.com/v1', keyUrl: 'https://platform.baichuan-ai.com/console/apikey', protocol: 'openai', host: 'baichuan-ai' },
  { value: 'minimax', label: 'MiniMax', baseUrl: 'https://api.minimax.chat/v1', keyUrl: 'https://platform.minimaxi.com/user-center/basic-information/interface-key', protocol: 'openai', host: 'minimax' },
  { value: 'qianfan', label: '百度千帆', baseUrl: 'https://qianfan.baidubce.com/v2', keyUrl: 'https://console.bce.baidu.com/qianfan/ais/console/apikey', protocol: 'openai', host: 'baidubce' },
  { value: 'stepfun', label: '阶跃星辰', baseUrl: 'https://api.stepfun.com/v1', keyUrl: 'https://platform.stepfun.com/console/api_keys', protocol: 'openai', host: 'stepfun' },
  { value: 'infini', label: '无问芯穹', baseUrl: 'https://open.bigmodel.cn/api/paas/v4', keyUrl: 'https://cloud.infini-ai.com/identity/token', protocol: 'openai', host: 'infini-ai' },
  { value: 'aihubmix', label: 'AiHubMix', baseUrl: 'https://aihubmix.com/v1', keyUrl: 'https://aihubmix.com/token', protocol: 'openai', host: 'aihubmix' },
  { value: 'oatify', label: 'Oatify', baseUrl: 'https://api.oatify.com/v1', keyUrl: 'https://oatify.com/dashboard', protocol: 'openai', host: 'oatify' },
  { value: 'nvidia', label: 'NVIDIA NIM', baseUrl: 'https://integrate.api.nvidia.com/v1', keyUrl: 'https://build.nvidia.com/keys', protocol: 'openai', host: 'nvidia' },
  { value: 'anthropic', label: 'Anthropic 官方', baseUrl: 'https://api.anthropic.com/v1', keyUrl: 'https://console.anthropic.com/settings/keys', protocol: 'anthropic', host: 'api.anthropic.com' },
  { value: 'openai', label: 'OpenAI 官方', baseUrl: 'https://api.openai.com/v1', keyUrl: 'https://platform.openai.com/api-keys', protocol: 'openai', host: 'api.openai.com' },
  { value: 'gemini', label: 'Google Gemini', baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', keyUrl: 'https://aistudio.google.com/app/apikey', protocol: 'openai', host: 'generativelanguage' },
  { value: 'mistral', label: 'Mistral', baseUrl: 'https://api.mistral.ai/v1', keyUrl: 'https://console.mistral.ai/api-keys', protocol: 'openai', host: 'mistral.ai' },
  { value: 'xai', label: 'xAI（Grok）', baseUrl: 'https://api.x.ai/v1', keyUrl: 'https://console.x.ai', protocol: 'openai', host: 'api.x.ai' },
  { value: 'cerebras', label: 'Cerebras', baseUrl: 'https://api.cerebras.ai/v1', keyUrl: 'https://cloud.cerebras.ai', protocol: 'openai', host: 'cerebras.ai' },
  { value: 'fireworks', label: 'Fireworks AI', baseUrl: 'https://api.fireworks.ai/inference/v1', keyUrl: 'https://fireworks.ai/api-keys', protocol: 'openai', host: 'fireworks.ai' },
  { value: 'anyrouter', label: 'AnyRouter', baseUrl: 'https://anyrouter.top/v1', keyUrl: 'https://anyrouter.top', protocol: 'openai', host: 'anyrouter' },
  { value: 'hf', label: 'HuggingFace', baseUrl: 'https://router.huggingface.co/v1', keyUrl: 'https://huggingface.co/settings/tokens', protocol: 'openai', host: 'huggingface.co' },
  { value: 'lmstudio', label: 'LM Studio（本地）', baseUrl: 'http://127.0.0.1:1234/v1', keyUrl: '', protocol: 'openai', host: '127.0.0.1:1234' },
  { value: 'ollama', label: 'Ollama（本地）', baseUrl: 'http://127.0.0.1:11434/v1', keyUrl: '', protocol: 'openai', host: '11434' },
  { value: 'custom', label: '自定义', baseUrl: '', keyUrl: '', host: '' },
]

const CUSTOM = 'custom'

/** 从 Base URL 反查所属提供商（编辑旧节点时用） */
function providerOf(baseUrl: string): Provider | null {
  const b = (baseUrl || '').toLowerCase()
  if (!b) return null
  return PROVIDERS.find((p) => p.host && b.includes(p.host)) || null
}

function UpstreamForm({ form, initial }: { form: FormInstance<UpstreamFormValues>; initial: Upstream | null }) {
  const { message } = App.useApp()
  const [probing, setProbing] = useState(false)
  const [probe, setProbe] = useState<{ open: boolean; models: string[]; selected: string[] }>({ open: false, models: [], selected: [] })
  // 提供商选择：新增时默认「自定义」，编辑时按 Base URL 反查
  const [provider, setProvider] = useState<string>(() => {
    if (!initial) return CUSTOM
    return providerOf(initial.base_url)?.value || CUSTOM
  })
  const [customKeyUrl, setCustomKeyUrl] = useState(initial?.key_url || '')
  // 生效的 Key 申请页：自定义 → 手填的；内置 → 提供商自带
  const [keyUrl, setKeyUrl] = useState(() => {
    if (initial?.key_url) return initial.key_url
    return providerOf(initial?.base_url || '')?.keyUrl || ''
  })

  const pickProvider = (v: string) => {
    setProvider(v)
    const p = PROVIDERS.find((x) => x.value === v)
    if (!p || v === CUSTOM) {
      // 切到自定义：把当前生效的网址带进表单继续编辑
      const cur = (form.getFieldValue('key_url') || customKeyUrl || keyUrl || '').trim()
      form.setFieldValue('key_url', cur)
      setCustomKeyUrl(cur)
      setKeyUrl(cur)
      return
    }
    form.setFieldsValue({
      base_url: p.baseUrl,
      key_url: p.keyUrl,
      protocol: p.protocol || form.getFieldValue('protocol') || 'openai',
    })
    setKeyUrl(p.keyUrl)
  }

  const probeModels = async () => {
    const v = form.getFieldsValue()
    if (!v.base_url) {
      message.warning('请先填写 Base URL')
      return
    }
    setProbing(true)
    try {
      const r = await POST<{ models: string[]; error: string }>('/api/upstreams/probe-models', {
        base_url: v.base_url,
        api_key: v.api_key || '',
        protocol: v.protocol || 'openai',
        upstream_id: initial?.id || '',
      })
      if (r.models.length) {
        // 先出预选面板：默认全选，勾选确认后再写入
        setProbe({ open: true, models: r.models, selected: r.models })
      } else {
        message.error(r.error || '未获取到模型')
      }
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setProbing(false)
    }
  }

  const confirmProbe = () => {
    form.setFieldValue('models_text', probe.selected.join(','))
    setProbe({ open: false, models: [], selected: [] })
    message.success(`已写入 ${probe.selected.length} 个模型`)
  }

  return (
    <>
    <Form form={form} layout="vertical" initialValues={initial ?? undefined}>
      <Space style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, width: '100%' }} align="start">
        <Form.Item name="name" label="名称" initialValue={initial?.name}>
          <Input placeholder="如：CommandCode Goat 主节点" />
        </Form.Item>
        <Form.Item label="提供商">
          <Select
            value={provider}
            onChange={pickProvider}
            showSearch
            allowClear={false}
            optionFilterProp="label"
            placeholder="选择提供商（可搜索）"
            options={PROVIDERS.map((p) => ({ value: p.value, label: p.label }))}
            notFoundContent="没有匹配的提供商，选「自定义」手填"
          />
        </Form.Item>
        <Form.Item name="protocol" label="协议" initialValue={initial?.protocol ?? 'anthropic'}>
          <Select
            options={[
              { value: 'anthropic', label: 'Anthropic（/v1/messages）' },
              { value: 'openai', label: 'OpenAI（/v1/chat/completions）' },
              { value: 'responses', label: 'OpenAI Responses（/v1/responses）' },
            ]}
          />
        </Form.Item>
      </Space>
      <Form.Item name="base_url" label="Base URL" initialValue={initial?.base_url} rules={[{ required: true, message: 'Base URL 不能为空' }]}>
        <Input placeholder="https://api.commandcode.dev/v1" />
      </Form.Item>
      {/* 仅「自定义」提供商时需要手填 Key 获取网址；内置提供商自带申请页 */}
      {provider === CUSTOM && (
        <Form.Item
          name="key_url"
          label="Key 获取网址（可留空；导出模板时会带上，方便对方自行申请）"
        >
          <Input
            placeholder="https://platform.example.com/api-keys"
            onChange={(e) => setCustomKeyUrl(e.target.value.trim())}
          />
        </Form.Item>
      )}
      <Form.Item
        name="api_key"
        label={
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
            API Key / Token
            <a
              onClick={(e) => {
                e.preventDefault()
                if (keyUrl) window.open(keyUrl, '_blank', 'noreferrer')
                else message.warning('该提供商没有配置 Key 获取网址——请在上方「Key 获取网址」里填一个')
              }}
              style={{ fontSize: 12, fontWeight: 400, cursor: 'pointer' }}
            >
              获取key
            </a>
          </span>
        }
        initialValue={initial?.api_key_masked === '未设置' ? '' : undefined}
      >
        <Input.Password placeholder={initial ? '留空保持原 Key 不变' : 'sk-...'} autoComplete="new-password" />
      </Form.Item>
      <Space style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, width: '100%' }} align="start">
        <Form.Item name="priority" label="优先级（越小越优先）" initialValue={initial?.priority ?? 100}>
          <InputNumber style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="weight" label="权重（同优先级内）" initialValue={initial?.weight ?? 1}>
          <InputNumber style={{ width: '100%' }} />
        </Form.Item>
      </Space>
      <Space style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, width: '100%' }} align="start">
        <Form.Item name="timeout" label="超时（秒）" initialValue={initial?.timeout ?? 180}>
          <InputNumber style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item name="max_retries" label="最大重试次数" initialValue={initial?.max_retries ?? 2}>
          <InputNumber style={{ width: '100%' }} />
        </Form.Item>
      </Space>
      <Form.Item label="支持的模型（逗号分隔，留空表示不限）">
        <Space.Compact style={{ width: '100%' }}>
          <Form.Item name="models_text" noStyle>
            <Input placeholder="claude-sonnet-4-5,claude-opus-4-1" />
          </Form.Item>
          <Button onClick={probeModels} loading={probing}>获取模型</Button>
        </Space.Compact>
      </Form.Item>
      <Form.Item name="note" label="备注" initialValue={initial?.note}>
        <Input />
      </Form.Item>
      <Form.Item name="enabled" label=" " valuePropName="checked" initialValue={initial ? initial.enabled : true}>
        <Checkbox>启用该节点</Checkbox>
      </Form.Item>
      </Form>
      <Modal
        title={probe.open ? `选择模型（已获取 ${probe.models.length} 个）` : ''}
        open={probe.open}
        onOk={confirmProbe}
        okText={`写入选中 ${probe.selected.length} 个`}
        okButtonProps={{ disabled: !probe.selected.length }}
        onCancel={() => setProbe({ open: false, models: [], selected: [] })}
        width={520}
      >
        <div style={{ display: 'flex', gap: 14, marginBottom: 8 }}>
          <Checkbox
            checked={probe.selected.length === probe.models.length}
            indeterminate={probe.selected.length > 0 && probe.selected.length < probe.models.length}
            onChange={(e) => setProbe((p) => ({ ...p, selected: e.target.checked ? p.models : [] }))}
          >
            全选
          </Checkbox>
          <Input
            size="small"
            placeholder="过滤…"
            style={{ width: 180 }}
            onChange={(e) => {
              const kw = e.target.value.toLowerCase()
              setProbe((p) => ({ ...p, selected: p.models.filter((m) => m.toLowerCase().includes(kw)) }))
            }}
          />
        </div>
        <div style={{ maxHeight: 340, overflow: 'auto', border: '1px solid #222938', borderRadius: 8, padding: '6px 10px' }}>
          {probe.models.map((m) => (
            <div key={m} style={{ padding: '3px 0' }}>
              <Checkbox
                checked={probe.selected.includes(m)}
                onChange={(e) =>
                  setProbe((p) => ({ ...p, selected: e.target.checked ? [...p.selected, m] : p.selected.filter((x) => x !== m) }))
                }
              >
                <span className="mono" style={{ fontSize: 12.5 }}>{m}</span>
              </Checkbox>
            </div>
          ))}
        </div>
      </Modal>
    </>
  )
}

function statusTag(u: Upstream) {
  if (u.in_cooldown) return <Tag color="red">冷却中</Tag>
  return u.healthy ? <Tag color="green">正常</Tag> : <Tag color="gold">异常</Tag>
}

/** 免费节点：名称或备注里带 free 标记（默认内置，不可删除） */
const isFree = (u: Upstream) => /free/i.test((u.name || '') + ' ' + (u.note || ''))

export default function Upstreams() {
  const { message } = App.useApp()
  const [list, setList] = useState<Upstream[] | null>(null)
  const [listFilter, setListFilter] = useState<'all' | 'free' | 'custom'>('all')
  const [editing, setEditing] = useState<Upstream | null>(null)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm<UpstreamFormValues>()
  const [testResult, setTestResult] = useState<{ name: string; r: TestResult } | null>(null)
  const [modelsOf, setModelsOf] = useState<Upstream | null>(null)
  const [modelOptions, setModelOptions] = useState<string[]>([])
  const [modelSel, setModelSel] = useState<string[]>([])
  const [imported, setImported] = useState(false)
  const [importedKeyUrl, setImportedKeyUrl] = useState('')

  const load = () => {
    GET<Upstream[]>('/api/upstreams')
      .then(setList)
      .catch((e) => message.error('加载上游失败：' + e.message))
  }
  useEffect(load, [message])

  const openEdit = (u: Upstream | null) => {
    if (u) {
      setEditing(u)
      form.setFieldsValue({
        name: u.name,
        protocol: u.protocol,
        base_url: u.base_url,
        api_key: '',
        priority: u.priority,
        weight: u.weight,
        timeout: u.timeout,
        max_retries: u.max_retries,
        models_text: (u.models || []).join(','),
        note: u.note,
        key_url: u.key_url || providerOf(u.base_url)?.keyUrl || '',
        enabled: u.enabled,
      })
    } else {
      setCreating(true)
      form.resetFields()
    }
  }

  const closeEdit = () => {
    setEditing(null)
    setCreating(false)
    setImported(false)
    setImportedKeyUrl('')
  }

  /** 导出上游为分享模板：含 Base URL / 模型 / 备注（方便对方去拿 Key），不含 API Key */
  const exportTpl = (u: Upstream) => {
    const tpl = {
      template: 'switchboard-upstream',
      version: 1,
      name: u.name.replace(/\(free\)/i, '').trim() || u.name,
      protocol: u.protocol,
      base_url: u.base_url,
      priority: u.priority,
      weight: u.weight,
      timeout: u.timeout,
      max_retries: u.max_retries,
      models: u.models || [],
      note: u.note || '',
      key_url: u.key_url || '',
    }
    const blob = new Blob([JSON.stringify(tpl, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `switchboard-upstream-${u.name.replace(/[\\/:*?"<>| ]+/g, '-')}.json`
    a.click()
    URL.revokeObjectURL(a.href)
    message.success('模板已导出（不含 API Key）')
  }

  /** 导入模板：预填新增表单，Key 留空由导入者自行填写 */
  const importTpl = async (file: File) => {
    try {
      const t = JSON.parse(await file.text())
      if (t.template !== 'switchboard-upstream') throw new Error('不是 Switchboard 上游模板（template 字段不符）')
      closeEdit()
      setCreating(true)
      setImported(true)
      setImportedKeyUrl(t.key_url || '')
      form.setFieldsValue({
        name: t.name || '',
        protocol: t.protocol || 'anthropic',
        base_url: t.base_url || '',
        api_key: '',
        priority: t.priority ?? 100,
        weight: t.weight ?? 1,
        timeout: t.timeout ?? 180,
        max_retries: t.max_retries ?? 2,
        models_text: (t.models || []).join(','),
        note: t.note || '',
        key_url: t.key_url || '',
        enabled: true,
      })
      message.success(
        `已导入模板「${t.name || '未命名'}」，请填写 API Key` +
          (t.key_url ? `（申请地址：${t.key_url}）` : ''),
      )
    } catch (e) {
      message.error('导入失败：' + (e as Error).message)
    }
    return false
  }

  const submitEdit = async () => {
    const v = await form.validateFields()
    const data = {
      name: v.name || '未命名上游',
      protocol: v.protocol,
      base_url: (v.base_url || '').replace(/\/+$/, ''),
      api_key: v.api_key || '',
      priority: Number(v.priority) || 100,
      weight: Number(v.weight) || 1,
      timeout: Number(v.timeout) || 180,
      max_retries: Number(v.max_retries) || 0,
      models: v.models_text ? v.models_text.split(',').map((s: string) => s.trim()).filter(Boolean) : [],
      note: v.note || '',
      // key_url：内置提供商由 pickProvider 写入 store，自定义由用户填写
      key_url: (v.key_url !== undefined ? v.key_url : editing?.key_url) || '',
      enabled: !!v.enabled,
    }
    try {
      if (editing) await PUT('/api/upstreams/' + editing.id, data)
      else await POST('/api/upstreams', data)
      message.success('已保存')
      closeEdit()
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const testUp = async (u: Upstream) => {
    message.warning({ content: '正在测试连通性…', key: 'test-' + u.id, duration: 30 })
    try {
      const r = await POST<TestResult>('/api/upstreams/' + u.id + '/test', { model: (u.models || [])[0] || '' })
      message.destroy('test-' + u.id)
      setTestResult({ name: u.name, r })
    } catch (e) {
      message.destroy('test-' + u.id)
      message.error('测试失败：' + (e as Error).message)
    }
  }

  const healthUp = async (u: Upstream) => {
    try {
      const r = await POST<{ ok: boolean; latency_ms?: number; error?: string; status?: number }>('/api/upstreams/' + u.id + '/health')
      if (r.ok) message.success(`${u.name} 健康（${r.latency_ms}ms）`)
      else message.error(`${u.name} 异常：${r.error || r.status}`, 5)
      load()
    } catch (e) {
      message.error('体检失败：' + (e as Error).message)
    }
  }

  const pickModels = async (u: Upstream) => {
    message.warning({ content: '正在拉取模型列表…', key: 'models-' + u.id, duration: 15 })
    try {
      const r = await GET<{ models: string[] }>('/api/upstreams/' + u.id + '/models')
      message.destroy('models-' + u.id)
      if (!r.models.length) {
        message.error('未获取到模型，请检查 Key 与 Base URL', 4)
        return
      }
      setModelsOf(u)
      setModelOptions(r.models)
      setModelSel(u.models || [])
    } catch (e) {
      message.destroy('models-' + u.id)
      message.error('拉取失败：' + (e as Error).message)
    }
  }

  const saveModels = async () => {
    if (!modelsOf) return
    try {
      await PUT('/api/upstreams/' + modelsOf.id, { models: modelSel })
      message.success(`已更新，选中 ${modelSel.length} 个模型`)
      setModelsOf(null)
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const healthAll = async () => {
    message.warning('正在体检全部节点…')
    try {
      const r = await POST<{ ok: boolean; name: string }[]>('/api/upstreams/health-all')
      const bad = r.filter((x) => x.ok === false)
      if (!bad.length) message.success('全部节点正常')
      else message.error(`${bad.length} 个节点异常：${bad.map((x) => x.name).join('、')}`, 6)
      load()
    } catch (e) {
      message.error('体检失败：' + (e as Error).message)
    }
  }

  return (
    <Card
      size="small"
      title="上游节点"
      extra={
        <Space>
          <Segmented
            size="small"
            value={listFilter}
            onChange={(v) => setListFilter(v as 'all' | 'free' | 'custom')}
            options={[
              { label: '全部', value: 'all' },
              { label: 'Free', value: 'free' },
              { label: '自定义', value: 'custom' },
            ]}
          />
          <Button icon={<ThunderboltOutlined />} onClick={healthAll}>批量体检</Button>
          <Upload accept=".json,application/json" showUploadList={false} beforeUpload={(f) => importTpl(f as unknown as File)}>
            <Button icon={<ImportOutlined />}>导入模板</Button>
          </Upload>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openEdit(null)}>新增上游</Button>
        </Space>
      }
    >
      <Table<Upstream>
        size="small"
        rowKey="id"
        loading={!list}
        dataSource={list ? [...list].filter((u) => listFilter === 'all' || (listFilter === 'free') === isFree(u)).sort((a, b) => Number(isFree(b)) - Number(isFree(a)) || a.name.localeCompare(b.name)) : []}
        pagination={false}
        scroll={{ x: 1320 }}
        columns={[
          {
            title: '名称',
            dataIndex: 'name',
            width: 220,
            ellipsis: true,
            render: (_: unknown, u: Upstream) => (
              <>
                <div style={{ fontWeight: 500 }}>
                  {u.name}
                  {isFree(u) && (
                    <Tooltip title="免费节点">
                      <GiftOutlined style={{ marginLeft: 6, color: '#45c98a', fontSize: 13 }} />
                    </Tooltip>
                  )}
                </div>
                <div style={{ fontSize: 11, color: '#5c6880' }}>{u.note}</div>
              </>
            ),
          },
          {
            title: 'Base URL',
            dataIndex: 'base_url',
            width: 170,
            ellipsis: true,
            render: (v: string) => <span className="mono" style={{ fontSize: 11.5 }}>{v}</span>,
          },
          {
            title: '协议',
            dataIndex: 'protocol',
            width: 100,
            render: (v: string) => <Tag color={v === 'anthropic' ? 'purple' : 'blue'}>{v}</Tag>,
          },
          {
            title: 'API Key',
            dataIndex: 'api_key_masked',
            width: 170,
            ellipsis: true,
            render: (v: string) => (
              <span className="mono" style={{ fontSize: 11.5, whiteSpace: 'nowrap' }}>{v || '未设置'}</span>
            ),
          },
          { title: '优先级', dataIndex: 'priority', width: 64 },
          {
            title: '状态',
            width: 120,
            render: (_: unknown, u: Upstream) => (
              <Space size={4}>{statusTag(u)}{u.enabled ? <Tag color="green">启用</Tag> : <Tag color="default">停用</Tag>}</Space>
            ),
          },
          {
            title: '统计',
            width: 140,
            render: (_: unknown, u: Upstream) => (
              <span style={{ fontSize: 11.5, color: '#93a0b8' }}>
                {fmtNum(u.total_requests)} 次 / 失败 {fmtNum(u.failed_requests)}
                <br />
                <span style={{ color: '#5c6880' }}>{u.last_latency_ms ? u.last_latency_ms + 'ms' : '-'}</span>
              </span>
            ),
          },
          {
            title: '最近错误',
            dataIndex: 'last_error',
            width: 180,
            ellipsis: true,
            render: (v: string) => v ? <Typography.Text type="danger" style={{ fontSize: 11.5 }} ellipsis={{ tooltip: v }}>{v}</Typography.Text> : <span style={{ color: '#5c6880' }}>-</span>,
          },
          {
            title: '操作',
            width: 340,
            fixed: 'right',
            render: (_: unknown, u: Upstream) => (
              <Space size={2} wrap>
                <Button size="small" type="text" onClick={() => openEdit(u)}>编辑</Button>
                <Button size="small" type="text" onClick={() => testUp(u)}>测试</Button>
                <Button size="small" type="text" onClick={() => healthUp(u)}>体检</Button>
                <Button size="small" type="text" onClick={() => pickModels(u)}>模型</Button>
                <Tooltip title="导出分享模板（不含 API Key，含 Base URL）">
                  <Button size="small" type="text" onClick={() => exportTpl(u)}>导出</Button>
                </Tooltip>
                <Button
                  size="small"
                  type="text"
                  onClick={() => POST('/api/upstreams/' + u.id + '/reset').then(() => { message.success('已重置节点状态'); load() })}
                >
                  重置
                </Button>
                {isFree(u) ? (
                  <Tooltip title="内置免费节点，不可删除（可编辑/停用）">
                    <Button size="small" type="text" danger disabled>删除</Button>
                  </Tooltip>
                ) : (
                  <PopconfirmDelete name={u.name} onConfirm={async () => { await DEL('/api/upstreams/' + u.id); message.success('已删除'); load() }} />
                )}
              </Space>
            ),
          },
        ]}
        locale={{ emptyText: listFilter === 'free' ? '没有免费节点（名称或备注带 free）' : '还没有上游节点，点击右上角「新增上游」' }}
      />
      <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginTop: 10 }}>
        优先级数字越小越优先；连续失败达到阈值后节点进入冷却并自动切换到下一个可用节点。
      </Typography.Paragraph>

      <Modal
        title={editing ? '编辑上游' : imported ? '导入模板' : '新增上游'}
        open={creating || !!editing}
        onOk={submitEdit}
        onCancel={closeEdit}
        okText="保存"
        width={620}
        destroyOnClose
      >
        {imported && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12, fontSize: 12 }}
            message={
              <span>
                已按模板预填，API Key 需自行填写。
                {importedKeyUrl && (
                  <>
                    {' '}获取 Key：
                    <a href={importedKeyUrl} target="_blank" rel="noreferrer">{importedKeyUrl} ↗</a>
                  </>
                )}
              </span>
            }
          />
        )}
        <UpstreamForm form={form} initial={editing} />
      </Modal>

      <Modal title={testResult ? '测试结果 · ' + testResult.name : ''} open={!!testResult} onCancel={() => setTestResult(null)} footer={null} width={680}>
        {testResult && (testResult.r.ok ? (
          <div style={{ display: 'grid', gridTemplateColumns: '84px 1fr', gap: '8px 12px', fontSize: 13 }}>
            <span style={{ color: '#5c6880' }}>结果</span><span><Tag color="green">连通成功</Tag></span>
            <span style={{ color: '#5c6880' }}>耗时</span><span>{testResult.r.latency_ms} ms</span>
            <span style={{ color: '#5c6880' }}>模型</span><span className="mono">{testResult.r.model}</span>
            <span style={{ color: '#5c6880' }}>响应</span>
            <span><div className="code-block">{(testResult.r.preview || '').slice(0, 500)}</div></span>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: '84px 1fr', gap: '8px 12px', fontSize: 13 }}>
            <span style={{ color: '#5c6880' }}>结果</span><span><Tag color="red">失败</Tag></span>
            <span style={{ color: '#5c6880' }}>状态码</span><span>{testResult.r.status || '-'}</span>
            <span style={{ color: '#5c6880' }}>错误</span>
            <span><div className="code-block">{testResult.r.error || '未知错误'}</div></span>
            <span />
            <span style={{ fontSize: 12, color: '#5c6880' }}>
              请检查 Base URL、API Key 与协议是否匹配。若上游是 OpenAI 格式却选了 Anthropic，就会出现 404。
            </span>
          </div>
        ))}
      </Modal>

      <Modal
        title={modelsOf ? '可用模型 · ' + modelsOf.name : ''}
        open={!!modelsOf}
        onOk={saveModels}
        onCancel={() => setModelsOf(null)}
        okText="保存"
        width={680}
      >
        <p style={{ fontSize: 12, color: '#5c6880' }}>勾选后点击保存，将写入该上游的「支持模型」字段（用于调度筛选）。</p>
        <div style={{ maxHeight: 320, overflow: 'auto', border: '1px solid #222938', borderRadius: 8, padding: 10 }}>
          {modelOptions.map((m) => (
            <div key={m} style={{ padding: '4px 0' }}>
              <Checkbox checked={modelSel.includes(m)} onChange={(e) => setModelSel(e.target.checked ? [...modelSel, m] : modelSel.filter((x) => x !== m))}>
                <span className="mono">{m}</span>
              </Checkbox>
            </div>
          ))}
        </div>
        <p style={{ fontSize: 12, color: '#5c6880' }}>共 {modelOptions.length} 个模型。</p>
      </Modal>
    </Card>
  )
}

function PopconfirmDelete({ name, onConfirm }: { name: string; onConfirm: () => void }) {
  const { modal } = App.useApp()
  return (
    <Button size="small" type="text" danger onClick={() => modal.confirm({ title: `确定删除上游「${name}」？`, onOk: onConfirm })}>
      删除
    </Button>
  )
}
