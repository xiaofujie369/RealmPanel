export const base = location.pathname.replace(/\/?$/, '/') + 'api/v1/'
export let csrf = ''
export function setCsrf(value: string) { csrf = value }
export async function api<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(base + path, { method, headers: { 'Content-Type': 'application/json', 'X-RMP-Request': '1', 'X-CSRF-Token': csrf }, body: body === undefined ? undefined : JSON.stringify(body) })
  const value = await response.json()
  if (response.status === 401 && path !== 'auth/login') window.dispatchEvent(new Event('rmp-session-expired'))
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : JSON.stringify(value.detail))
  return value
}
