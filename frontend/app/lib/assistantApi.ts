/**
 * Alpha 助手 API
 */

export interface AssistantConversation {
  session_uuid: string;
  title: string;
  channel: string;
  messageCount: number;
  createdAt?: string;
  updatedAt?: string;
}

export interface AssistantMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  createdAt?: string;
  isErrorAlert?: boolean;
  alertSeverity?: 'P0' | 'P1' | 'P2' | string;
}

export interface AssistantBadgeEntry {
  logger?: string;
  count?: number;
  sample?: string;
  severity_hint?: string;
}

export interface AssistantBadge {
  count: number;
  kind: 'none' | 'p0' | 'error_types';
  label: string;
  hint: string;
  p0_count: number;
  distinct_groups: number;
  total_errors: number;
  top_entries?: AssistantBadgeEntry[];
  pushed_alerts?: number;
  alert_fingerprint?: string | null;
}

const BASE =
  `${(import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') || '/api'}/assistant`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

export function getAssistantBadge(windowHours = 24, sessionUuid?: string) {
  const qs = new URLSearchParams({ window_hours: String(windowHours) });
  if (sessionUuid) qs.set('session_uuid', sessionUuid);
  return request<AssistantBadge>(`/badge?${qs.toString()}`);
}

export function listAssistantConversations(limit = 30) {
  return request<{ conversations: AssistantConversation[] }>(`/conversations?limit=${limit}`);
}

export function createAssistantConversation(seedWelcome = true) {
  return request<{ session_uuid: string; title: string; channel: string }>('/conversations', {
    method: 'POST',
    body: JSON.stringify({ seed_welcome: seedWelcome }),
  });
}

export function getAssistantMessages(sessionUuid: string) {
  return request<{ messages: AssistantMessage[]; session_uuid: string }>(
    `/conversations/${encodeURIComponent(sessionUuid)}/messages`,
  );
}

export function deleteAssistantConversation(sessionUuid: string) {
  return request<{ deleted: boolean }>(`/conversations/${encodeURIComponent(sessionUuid)}`, {
    method: 'DELETE',
  });
}

export function pushAssistantDailyReport() {
  return request<{ ok: boolean }>('/daily-report/push', { method: 'POST' });
}

export const WELCOME_MESSAGE =
  '你好，我是 Alpha 助手。可以问我：今天有什么报错？OpenCode 在线吗？';
