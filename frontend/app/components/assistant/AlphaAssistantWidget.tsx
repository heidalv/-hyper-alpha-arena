import { useCallback, useEffect, useRef, useState } from 'react';
import { Brain, Minus, Plus, Send, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Button } from '../ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../ui/select';
import {
  AssistantBadge,
  AssistantConversation,
  AssistantMessage,
  WELCOME_MESSAGE,
  createAssistantConversation,
  getAssistantBadge,
  getAssistantMessages,
  listAssistantConversations,
} from '@/lib/assistantApi';

type ChatMessage = {
  role: 'user' | 'assistant';
  content: string;
  id?: string;
  createdAt?: string;
  streaming?: boolean;
  statusText?: string;
  isErrorAlert?: boolean;
  alertSeverity?: string;
};

const STORAGE_OPEN = 'alpha_assistant_open';
const STORAGE_SESSION = 'alpha_assistant_session';
const STORAGE_BADGE_DISMISS = 'alpha_assistant_badge_dismiss';

function errorAlertBubbleClass(severity?: string): string {
  switch ((severity || 'P2').toUpperCase()) {
    case 'P0':
      return 'bg-red-600/20 border border-red-600/50 text-red-950 dark:text-red-100';
    case 'P1':
      return 'bg-amber-500/20 border border-amber-500/50 text-amber-950 dark:text-amber-100';
    default:
      return 'bg-sky-500/15 border border-sky-500/40 text-sky-950 dark:text-sky-100';
  }
}

function badgeBannerClass(kind?: AssistantBadge['kind']): string {
  if (kind === 'p0') {
    return 'bg-red-600/15 border-red-600/40 text-red-800 dark:text-red-200';
  }
  return 'bg-amber-500/15 border-amber-500/40 text-amber-900 dark:text-amber-100';
}

function badgeFingerprint(b: AssistantBadge): string {
  const tops = (b.top_entries || []).map((e) => e.logger || '').join('|');
  return `${b.kind}:${b.count}:${b.total_errors}:${b.distinct_groups}:${tops}`;
}

function isBadgeDismissed(b: AssistantBadge | null): boolean {
  if (!b?.count) return true;
  try {
    const raw = localStorage.getItem(STORAGE_BADGE_DISMISS);
    if (!raw) return false;
    const saved = JSON.parse(raw) as { fp?: string };
    return saved.fp === badgeFingerprint(b);
  } catch {
    return false;
  }
}

function dismissBadge(b: AssistantBadge) {
  localStorage.setItem(
    STORAGE_BADGE_DISMISS,
    JSON.stringify({ fp: badgeFingerprint(b), at: Date.now() }),
  );
}

function parseSseChunk(raw: string): { event: string; data: string } | null {
  const lines = raw.split('\n');
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  const data = dataLines.join('\n');
  if (!data) return null;
  return { event, data };
}

function formatMessageTime(iso?: string): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.toDateString() === now.toDateString()) return hm;
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `昨天 ${hm}`;
  if (d.getFullYear() === now.getFullYear()) {
    return `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
  }
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${hm}`;
}

