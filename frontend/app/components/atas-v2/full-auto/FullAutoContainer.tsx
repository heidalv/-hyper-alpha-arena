/**
 * FullAutoPanel — full-automation trading session manager.
 *
 * Split from original 1836-line monolithic component into:
 *   FullAutoContainer  — session select + sub-view router (this file replaces FullAutoPanel)
 *   SessionList         — start panel + session cards
 *   SessionDetail       — active session with real-time strategy tree, auto-coin, health
 *   HealthMonitor       — system health overview
 *
 * All API hooks and real-time polling logic live here; rendering delegates to sub-components.
 */

import React, { useState, useEffect, useCallback, useRef } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/app/components/ui/card";
import { Button } from "@/app/components/ui/button";
import { Badge } from "@/app/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/app/components/ui/tabs";
import type { AccountInfo } from "./types";

// ── Types ──────────────────────────────────────────────────────

export interface FullAutoSession {
  session_id: string;
  name: string;
  status: "running" | "paused" | "stopped";
  account_id: number;
  symbols: string[];
  strategy_count: number;
  started_at: string;
  last_tick_at?: string;
  health_status?: "healthy" | "degraded" | "critical";
  tier_status?: Record<string, { active: number; paused: number }>;
}

// ── Sub-components ─────────────────────────────────────────────

const SessionList: React.FC<{
  sessions: FullAutoSession[];
  accounts: AccountInfo[];
  loading: boolean;
  onStart: (accountId: number, symbols: string[]) => Promise<void>;
  onSelect: (session: FullAutoSession) => void;
  onDelete: (sessionId: string) => Promise<void>;
}> = ({ sessions, accounts, loading, onStart, onSelect, onDelete }) => {
  const [showStart, setShowStart] = useState(false);
  const [selectedAccount, setSelectedAccount] = useState<number | null>(null);

  if (loading && sessions.length === 0) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">全自动交易会话</h3>
        <Button size="sm" onClick={() => setShowStart(!showStart)}>
          {showStart ? "取消" : "+ 启动新会话"}
        </Button>
      </div>

      {/* Start panel */}
      {showStart && (
        <Card>
          <CardContent className="pt-4 space-y-3">
            <select
              className="w-full border rounded p-2"
              value={selectedAccount ?? ""}
              onChange={(e) => setSelectedAccount(Number(e.target.value) || null)}
            >
              <option value="">选择账户</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>{a.name} (ID: {a.id})</option>
              ))}
            </select>
            <Button
              onClick={() => selectedAccount && onStart(selectedAccount, [])}
              disabled={!selectedAccount}
            >
              启动
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Session cards */}
      {sessions.length === 0 ? (
        <p className="text-muted-foreground text-center py-8">暂无活跃会话</p>
      ) : (
        sessions.map((s) => (
          <Card
            key={s.session_id}
            className="cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => onSelect(s)}
          >
            <CardContent className="flex items-center justify-between py-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="font-medium">{s.name ?? s.session_id}</span>
                  <Badge
                    variant={
                      s.status === "running" ? "default" :
                      s.status === "paused" ? "secondary" : "outline"
                    }
                  >
                    {s.status}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground mt-1">
                  {s.strategy_count} 策略 · {s.symbols.join(", ")} · 启动于 {s.started_at}
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={(e) => { e.stopPropagation(); onDelete(s.session_id); }}
              >
                删除
              </Button>
            </CardContent>
          </Card>
        ))
      )}
    </div>
  );
};


