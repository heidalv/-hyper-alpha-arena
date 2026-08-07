const API_BASE = '/api'

export async function apiRequest<T = any>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${API_BASE}${endpoint}`
  const res = await fetch(url, {
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...options.headers as any },
    ...options,
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try { const d = await res.json(); msg = d.detail || d.message || msg } catch {}
    throw new Error(msg)
  }
  return res.json()
}

// Alias for convenience
export const api = { get: apiRequest, post: apiRequest }