function AssistantMarkdown({ content }: { content: string }) {
  return (
    <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-1 prose-ul:my-1 prose-li:my-0.5 prose-headings:my-1.5 break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

function waitingHint(elapsedSec: number): string {
  if (elapsedSec < 8) return '正在连接 AI 服务，请稍候…';
  if (elapsedSec < 30) return 'v4-pro 模型正在深度思考，首次回复通常需要 1-3 分钟。';
  if (elapsedSec < 90) return `已等待 ${elapsedSec} 秒，模型仍在生成中，没有卡死，请继续等待。`;
  return `已等待 ${elapsedSec} 秒，复杂问题可能更久；你可以先做别的事，回复完成后会显示在这里。`;
}

function WaitingIndicator({
  statusText,
  elapsedSec,
}: {
  statusText: string;
  elapsedSec: number;
}) {
  return (
    <div className="flex flex-col gap-2 py-0.5 min-w-[200px]">
      <div className="flex items-center gap-2 text-purple-600 dark:text-purple-400">
        <span
          className="inline-block w-4 h-4 shrink-0 border-2 border-current border-t-transparent rounded-full animate-spin"
          aria-hidden
        />
        <span className="font-medium text-sm">{statusText}</span>
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed">{waitingHint(elapsedSec)}</p>
      {elapsedSec >= 5 && (
        <p className="text-[11px] text-muted-foreground/70 tabular-nums">等待时间：{elapsedSec} 秒</p>
      )}
    </div>
  );
}

function toChatMessages(rows: AssistantMessage[]): ChatMessage[] {
  if (!rows.length) {
    return [{ role: 'assistant', content: WELCOME_MESSAGE }];
  }
  return rows.map((m) => ({
    role: m.role as 'user' | 'assistant',
    content: m.content,
    id: String(m.id),
    createdAt: m.createdAt,
    isErrorAlert: m.isErrorAlert,
    alertSeverity: m.alertSeverity,
  }));
}

function ensureVisibleMessages(messages: ChatMessage[]): ChatMessage[] {
  if (!messages.length) return [{ role: 'assistant', content: WELCOME_MESSAGE }];
  return messages;
}

export default function AlphaAssistantWidget() {
  const [open, setOpen] = useState(() => localStorage.getItem(STORAGE_OPEN) === '1');
  const [badge, setBadge] = useState<AssistantBadge | null>(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: 'assistant', content: WELCOME_MESSAGE },
  ]);
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(STORAGE_SESSION) || '');
  const [conversations, setConversations] = useState<AssistantConversation[]>([]);
  const [loadingConversations, setLoadingConversations] = useState(false);
  const [initialized, setInitialized] = useState(false);
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [badgeDismissed, setBadgeDismissed] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const sessionIdRef = useRef(sessionId);
  const openRef = useRef(open);
  sessionIdRef.current = sessionId;
  openRef.current = open;

  const acknowledgeBadge = useCallback((b: AssistantBadge | null) => {
    if (b?.count && b.count > 0) {
      dismissBadge(b);
    }
    setBadgeDismissed(true);
  }, []);

  const isNearBottom = useCallback((el: HTMLDivElement, threshold = 72) => {
    return el.scrollHeight - el.scrollTop - el.clientHeight <= threshold;
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    const el = scrollRef.current;
    if (!el || !stickToBottomRef.current) return;
    el.scrollTo({ top: el.scrollHeight, behavior });
  }, []);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickToBottomRef.current = isNearBottom(el);
  }, [isNearBottom]);

  const refreshBadge = useCallback(async (sid?: string) => {
    const activeSid = sid ?? sessionIdRef.current;
    try {
      const b = await getAssistantBadge(24, activeSid || undefined);
      setBadge(b);
      setBadgeDismissed(isBadgeDismissed(b));
      if (activeSid && (b.pushed_alerts ?? 0) > 0) {
        const data = await getAssistantMessages(activeSid);
        setMessages(ensureVisibleMessages(toChatMessages(data.messages || [])));
        stickToBottomRef.current = true;
      }
      return b;
    } catch {
      setBadge(null);
      setBadgeDismissed(true);
      return null;
    }
  }, []);

  const refreshConversations = useCallback(async () => {
    setLoadingConversations(true);
    try {
      const data = await listAssistantConversations();
      setConversations(data.conversations || []);
      return data.conversations || [];
    } catch {
      return [];
    } finally {
      setLoadingConversations(false);
    }
  }, []);

  const applyMessages = useCallback((rows: ChatMessage[]) => {
    setMessages(ensureVisibleMessages(rows));
  }, []);

  const loadSession = useCallback(async (uuid: string) => {
    if (!uuid) return false;
    try {
      const data = await getAssistantMessages(uuid);
      applyMessages(toChatMessages(data.messages || []));
      setSessionId(uuid);
      localStorage.setItem(STORAGE_SESSION, uuid);
      const b = await refreshBadge(uuid);
      if (openRef.current) {
        acknowledgeBadge(b);
      }
      return true;
    } catch {
      localStorage.removeItem(STORAGE_SESSION);
      setSessionId('');
      applyMessages([{ role: 'assistant', content: WELCOME_MESSAGE }]);
      return false;
    }
  }, [applyMessages, refreshBadge, acknowledgeBadge]);

  const startNewConversation = useCallback(async () => {
    try {
      const created = await createAssistantConversation(true);
      setSessionId(created.session_uuid);
      localStorage.setItem(STORAGE_SESSION, created.session_uuid);
      applyMessages([{ role: 'assistant', content: WELCOME_MESSAGE }]);
      await refreshConversations();
      await refreshBadge(created.session_uuid);
    } catch {
      setSessionId('');
      localStorage.removeItem(STORAGE_SESSION);
      applyMessages([{ role: 'assistant', content: WELCOME_MESSAGE }]);
    }
  }, [applyMessages, refreshConversations, refreshBadge]);

  const bootstrapSession = useCallback(async () => {
    await refreshBadge();
    const list = await refreshConversations();
    const saved = localStorage.getItem(STORAGE_SESSION);

    if (saved) {
      const ok = await loadSession(saved);
      if (ok) {
        setInitialized(true);
        return;
      }
    }

    const latest = list[0];
    if (latest?.session_uuid) {
      await loadSession(latest.session_uuid);
    } else {
      await startNewConversation();
    }
    setInitialized(true);
  }, [refreshBadge, refreshConversations, loadSession, startNewConversation]);

  useEffect(() => {
    localStorage.setItem(STORAGE_OPEN, open ? '1' : '0');
  }, [open]);

  useEffect(() => {
    bootstrapSession();
  }, [bootstrapSession]);

  useEffect(() => {
    const ms = badge?.count ? 45000 : 120000;
    const t = setInterval(() => {
      void refreshBadge(sessionIdRef.current);
    }, ms);
    return () => clearInterval(t);
  }, [badge?.count, refreshBadge]);

  useEffect(() => {
    if (!open || !initialized) return;
    (async () => {
      const b = await refreshBadge(sessionId);
      const list = await refreshConversations();
      if (sessionId && list.some((c) => c.session_uuid === sessionId)) {
        await loadSession(sessionId);
      } else if (list[0]?.session_uuid) {
        await loadSession(list[0].session_uuid);
      } else {
        await startNewConversation();
      }
      acknowledgeBadge(b);
    })();
  }, [open, initialized]); // eslint-disable-line react-hooks/exhaustive-deps -- 打开面板且初始化完成后刷新并标记已读

  useEffect(() => {
    if (!open) return;
    stickToBottomRef.current = true;
    requestAnimationFrame(() => scrollToBottom('auto'));
  }, [open, sessionId, scrollToBottom]);

  useEffect(() => {
    scrollToBottom(sending ? 'auto' : 'smooth');
  }, [messages, sending, scrollToBottom]);

  useEffect(() => {
    if (!sending) {
      setWaitSeconds(0);
      return;
    }
    setWaitSeconds(0);
    const timer = window.setInterval(() => {
      setWaitSeconds((s) => s + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [sending]);

  const selectValue =
    sessionId && conversations.some((c) => c.session_uuid === sessionId)
      ? sessionId
      : conversations[0]?.session_uuid || 'new';

  const send = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    setSending(true);
    stickToBottomRef.current = true;
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: text, createdAt: new Date().toISOString() },
      {
        role: 'assistant',
        content: '',
        streaming: true,
        statusText: '已收到，正在准备回复…',
        createdAt: new Date().toISOString(),
      },
    ]);

    let assistant = '';

    try {
      const res = await fetch('/api/assistant/chat-stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_message: text,
          session_id: sessionId || undefined,
          page_context: { route: window.location.pathname },
        }),
      });
      if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        for (const part of parts) {
          const parsed = parseSseChunk(part);
          if (!parsed) continue;
          const payload = JSON.parse(parsed.data);
          if (parsed.event === 'status') {
            const msg =
              (typeof payload.message === 'string' && payload.message) ||
              (payload.phase === 'preparing' ? '正在准备上下文…' : '思考中…');
            setMessages((prev) => {
              const next = [...prev];
              const idx = next.length - 1;
              if (idx >= 0 && next[idx].role === 'assistant') {
                next[idx] = {
                  ...next[idx],
                  streaming: true,
                  statusText: msg,
                  content: next[idx].content || '',
                };
              }
              return next;
            });
          }
          if (parsed.event === 'content' && payload.delta) {
            assistant += payload.delta;
            setMessages((prev) => {
              const next = [...prev];
              const idx = next.length - 1;
              if (idx >= 0 && next[idx].role === 'assistant') {
                next[idx] = {
                  ...next[idx],
                  role: 'assistant',
                  content: assistant,
                  streaming: true,
                  statusText: undefined,
                };
              }
              return next;
            });
          }
          if (parsed.event === 'done' && payload.session_id) {
            setSessionId(payload.session_id);
            localStorage.setItem(STORAGE_SESSION, payload.session_id);
            const links = payload.deep_links as Array<{ page?: string; tab?: string }> | undefined;
            if (links?.length) {
              const link = links[0];
              if (link.page === 'opencode-center' && link.tab) {
                sessionStorage.setItem('opencode_initial_tab', link.tab);
                window.dispatchEvent(new CustomEvent('arena-page-change', { detail: 'opencode-center' }));
              }
            }
            refreshConversations();
          }
        }
      }
      if (!assistant) {
        setMessages((prev) => {
          const next = [...prev];
          const idx = next.length - 1;
          if (idx >= 0 && next[idx].role === 'assistant') {
            next[idx] = {
              ...next[idx],
              content: '暂时没有收到回复，请稍后再试。',
              streaming: false,
              statusText: undefined,
            };
          }
          return next;
        });
      } else {
        setMessages((prev) => {
          const next = [...prev];
          const idx = next.length - 1;
          if (idx >= 0 && next[idx].role === 'assistant') {
            next[idx] = { ...next[idx], streaming: false, statusText: undefined };
          }
          return next;
        });
      }
    } catch {
      setMessages((prev) => {
        const next = [...prev];
        const idx = next.length - 1;
        if (idx >= 0 && next[idx].role === 'assistant') {
          next[idx] = {
            ...next[idx],
            content: '请求失败，请确认后端已启动且 Sidecar 可用。',
            streaming: false,
            statusText: undefined,
          };
        }
        return next;
      });
    } finally {
      setSending(false);
    }
  }, [input, sending, sessionId, refreshConversations]);

  const badgeTitle = badge && badge.count > 0 && !open && !badgeDismissed ? badge.hint : 'Alpha 助手';
  const displayBadgeCount =
    open || badgeDismissed ? 0 : badge && badge.count > 0 ? badge.count : 0;
  const visibleMessages = ensureVisibleMessages(messages);

  return (
    <>
      {open && (
        <div
          className="fixed z-[9000] flex flex-col bg-background border shadow-xl rounded-lg overflow-hidden"
          style={{ width: 420, height: 560, bottom: 96, right: 24 }}
        >
          <div className="flex flex-col gap-2 px-3 py-2 border-b bg-muted/30">
            <div className="flex items-center justify-between h-8">
              <span className="font-medium text-sm flex items-center gap-2">
                <Brain className="w-4 h-4 text-purple-500" /> Alpha 助手
              </span>
              <div className="flex gap-1">
                <Button size="icon" variant="ghost" className="h-8 w-8" onClick={() => setOpen(false)}>
                  <Minus className="w-4 h-4" />
                </Button>
                <Button size="icon" variant="ghost" className="h-8 w-8" onClick={() => setOpen(false)}>
                  <X className="w-4 h-4" />
                </Button>
              </div>
            </div>
            <div className="flex gap-2 items-center">
              <Select
                value={selectValue}
                onValueChange={(val) => {
                  if (val === 'new') startNewConversation();
                  else loadSession(val);
                }}
                disabled={loadingConversations}
              >
                <SelectTrigger className="h-8 text-xs flex-1">
                  <SelectValue placeholder={loadingConversations ? '加载中…' : '新建对话'} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="new">新建对话</SelectItem>
                  {conversations.map((c) => (
                    <SelectItem key={c.session_uuid} value={c.session_uuid}>
                      {c.title} ({c.messageCount})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                size="icon"
                variant="outline"
                className="h-8 w-8 shrink-0"
                title="新建对话"
                onClick={() => startNewConversation()}
              >
                <Plus className="w-4 h-4" />
              </Button>
            </div>
          </div>

          {displayBadgeCount > 0 && (
            <div className={`mx-3 mt-2 px-3 py-2 rounded-md border text-xs flex items-start justify-between gap-2 ${badgeBannerClass(badge?.kind)}`}>
              <div>
                <div className="font-semibold">{displayBadgeCount} 类后台错误</div>
                <div className="mt-0.5 opacity-90">{badge?.hint}</div>
                <div className="mt-1 text-[10px] opacity-75">告警已推送到下方对话，按 P0/P1/P2 分色显示</div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 text-xs shrink-0"
                onClick={() => {
                  if (badge) acknowledgeBadge(badge);
                }}
              >
                知道了
              </Button>
            </div>
          )}

          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-3 space-y-3 text-sm"
          >
            {visibleMessages.length === 0 ? (
              <div className="rounded-lg px-3 py-2 bg-muted text-foreground">
                <AssistantMarkdown content={WELCOME_MESSAGE} />
              </div>
            ) : (
              visibleMessages.map((m, i) => {
                const timeLabel = formatMessageTime(m.createdAt);
                const isUser = m.role === 'user';
                return (
                <div
                  key={m.id || i}
                  className={`flex flex-col max-w-[90%] gap-0.5 ${isUser ? 'ml-auto items-end' : 'items-start'}`}
                >
                  <div
                    className={`rounded-lg px-3 py-2 w-full ${
                    m.isErrorAlert
                      ? errorAlertBubbleClass(m.alertSeverity)
                      : isUser
                        ? 'bg-purple-600 text-white whitespace-pre-wrap break-words'
                        : 'bg-muted text-foreground'
                  }`}
                  >
                  {m.role === 'assistant' ? (
                    m.content ? (
                      <>
                        <AssistantMarkdown content={m.content} />
                        {m.streaming && (
                          <span className="inline-block w-2 h-4 ml-0.5 align-middle bg-foreground/50 animate-pulse" />
                        )}
                      </>
                    ) : (
                      <WaitingIndicator
                        statusText={m.statusText || '思考中…'}
                        elapsedSec={sending && i === visibleMessages.length - 1 ? waitSeconds : 0}
                      />
                    )
                  ) : (
                    m.content
                  )}
                  </div>
                  {timeLabel && (
                    <span
                      className={`text-[10px] text-muted-foreground tabular-nums px-1 ${
                        isUser ? 'text-right' : 'text-left'
                      }`}
                      title={m.createdAt}
                    >
                      {timeLabel}
                    </span>
                  )}
                </div>
              );
              })
            )}
          </div>

          {sending && (
            <div className="px-3 py-2 border-t bg-purple-500/5 text-xs text-muted-foreground flex items-center gap-2 shrink-0">
              <span className="inline-block w-3 h-3 border-2 border-purple-500 border-t-transparent rounded-full animate-spin shrink-0" />
              <span>
                AI 正在生成回复
                {waitSeconds > 0 ? `（${waitSeconds} 秒）` : ''}
                — v4-pro 深度思考约需 1-3 分钟
              </span>
            </div>
          )}

          <div className="p-3 border-t flex gap-2 shrink-0">
            <input
              className="flex-1 text-sm border rounded-md px-3 py-2 bg-background"
              placeholder={badge && badge.count > 0 ? '问：今天有什么报错？' : '输入问题…'}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && send()}
              disabled={sending}
            />
            <Button size="icon" onClick={send} disabled={sending || !input.trim()}>
              <Send className="w-4 h-4" />
            </Button>
          </div>
        </div>
      )}

      <button
        type="button"
        aria-label={badgeTitle}
        title={badgeTitle}
        onClick={() => setOpen((v) => !v)}
        className={`fixed z-[9000] bottom-6 right-6 flex items-center justify-center w-14 h-14 rounded-full bg-purple-600 text-white shadow-lg hover:bg-purple-700 transition-colors ${
          displayBadgeCount > 0
            ? badge?.kind === 'p0'
              ? 'ring-4 ring-red-500/60 animate-pulse'
              : 'ring-2 ring-amber-400/70 animate-pulse'
            : ''
        }`}
      >
        <Brain className="w-7 h-7 shrink-0" />
        {displayBadgeCount > 0 && (
          <span
            className={`absolute top-0 right-0 translate-x-1/4 -translate-y-1/4 min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-bold leading-none flex items-center justify-center shadow ring-2 ring-background pointer-events-none animate-bounce ${
              badge?.kind === 'p0' ? 'bg-red-600' : 'bg-amber-500 text-amber-950'
            }`}
            aria-label={`${displayBadgeCount} 条未读错误`}
          >
            {displayBadgeCount > 99 ? '99+' : displayBadgeCount}
          </span>
        )}
      </button>
    </>
  );
}
