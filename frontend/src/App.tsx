import { useEffect, useState } from 'react'
import { Layout, Menu, Button, App as AntApp } from 'antd'
import {
  AppstoreOutlined,
  BarChartOutlined,
  FileTextOutlined,
  ClusterOutlined,
  SwapOutlined,
  KeyOutlined,
  SettingOutlined,
  ReloadOutlined,
  BookOutlined,
  RocketOutlined,
} from '@ant-design/icons'
import { GET } from './api/client'
import Dashboard from './views/Dashboard'
import Usage from './views/Usage'
import Upstreams from './views/Upstreams'
import Mappings from './views/Mappings'
import Keys from './views/Keys'
import Logs from './views/Logs'
import Settings from './views/Settings'
import TokenOpt from './views/TokenOpt'
import QuickstartModal from './components/QuickstartModal'
import './global.css'

const { Sider, Header, Content } = Layout

type ViewKey = 'dashboard' | 'usage' | 'logs' | 'upstreams' | 'mappings' | 'keys' | 'optimize' | 'settings'

const VIEW_META: Record<ViewKey, { title: string; sub: string }> = {
  dashboard: { title: '概览', sub: '服务运行状态与用量统计' },
  usage: { title: '用量', sub: '5 小时 / 7 天 / 30 天滚动窗口的 Token 与请求消耗' },
  logs: { title: '请求日志', sub: '每次转发的明细与统计' },
  upstreams: { title: '上游管理', sub: '配置上游节点与故障切换' },
  mappings: { title: '模型映射', sub: '把客户端请求的模型别名映射到上游实际模型' },
  keys: { title: '客户端 Key', sub: '为本地工具分发独立密钥并限速' },
  optimize: { title: 'Token 优化', sub: 'Caveman 引擎：转发前压缩请求，节省上游 token' },
  settings: { title: '设置', sub: '服务参数、容错策略与配置备份' },
}

const MENU_ITEMS = [
  {
    type: 'group' as const,
    label: '监控',
    children: [
      { key: 'dashboard', icon: <AppstoreOutlined />, label: '概览' },
      { key: 'usage', icon: <BarChartOutlined />, label: '用量' },
      { key: 'logs', icon: <FileTextOutlined />, label: '请求日志' },
    ],
  },
  {
    type: 'group' as const,
    label: '路由',
    children: [
      { key: 'upstreams', icon: <ClusterOutlined />, label: '上游管理' },
      { key: 'mappings', icon: <SwapOutlined />, label: '模型映射' },
      { key: 'keys', icon: <KeyOutlined />, label: '客户端 Key' },
    ],
  },
  {
    type: 'group' as const,
    label: '系统',
    children: [
      { key: 'optimize', icon: <RocketOutlined />, label: 'Token 优化' },
      { key: 'settings', icon: <SettingOutlined />, label: '设置' },
    ],
  },
]

function viewFromHash(): ViewKey {
  const h = location.hash.replace(/^#\/?/, '')
  return (h in VIEW_META ? h : 'dashboard') as ViewKey
}

export default function App() {
  const { message } = AntApp.useApp()
  const [view, setView] = useState<ViewKey>(viewFromHash)
  const [refreshKey, setRefreshKey] = useState(0)
  const [serviceUp, setServiceUp] = useState<boolean | null>(null)
  const [quickstartOpen, setQuickstartOpen] = useState(false)

  // hash 同步：#/logs 直达对应视图
  useEffect(() => {
    const onHash = () => setView(viewFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const switchView = (v: string) => {
    setView(v as ViewKey)
    history.replaceState(null, '', `#/${v}`)
  }

  // 健康检查轮询
  useEffect(() => {
    const ping = async () => {
      try {
        await GET('/healthz')
        setServiceUp(true)
      } catch {
        setServiceUp(false)
      }
    }
    ping()
    const t = setInterval(ping, 15000)
    return () => clearInterval(t)
  }, [])

  const meta = VIEW_META[view]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={236} style={{ borderRight: '1px solid #222938', display: 'flex', flexDirection: 'column' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '18px 16px 14px' }}>
          <div className="brand-logo">P</div>
          <div>
            <div style={{ fontWeight: 650, fontSize: 13.5, lineHeight: 1.3 }}>Proxy</div>
            <div style={{ fontSize: 11, color: '#5c6880' }}>中转网关控制台</div>
          </div>
        </div>
        <Menu
          mode="inline"
          theme="dark"
          selectedKeys={[view]}
          items={MENU_ITEMS}
          onClick={({ key }) => switchView(key)}
          style={{ borderInlineEnd: 'none', flex: 1 }}
        />
        <div style={{ borderTop: '1px solid #222938', padding: '0 8px 14px' }}>
          <div className={`link-line${serviceUp === true ? ' live' : serviceUp === false ? ' down' : ''}`} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#93a0b8', padding: '0 8px' }}>
            <span className={`status-dot${serviceUp === true ? ' ok' : serviceUp === false ? ' bad' : ''}`} />
            <span>{serviceUp === true ? '服务运行中' : serviceUp === false ? '服务未连接' : '连接中…'}</span>
          </div>
          <div className="mono" style={{ fontSize: 11, color: '#5c6880', marginTop: 8, padding: '0 8px' }}>v1.0.0</div>
        </div>
      </Sider>

      <Layout>
        <Header
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #222938',
            padding: '12px 28px',
            height: 'auto',
            lineHeight: 1.4,
          }}
        >
          <div>
            <div style={{ fontSize: 18, fontWeight: 650 }}>{meta.title}</div>
            <div style={{ fontSize: 12.5, color: '#5c6880' }}>{meta.sub}</div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                setRefreshKey((k) => k + 1)
                message.success('已刷新')
              }}
            >
              刷新
            </Button>
            <Button type="primary" icon={<BookOutlined />} onClick={() => setQuickstartOpen(true)}>
              接入指南
            </Button>
          </div>
        </Header>
        <Content style={{ padding: '22px 28px 60px', overflowY: 'auto' }}>
          {/* key 里带 refreshKey：点击「刷新」时重挂载当前视图以重新拉取数据 */}
          <div key={`${view}-${refreshKey}`} style={{ animation: 'viewIn .28s ease' }}>
            {view === 'dashboard' && <Dashboard />}
            {view === 'usage' && <Usage />}
            {view === 'logs' && <Logs />}
            {view === 'upstreams' && <Upstreams />}
            {view === 'mappings' && <Mappings />}
            {view === 'keys' && <Keys />}
            {view === 'optimize' && <TokenOpt />}
            {view === 'settings' && <Settings />}
          </div>
        </Content>
      </Layout>

      <QuickstartModal open={quickstartOpen} onClose={() => setQuickstartOpen(false)} />
    </Layout>
  )
}
