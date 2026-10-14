/** API 客户端：移植自旧版 app.js 的 api()，保留 X-Admin-Token 与 401 重试流程 */

let adminToken = localStorage.getItem('ccproxy_admin_token') || ''

export function getAdminToken(): string {
  return adminToken
}

export function setAdminToken(t: string): void {
  adminToken = t
  localStorage.setItem('ccproxy_admin_token', t)
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export async function api<T = unknown>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((opts.headers as Record<string, string>) || {}),
  }
  if (adminToken) headers['X-Admin-Token'] = adminToken

  let res: Response
  try {
    res = await fetch(path, { ...opts, headers })
  } catch (e) {
    throw new Error('无法连接服务：' + (e as Error).message)
  }

  if (res.status === 401) {
    const t = prompt('该服务已启用管理口令，请输入：')
    if (t !== null) {
      setAdminToken(t.trim())
      return api<T>(path, opts)
    }
    throw new ApiError('需要管理口令', 401)
  }

  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      const j = await res.json()
      msg = j.detail || j.error?.message || msg
    } catch {
      /* 非 JSON 响应，保留默认消息 */
    }
    throw new ApiError(msg, res.status)
  }

  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) return res.json() as Promise<T>
  return res.text() as Promise<T>
}

export const GET = <T = unknown>(p: string) => api<T>(p)
export const POST = <T = unknown>(p: string, b?: unknown) =>
  api<T>(p, { method: 'POST', body: JSON.stringify(b ?? {}) })
export const PUT = <T = unknown>(p: string, b?: unknown) =>
  api<T>(p, { method: 'PUT', body: JSON.stringify(b ?? {}) })
export const DEL = <T = unknown>(p: string) => api<T>(p, { method: 'DELETE' })
