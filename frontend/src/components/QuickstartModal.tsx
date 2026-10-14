import { useEffect, useState } from 'react'
import { Modal, App, Skeleton, Typography } from 'antd'
import { RobotOutlined } from '@ant-design/icons'
import { GET } from '../api/client'
import type { Quickstart } from '../api/types'
import claudeSvg from '../assets/claude.svg'
import traeSvg from '../assets/trae.svg'
import qcodeSvg from '../assets/qcode.svg'

function CodeBlock({ text, onCopy }: { text: string; onCopy: (t: string) => void }) {
  return (
    <div className="code-block">
      {text}
      <button
        className="copy-btn"
        onClick={() => onCopy(text)}
        style={{
          position: 'absolute',
          top: 6,
          right: 6,
          fontSize: 11,
          padding: '2px 8px',
          borderRadius: 5,
          border: '1px solid #303950',
          background: '#11151f',
          color: '#93a0b8',
          cursor: 'pointer',
        }}
      >
        复制
      </button>
    </div>
  )
}

/** 接入指南的客户端图标定义（品牌 logo；WorkBuddy 无公开 logo，用机器人代替） */
type ClientKey = 'claude' | 'workbuddy' | 'qcode' | 'trae'
const CLIENTS: { key: ClientKey; name: string; icon: string; color: string }[] = [
  { key: 'claude', name: 'Claude Code', icon: claudeSvg, color: '#d97757' },
  { key: 'workbuddy', name: 'WorkBuddy', icon: '', color: '#45c98a' },
  { key: 'qcode', name: 'Qcode', icon: qcodeSvg, color: '#7e96ff' },
  { key: 'trae', name: 'Trae', icon: traeSvg, color: '#ff5c5c' },
]

