import { useEffect, useState } from 'react'
import { Row, Col, Card, Button, InputNumber, Switch, App, Space, Typography, Alert, Tag, Modal } from 'antd'
import { SaveOutlined, QuestionCircleOutlined } from '@ant-design/icons'
import { GET, PUT } from '../api/client'
import type { Settings } from '../api/types'

/** Token 优化：Caveman 引擎（Read less，输入瘦身）+ Say less（voice，输出瘦身）+ RTK（客户端命令瘦身） */

type Draft = {
  caveman_enabled: boolean
  caveman_keep_recent: number
  caveman_min_chars: number
  caveman_compress_system: boolean
  voice_enabled: boolean
}

type RtkStatus = { installed: boolean; version: string; path?: string; error?: string }

function RtkCard() {
  const { message } = App.useApp()
  const [st, setSt] = useState<RtkStatus | null>(null)
  const [helpOpen, setHelpOpen] = useState(false)

  useEffect(() => {
    GET<RtkStatus>('/api/rtk/status').then(setSt).catch(() => setSt({ installed: false, version: '' }))
  }, [])

  const copy = async (t: string) => {
    try {
      await navigator.clipboard.writeText(t)
      message.success('已复制')
    } catch {
      message.error('复制失败')
    }
  }

  const code = (text: string) => (
    <div className="code-block" style={{ position: 'relative', fontSize: 12 }}>
      {text}
      <button
        className="copy-btn"
        onClick={() => copy(text)}
        style={{
          position: 'absolute', top: 5, right: 5, fontSize: 11, padding: '1px 7px',
          borderRadius: 5, border: '1px solid #303950', background: '#11151f',
          color: '#93a0b8', cursor: 'pointer',
        }}
      >
        复制
      </button>
    </div>
  )

  return (
    <Card
      size="small"
      title={
        <Space size={6}>
          RTK（客户端命令瘦身）
          <Button
            size="small"
            type="text"
            icon={<QuestionCircleOutlined />}
            onClick={() => setHelpOpen(true)}
            title="安装方法"
          />
        </Space>
      }
    >
      <Space direction="vertical" size={13} style={{ width: '100%' }}>
        <div>
          {st === null ? (
            <Tag>检测中…</Tag>
          ) : st.installed ? (
            <Tag color="green">已安装{st.version ? ` · ${st.version}` : ''}</Tag>
          ) : (
            <Tag color="default">未安装</Tag>
          )}
        </div>
        <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0, lineHeight: 1.7 }}>
          RTK（Rust Token Killer）在本机把 <code>git</code> / <code>npm</code> / 测试等命令输出压缩
          60–90%：客户端通过 hook 把 <code>git status</code> 自动改写为 <code>rtk git status</code>，
          Agent 读到的就是瘦身后的结果。它作用于客户端命令执行，与网关侧的 Read less /
          Say less 互补——两边一起开效果最好。
        </Typography.Paragraph>
        <div>
          <div style={{ fontSize: 12, color: '#93a0b8', marginBottom: 4 }}>本机验证命令</div>
          <div className="code-block" style={{ position: 'relative' }}>
            rtk --version            <button
              className="copy-btn"
              onClick={() => copy('rtk --version')}
              style={{
                position: 'absolute', top: 6, right: 6, fontSize: 11, padding: '2px 8px',
                borderRadius: 5, border: '1px solid #303950', background: '#11151f',
                color: '#93a0b8', cursor: 'pointer',
              }}
            >
              复制
            </button>
          </div>
        </div>
        <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
          安装方式点标题旁的 <QuestionCircleOutlined /> 查看；装好后刷新本页即可检测到。
        </Typography.Text>
      </Space>

      <Modal title="安装 RTK" open={helpOpen} onCancel={() => setHelpOpen(false)} footer={null} width={560}>
        <Space direction="vertical" size={13} style={{ width: '100%', fontSize: 12.5 }}>
          <div>
            <div style={{ marginBottom: 4 }}>Windows（本机推荐）</div>
            {code('winget install rtk-ai.rtk')}
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>macOS / Linux</div>
            {code('brew install rtk')}
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>无包管理器时（安装脚本）</div>
            {code("curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh")}
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>安装后初始化（自动配置 Claude Code 等 Agent 的 hook）</div>
            {code('rtk init --global')}
          </div>
          <div>
            <div style={{ marginBottom: 4 }}>验证</div>
            {code('rtk --version && rtk gain')}
          </div>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
            注意：crates.io 上有另一个同名项目，认准 <code>rtk-ai</code> 官方源——
            <code>rtk gain</code> 能打开节省面板即为正确版本。
            详情见 <a href="https://github.com/rtk-ai/rtk" target="_blank" rel="noreferrer">github.com/rtk-ai/rtk ↗</a>。
          </Typography.Paragraph>
          <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
            装好后回到本页刷新，上方状态会自动变为「已安装」。
          </Typography.Text>
        </Space>
      </Modal>
    </Card>
  )
}

