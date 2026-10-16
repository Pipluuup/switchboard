// Switchboard Cookie 助手：读取站点全部 Cookie（含 httpOnly），写回本机网关设置。

const SITES = {
  opencode: {
    label: 'OpenCode Console',
    domain: 'opencode.ai',
    urls: ['https://opencode.ai/console', 'https://opencode.ai/'],
    apiOrigin: 'https://opencode.ai',
    setting: 'opencode_console_cookie',
    btn: 'btn-opencode',
  },
  ark: {
    label: '火山方舟',
    domain: 'volcengine.com',
    urls: [
      // 认证 Cookie（digest/access/userInfo）挂在 /api 路径下，必须按真实 API 地址收集
      'https://console.volcengine.com/api/top/ark/cn-beijing/2024-01-01/GetAgentPlanAFPUsage',
      'https://console.volcengine.com/ark',
      'https://console.volcengine.com/',
    ],
    apiOrigin: 'https://console.volcengine.com',
    setting: 'ark_console_cookie',
    btn: 'btn-ark',
  },
}

/** 判定「有登录态」的 Cookie 名（各家叫法不同） */
const AUTH_HINTS = /^(sessionid|session|sid|token|auth|jwt|digest|access|userinfo)$/i

const $ = (id) => document.getElementById(id)
const status = (msg, cls = '') => { $('status').textContent = msg; $('status').className = cls }

// ---- 网关地址 / 令牌（持久化在扩展本地） ----
chrome.storage.local.get({ gw: 'http://127.0.0.1:8000', token: '' }, ({ gw, token }) => {
  $('gw').value = gw
  $('token').value = token
})
for (const id of ['gw', 'token']) {
  $(id).addEventListener('change', () => chrome.storage.local.set({ [id]: $(id).value.trim() }))
}

async function gateway() {
  const { gw, token } = await chrome.storage.local.get({ gw: 'http://127.0.0.1:8000', token: '' })
  return { base: (gw || 'http://127.0.0.1:8000').replace(/\/+$/, ''), token: token || '' }
}

/** 收集某站点全部 Cookie（含 httpOnly 与 CHIPS 分区存储），合并去重。
 *
 * 每个候选 URL 按「无分区 + 若干 topLevelSite 分区」多查几轮；
 * 按「长域在前、短路径在前」排序，与浏览器发送顺序一致。
 */
async function collectCookies(site) {
  const partitions = [
    undefined,
    { topLevelSite: `https://${site.domain}`, hasCrossSiteAncestor: false },
    { topLevelSite: `https://${site.domain}`, hasCrossSiteAncestor: true },
    { topLevelSite: site.apiOrigin, hasCrossSiteAncestor: false },
    {},
  ]
  const map = new Map()
  for (const url of site.urls) {
    for (const pk of partitions) {
      const q = pk === undefined ? { url } : { url, partitionKey: pk }
      let found = []
      try { found = await chrome.cookies.getAll(q) } catch { /* 分区查询旧内核不支持 */ }
      for (const c of found) map.set(c.name + '|' + c.domain + '|' + c.path, c)
    }
  }
  return [...map.values()].sort(
    (a, b) => b.domain.length - a.domain.length || a.path.length - b.path.length)
}

async function cookieHeader(site) {
  const cookies = await collectCookies(site)
  const names = cookies.map((c) => c.name)
  return {
    header: cookies.map((c) => `${c.name}=${c.value}`).join('; '),
    names,
    hasLogin: names.some((n) => AUTH_HINTS.test(n)),
  }
}

async function writeCookie(key) {
  const site = SITES[key]
  const btn = $(site.btn)
  btn.disabled = true
  status(`正在读取 ${site.label} Cookie…`)
  try {
    const { header, names, hasLogin } = await cookieHeader(site)
    if (!header) {
      status(`✗ 未读到 ${site.domain} 的 Cookie——请先在浏览器里登录 ${site.urls[1]}`, 'err')
      return
    }
    if (!hasLogin) {
      status(`✗ 缺少登录态 Cookie（如 digest / sessionid）。请确认：①本扩展和登录用的是同一个浏览器；\n②${site.urls[1]} 处于已登录状态，然后重试。`, 'err')
      return
    }
    const { base, token } = await gateway()
    const resp = await fetch(`${base}/api/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Token': token },
      body: JSON.stringify({ [site.setting]: header }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status} ${(await resp.text()).slice(0, 120)}`)
    status(`✓ ${site.label} Cookie 已写入网关（${names.length} 个 Cookie，${header.length} 字符）`, 'ok')
  } catch (e) {
    status(`✗ 写入失败：${e.message}\n请确认网关已启动、地址正确。`, 'err')
  } finally {
    btn.disabled = false
  }
}

$('btn-opencode').addEventListener('click', () => writeCookie('opencode'))
$('btn-ark').addEventListener('click', () => writeCookie('ark'))

$('btn-test').addEventListener('click', async () => {
  status('正在测试…')
  try {
    const { base, token } = await gateway()
    const resp = await fetch(`${base}/healthz`, { headers: { 'X-Admin-Token': token } })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    status(`✓ 网关在线：${base}`, 'ok')
  } catch (e) {
    status(`✗ 连不上：${e.message}`, 'err')
  }
})

// 跳转到对应控制台（新标签页）
for (const [key, id] of [['opencode', 'lnk-opencode'], ['ark', 'lnk-ark']]) {
  $(id).addEventListener('click', (ev) => {
    ev.preventDefault()
    chrome.tabs.create({ url: SITES[key].urls[0] })
  })
}