/** 接入指南弹窗：顶部图标选择客户端，下方显示对应接入方法 */
export default function QuickstartModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { message } = App.useApp()
  const [data, setData] = useState<Quickstart | null>(null)
  const [active, setActive] = useState<ClientKey>('claude')

  useEffect(() => {
    if (!open) return
    setData(null)
    GET<Quickstart>('/api/quickstart').then(setData).catch((e) => message.error('获取接入信息失败：' + e.message))
  }, [open, message])

  const copy = async (t: string) => {
    try {
      await navigator.clipboard.writeText(t)
      message.success('已复制')
    } catch {
      message.error('复制失败')
    }
  }

  /** 通用连接信息（OpenAI 兼容客户端：Qcode / Trae 等） */
  const connInfo = (baseUrlSuffix = '/v1') => data && (
    <>
      <div>
        <div style={{ fontSize: 12.5, color: '#93a0b8' }}>Base URL（OpenAI 兼容）</div>
        <CodeBlock text={data.base_url.replace(/\/$/, '') + baseUrlSuffix} onCopy={copy} />
      </div>
      <div>
        <div style={{ fontSize: 12.5, color: '#93a0b8' }}>API Key</div>
        <CodeBlock text={data.api_key} onCopy={copy} />
      </div>
      <div>
        <div style={{ fontSize: 12.5, color: '#93a0b8' }}>模型名</div>
        <CodeBlock text="Free" onCopy={copy} />
      </div>
    </>
  )

  const claudeGuide = data && (
    <>
      <p style={{ fontSize: 12, color: '#5c6880', margin: 0 }}>
        把下面的环境变量写到你的 shell 里，然后直接运行 <code>claude</code> 即可走本地中转。
      </p>
      <div>
        <div style={{ fontSize: 12.5, color: '#93a0b8' }}>Base URL（Anthropic 协议）</div>
        <CodeBlock text={data.base_url} onCopy={copy} />
      </div>
      <div>
        <div style={{ fontSize: 12.5, color: '#93a0b8' }}>API Key</div>
        <CodeBlock text={data.api_key} onCopy={copy} />
      </div>
      <div>
        <div style={{ fontSize: 12.5, color: '#93a0b8' }}>macOS / Linux（bash / zsh）</div>
        <CodeBlock text={data.snippets.env} onCopy={copy} />
      </div>
      <div>
        <div style={{ fontSize: 12.5, color: '#93a0b8' }}>Windows（PowerShell）</div>
        <CodeBlock text={data.snippets.powershell} onCopy={copy} />
      </div>
      <div>
        <div style={{ fontSize: 12.5, color: '#93a0b8' }}>ccswitch 配置片段</div>
        <CodeBlock text={data.snippets.ccswitch} onCopy={copy} />
      </div>
      <p style={{ fontSize: 12, color: '#5c6880', margin: 0 }}>
        Windows 永久生效：<code>setx ANTHROPIC_BASE_URL "{data.base_url}"</code> 与{' '}
        <code>setx ANTHROPIC_API_KEY "你的Key"</code>，重启终端。
      </p>
    </>
  )

  const workbuddyGuide = data && (
    <>
      <p style={{ fontSize: 12, color: '#5c6880', margin: 0 }}>
        把下面这段提示词发给 WorkBuddy，Agent 会自己完成接入并自测连通性。
      </p>
      <CodeBlock
        text={[
          '请把自己接入以下本地模型网关，并完成连通性自测：',
          '',
          `- 网关 Base URL：${data.base_url}`,
          `- API Key：${data.api_key}`,
          '- 协议二选一（选你原生支持的）：OpenAI 兼容 POST /v1/chat/completions；Anthropic 兼容 POST /v1/messages',
          '- 模型名固定用：Free（这是网关的默认免费通道，直接填入你的模型配置即可，不需要查询模型清单）',
          '',
          '另外，为节省 token：执行 git / npm / cargo / 测试等命令时，统一在命令前加 rtk 前缀（如 rtk git status、rtk cargo test），',
          '命令输出会被压缩后再进入你的上下文；输出异常时再去掉 rtk 前缀排查。',
          '',
          '完成后请：',
          '1. 把 Base URL + API Key + 模型名 Free 写入你自己的模型配置',
          '2. 发一条测试消息验证连通，报告所用模型、延迟和返回内容',
          '3. 若失败：OpenAI 格式走 /v1/chat/completions，Anthropic 格式走 /v1/messages，检查协议与路径是否匹配后再重试',
        ].join('\n')}
        onCopy={copy}
      />
    </>
  )

  const qcodeGuide = data && (
    <>
      <p style={{ fontSize: 12, color: '#5c6880', margin: 0 }}>
        在 Qcode 的模型设置里添加自定义提供商（OpenAI 兼容），填入以下三项：
      </p>
      {connInfo()}
      <p style={{ fontSize: 12, color: '#5c6880', margin: 0 }}>
        保存后把默认模型切到 Free 即可。若 Qcode 只支持 Anthropic 协议，Base URL 去掉末尾 <code>/v1</code> 直接填网关地址。
      </p>
    </>
  )

  const traeGuide = data && (
    <>
      <p style={{ fontSize: 12, color: '#5c6880', margin: 0 }}>
        Trae → 设置 → 模型 → 添加自定义模型，协议选 <b>OpenAI 兼容</b>，填入以下三项：
      </p>
      {connInfo()}
      <p style={{ fontSize: 12, color: '#5c6880', margin: 0 }}>
        保存后在对话里选中该模型即可。若列表校验模型名，先填 <code>Free</code> 再保存；健康检查失败多为协议选错，检查是否误选了 Anthropic。
      </p>
    </>
  )

  const guides: Record<ClientKey, React.ReactNode> = {
    claude: claudeGuide,
    workbuddy: workbuddyGuide,
    qcode: qcodeGuide,
    trae: traeGuide,
  }

  return (
    <Modal title="接入指南" open={open} onCancel={onClose} footer={null} width={680}>
      {!data ? (
        <Skeleton active />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* 客户端图标选择区 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
            {CLIENTS.map((c) => (
              <button
                key={c.key}
                onClick={() => setActive(c.key)}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: 6,
                  padding: '12px 6px 10px',
                  borderRadius: 10,
                  border: active === c.key ? `1.5px solid ${c.color}` : '1px solid #232a3a',
                  background: active === c.key ? 'rgba(126,150,255,0.07)' : 'transparent',
                  cursor: 'pointer',
                  transition: 'border-color .15s, background .15s',
                }}
              >
                {c.icon ? (
                  <img src={c.icon} alt={c.name} style={{ width: 24, height: 24, display: 'block' }} />
                ) : (
                  <RobotOutlined style={{ fontSize: 24, color: c.color, display: 'block' }} />
                )}
                <span style={{ fontSize: 12, color: active === c.key ? '#dfe6f5' : '#93a0b8' }}>{c.name}</span>
              </button>
            ))}
          </div>

          {/* 当前客户端的接入内容 */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, borderTop: '1px solid #1d2434', paddingTop: 14 }}>
            <Typography.Text style={{ fontSize: 13, fontWeight: 550 }}>
              {CLIENTS.find((c) => c.key === active)?.name} 接入
            </Typography.Text>
            {guides[active]}
          </div>
        </div>
      )}
    </Modal>
  )
}