export default function TokenOpt() {
  const { message } = App.useApp()
  const [draft, setDraft] = useState<Draft | null>(null)

  useEffect(() => {
    GET<Settings>('/api/settings')
      .then((s) =>
        setDraft({
          caveman_enabled: !!s.caveman_enabled,
          caveman_keep_recent: s.caveman_keep_recent || 4,
          caveman_min_chars: s.caveman_min_chars || 400,
          caveman_compress_system: !!s.caveman_compress_system,
          voice_enabled: !!s.voice_enabled,
        }),
      )
      .catch((e) => message.error('加载设置失败：' + e.message))
  }, [message])

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => (d ? { ...d, [k]: v } : d))

  const save = async () => {
    if (!draft) return
    try {
      await PUT('/api/settings', {
        caveman_enabled: draft.caveman_enabled,
        caveman_keep_recent: Number(draft.caveman_keep_recent) || 4,
        caveman_min_chars: Number(draft.caveman_min_chars) || 400,
        caveman_compress_system: draft.caveman_compress_system,
        voice_enabled: draft.voice_enabled,
      })
      message.success('已保存')
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  if (!draft) return null

  return (
    <Row gutter={[14, 14]}>
      <Col xs={24}>
        <Card
          size="small"
          title="Caveman 引擎 · Read less（输入瘦身）"
          extra={
            <Switch
              checked={draft.caveman_enabled}
              checkedChildren="已启动"
              unCheckedChildren="未启动"
              onChange={(v) => set('caveman_enabled', v)}
            />
          }
        >
          <Space direction="vertical" size={13} style={{ width: '100%' }}>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0, lineHeight: 1.7 }}>
              转发前对请求做「有损瘦身」，节省上游 token：超出保留窗口的历史消息里，
              超长文本按格式感知压缩——JSON 保留字段与层级只删值里的噪声、日志只留首尾行、
              其余截成「开头 + 省略标记 + 结尾」。日志详情记录的仍是客户端的原始请求，可随时对比。
            </Typography.Paragraph>
            <Alert
              type="warning"
              showIcon
              style={{ fontSize: 12 }}
              message="压缩是有损的：早期上下文的细节会丢失，若发现模型答非所问，调大保留窗口或关闭引擎。"
            />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>
                保留最近消息数（原文不动）
                <InputNumber
                  style={{ width: '100%', marginTop: 4 }}
                  min={1}
                  max={100}
                  value={draft.caveman_keep_recent}
                  onChange={(v) => set('caveman_keep_recent', v ?? 4)}
                />
              </label>
              <label style={{ fontSize: 12.5, color: '#93a0b8' }}>
                截断阈值（字符，单块文本超过才压缩）
                <InputNumber
                  style={{ width: '100%', marginTop: 4 }}
                  min={64}
                  max={100000}
                  step={100}
                  value={draft.caveman_min_chars}
                  onChange={(v) => set('caveman_min_chars', v ?? 400)}
                />
              </label>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12.5 }}>
              <Switch size="small" checked={draft.caveman_compress_system} onChange={(v) => set('caveman_compress_system', v)} />
              同时压缩 system 提示词
            </label>
            <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
              默认不压缩 system：改写它会打断上游的 prompt cache，反而更费钱。
            </Typography.Text>
          </Space>
        </Card>
      </Col>
      <Col xs={24}>
        <Card
          size="small"
          title="Say less（输出瘦身）"
          extra={
            <Switch
              checked={draft.voice_enabled}
              checkedChildren="已启动"
              unCheckedChildren="未启动"
              onChange={(v) => set('voice_enabled', v)}
            />
          }
        >
          <Space direction="vertical" size={13} style={{ width: '100%' }}>
            <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0, lineHeight: 1.7 }}>
              在转发请求的 system 里附加「电报体回复」规则，让模型回答变短：
              先答案后解释、无寒暄、代码 / 命令 / 报错原样保留。规则改写自开源项目
              JuliusBrussee/caveman（Apache-2.0）。
            </Typography.Paragraph>
            <Alert
              type="info"
              showIcon
              style={{ fontSize: 12 }}
              message="规则本身约占 400 token 输入，且会打断 system 的 prompt cache；输出 token 明显更省时才划算。建议长会话常开，短问答可关。"
            />
            <Typography.Text type="secondary" style={{ fontSize: 11.5 }}>
              只影响模型「说」多少，不碰你的提问内容；代码、命令等载荷永远原样保留。
            </Typography.Text>
          </Space>
        </Card>
      </Col>
      <Col xs={24}>
        <RtkCard />
      </Col>
      <Col xs={24}>
        <Button type="primary" icon={<SaveOutlined />} onClick={save}>保存配置</Button>
      </Col>
    </Row>
  )
}