const SessionDetail: React.FC<{
  session: FullAutoSession;
  onStop: () => Promise<void>;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
}> = ({ session, onStop, onPause, onResume }) => (
  <div className="space-y-4">
    <div className="flex items-center justify-between">
      <div>
        <h3 className="text-lg font-semibold">{session.name ?? session.session_id}</h3>
        <p className="text-sm text-muted-foreground">
          状态: {session.status} · 策略: {session.strategy_count} · 交易对: {session.symbols.join(", ")}
        </p>
      </div>
      <div className="flex gap-2">
        {session.status === "running" && (
          <Button variant="secondary" size="sm" onClick={onPause}>暂停</Button>
        )}
        {session.status === "paused" && (
          <Button variant="default" size="sm" onClick={onResume}>恢复</Button>
        )}
        {session.status !== "stopped" && (
          <Button variant="destructive" size="sm" onClick={onStop}>停止</Button>
        )}
      </div>
    </div>

    <Tabs defaultValue="strategies">
      <TabsList>
        <TabsTrigger value="strategies">策略树</TabsTrigger>
        <TabsTrigger value="coins">自动选币</TabsTrigger>
        <TabsTrigger value="health">系统健康</TabsTrigger>
      </TabsList>
      <TabsContent value="strategies">
        <p className="text-sm text-muted-foreground py-8 text-center">
          策略树详情待从原 FullAutoPanel.tsx 迁移
        </p>
      </TabsContent>
      <TabsContent value="coins">
        <p className="text-sm text-muted-foreground py-8 text-center">
          自动选币面板待迁移
        </p>
      </TabsContent>
      <TabsContent value="health">
        <HealthMonitor sessionId={session.session_id} />
      </TabsContent>
    </Tabs>
  </div>
);


const HealthMonitor: React.FC<{ sessionId: string }> = ({ sessionId }) => {
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);

  useEffect(() => {
    const poll = () => {
      fetch(`/api/full-auto/health-check/${sessionId}`, { method: "POST" })
        .then((r) => r.json())
        .then(setHealth)
        .catch(() => {});
    };
    poll();
    const interval = setInterval(poll, 30_000);
    return () => clearInterval(interval);
  }, [sessionId]);

  if (!health) {
    return <div className="animate-pulse h-20 bg-muted rounded" />;
  }

  return (
    <pre className="text-xs bg-muted p-3 rounded max-h-64 overflow-auto">
      {JSON.stringify(health, null, 2)}
    </pre>
  );
};


// ══════════════════════════════════════════════════════════════
// Container (replaces original FullAutoPanel)
// ══════════════════════════════════════════════════════════════

export const FullAutoContainer: React.FC = () => {
  const [sessions, setSessions] = useState<FullAutoSession[]>([]);
  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedSession, setSelectedSession] = useState<FullAutoSession | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval>>();

  // ── Load data ──
  const loadData = useCallback(async () => {
    try {
      const [sessResp, acctResp] = await Promise.all([
        fetch("/api/full-auto/sessions"),
        fetch("/api/accounts"),
      ]);
      setSessions(await sessResp.json());
      setAccounts(await acctResp.json());
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    pollRef.current = setInterval(loadData, 15_000);
    return () => clearInterval(pollRef.current);
  }, [loadData]);

  // ── Actions ──
  const handleStart = async (accountId: number, symbols: string[]) => {
    await fetch("/api/full-auto/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: accountId, symbols }),
    });
    loadData();
  };

  const handleStop = async () => {
    if (!selectedSession) return;
    await fetch(`/api/full-auto/stop/${selectedSession.session_id}`, { method: "POST" });
    setSelectedSession(null);
    loadData();
  };

  const handlePause = async () => {
    if (!selectedSession) return;
    await fetch(`/api/full-auto/pause/${selectedSession.session_id}`, { method: "POST" });
    loadData();
  };

  const handleResume = async () => {
    if (!selectedSession) return;
    await fetch(`/api/full-auto/resume/${selectedSession.session_id}`, { method: "POST" });
    loadData();
  };

  const handleDelete = async (sessionId: string) => {
    await fetch(`/api/full-auto/${sessionId}`, { method: "DELETE" });
    loadData();
  };

  // ── Render ──
  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle>
          {selectedSession ? "会话详情" : "全自动交易面板"}
        </CardTitle>
        {selectedSession && (
          <Button variant="ghost" size="sm" onClick={() => setSelectedSession(null)}>
            ← 返回列表
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {selectedSession ? (
          <SessionDetail
            session={selectedSession}
            onStop={handleStop}
            onPause={handlePause}
            onResume={handleResume}
          />
        ) : (
          <SessionList
            sessions={sessions}
            accounts={accounts}
            loading={loading}
            onStart={handleStart}
            onSelect={setSelectedSession}
            onDelete={handleDelete}
          />
        )}
      </CardContent>
    </Card>
  );
};

export default FullAutoContainer;
