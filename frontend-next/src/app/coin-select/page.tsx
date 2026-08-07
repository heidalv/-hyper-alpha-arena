"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { coinSelectApi } from "@/lib/api";
import { useAuthStore } from "@/lib/stores/auth";
import { cn } from "@/lib/utils";
import {
  Brain,
  Loader2,
  RefreshCw,
  Shield,
  Sparkles,
  AlertTriangle,
} from "lucide-react";

type Horizon = "scalp" | "midlong";

type BoardItem = {
  id: number;
  symbol: string;
  horizon: string;
  tier_label?: string;
  score?: number;
  factor_match?: number | null;
  factor_detail?: { top?: unknown[]; reason?: string; n?: number; alpha?: number };
  ai_verdict?: string;
  ai_reason?: string;
  confidence?: number;
  direction_bias?: string;
  risk_notes?: string;
  invalidation?: string;
  valid_until?: string | null;
  adopt_count?: number;
  trap_soft?: number | null;
  mtf_confluence?: number | null;
  gate?: string | null;
  liquidity?: number | null;
  hist_hit_rate?: number | null;
  hist_avg_pnl_24h?: number | null;
  hist_samples?: number;
  degraded?: string | null;
};

export default function CoinSelectPage() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";
  const isVip = (user?.tier || "").toLowerCase() === "vip" || isAdmin;

  const [horizon, setHorizon] = useState<Horizon>("scalp");
  const [enabled, setEnabled] = useState(false);
  const [autoFollow, setAutoFollow] = useState(false);
  const [defaultSession, setDefaultSession] = useState<string>("");
  const [items, setItems] = useState<BoardItem[]>([]);
  const [lastScan, setLastScan] = useState<any>(null);
  const [sessions, setSessions] = useState<any[]>([]);
  const [sessionHint, setSessionHint] = useState<string | null>(null);
  const [adoptSession, setAdoptSession] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [admin, setAdmin] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [boardDegraded, setBoardDegraded] = useState<string | null>(null);
  const [minScore, setMinScore] = useState("");
  const [maxTrap, setMaxTrap] = useState("");
  const [sortBy, setSortBy] = useState("confidence");
  const [verdictFilter, setVerdictFilter] = useState("");

  const canUse = isVip && (enabled || isAdmin);

  const load = useCallback(async () => {
    if (!isVip) {
      setLoading(false);
      setError("此功能仅 VIP / 管理员可用");
      return;
    }
    try {
      setLoading(true);
      setError(null);
      const settings = await coinSelectApi.settings();
      setEnabled(!!settings.enabled || settings.coin_select_enabled === "true");
      setAutoFollow(
        String(settings.auto_follow_scalp || "").toLowerCase() === "true" ||
          settings.auto_follow_scalp === true
      );
      setDefaultSession(settings.default_session_id || "");

      const sess = await coinSelectApi.sessions();
      setSessions(sess.sessions || []);
      setSessionHint(sess.hint || null);
      if (!adoptSession && (settings.default_session_id || sess.sessions?.[0]?.session_id)) {
        setAdoptSession(settings.default_session_id || sess.sessions[0].session_id);
      }

      const on =
        !!settings.is_admin ||
        !!settings.enabled ||
        String(settings.coin_select_enabled || "").toLowerCase() === "true";
      if (on) {
        const board = await coinSelectApi.board(horizon, {
          min_score: minScore ? Number(minScore) : undefined,
          max_trap: maxTrap ? Number(maxTrap) : undefined,
          verdict: verdictFilter || undefined,
          sort_by: sortBy,
        });
        setItems(board.items || []);
        setLastScan(board.last_scan || null);
        setBoardDegraded(board.degraded || null);
      } else {
        setItems([]);
        setLastScan(null);
        setBoardDegraded(null);
      }

      if (settings.is_admin) {
        try {
          setAdmin(await coinSelectApi.adminDetail());
        } catch {
          setAdmin(null);
        }
      }
    } catch (e: any) {
      const detail = e?.detail || e?.message || String(e);
      if (/not found/i.test(String(detail)) || e?.status === 404) {
        setError("选币接口未找到（404）。请重启后端后再刷新本页。");
      } else {
        setError(detail);
      }
    } finally {
      setLoading(false);
    }
  }, [horizon, isVip, adoptSession, minScore, maxTrap, sortBy, verdictFilter]);

  useEffect(() => {
    load();
  }, [horizon, minScore, maxTrap, sortBy, verdictFilter]); // eslint-disable-line react-hooks/exhaustive-deps

  const strong = useMemo(
    () => items.filter((i) => i.ai_verdict === "approve" || i.tier_label === "strong"),
    [items]
  );
  const watch = useMemo(
    () => items.filter((i) => !(i.ai_verdict === "approve" || i.tier_label === "strong")),
    [items]
  );

  const patchSettings = async (patch: Record<string, unknown>) => {
    setBusy(true);
    setMsg(null);
    try {
      const s = await coinSelectApi.patchSettings(patch);
      setEnabled(!!s.enabled || s.coin_select_enabled === "true");
      setAutoFollow(
        String(s.auto_follow_scalp || "").toLowerCase() === "true" || s.auto_follow_scalp === true
      );
      setDefaultSession(s.default_session_id || "");
      setMsg("设置已保存");
      await load();
    } catch (e: any) {
      setError(e?.detail || e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const adopt = async (item: BoardItem) => {
    if (!adoptSession) {
      setError("请先选择要加入的交易会话");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await coinSelectApi.adopt({
        symbol: item.symbol,
        horizon: item.horizon || horizon,
        session_id: adoptSession,
        candidate_id: item.id,
      });
      setMsg(
        `已将 ${item.symbol} 加入${horizon === "scalp" ? "短线" : "长线"}会话`
      );
      await load();
    } catch (e: any) {
      setError(e?.detail || e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const scanNow = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await coinSelectApi.scanNow();
      setMsg(r.ok ? `扫描完成：短线 ${r.board_scalp} / 长线 ${r.board_midlong}` : r.reason || "扫描未完成");
      await load();
    } catch (e: any) {
      setError(e?.detail || e?.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!isVip) {
    return (
      <div className="p-6 max-w-xl">
        <Card className="p-6 space-y-2">
          <div className="flex items-center gap-2 text-lg font-semibold">
            <Sparkles className="w-5 h-5 text-amber-400" />
            VIP AI 选币
          </div>
          <p className="text-sm text-muted-foreground">
            这是 VIP 专属特色：平台共用深度选币看板。请升级到 VIP 后使用。
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4 max-w-6xl mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Brain className="w-5 h-5 text-amber-400" />
          <h1 className="text-lg font-bold">VIP AI 选币</h1>
          <Badge variant="outline" className="text-[10px]">
            平台共用 · 管理员 LLM
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => load()} disabled={loading || busy}>
            <RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />
            刷新
          </Button>
          {isAdmin && (
            <Button size="sm" onClick={scanNow} disabled={busy}>
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Shield className="w-3.5 h-3.5" />}
              立即重扫
            </Button>
          )}
        </div>
      </div>

      <Card className="p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-6">
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={enabled}
              disabled={busy || isAdmin}
              onCheckedChange={(v) => patchSettings({ enabled: !!v })}
            />
            启用选币看板
            {isAdmin && <span className="text-[10px] text-muted-foreground">（管理员始终可见）</span>}
          </label>
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={autoFollow}
              disabled={busy || !enabled}
              onCheckedChange={(v) =>
                patchSettings({
                  auto_follow_scalp: !!v,
                  default_session_id: adoptSession || defaultSession || undefined,
                })
              }
            />
            自动跟投短线
            <span className="text-[10px] text-muted-foreground">（默认关；长线永不自动）</span>
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="text-muted-foreground">加入会话</span>
          <select
            className="h-8 rounded-md border border-border bg-background px-2 text-xs min-w-[220px]"
            value={adoptSession}
            onChange={(e) => {
              setAdoptSession(e.target.value);
              patchSettings({ default_session_id: e.target.value || null });
            }}
          >
            <option value="">选择会话…</option>
            {sessions.map((s) => (
              <option key={s.session_id} value={s.session_id}>
                {(s.account_name || `账户${s.account_id}`) +
                  ` · ${s.session_id} · ${s.status} · 固定${(s.symbols || []).length}/自动${
                    (s.auto_coin_symbols || []).length
                  }`}
              </option>
            ))}
          </select>
          <span className="text-[11px] text-muted-foreground">
            上次扫描：{lastScan?.finished_at || "暂无"}
            {lastScan?.duration_sec != null ? ` · ${lastScan.duration_sec}s` : ""}
          </span>
        </div>
        {sessionHint && (
          <p className="text-xs text-amber-400/90">{sessionHint}</p>
        )}
        <p className="text-xs text-muted-foreground">
          只能加入「当前登录用户」自己的交易会话（账户隔离）。行情只读数据中心；选币 LLM 用管理员配置；交易决策仍用你自己的 Key。
        </p>
      </Card>

      {!canUse && (
        <Card className="p-4 flex items-start gap-2 text-sm">
          <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5" />
          请先打开「启用选币看板」开关，才能查看完整推荐与加入会话。
        </Card>
      )}

      {error && (
        <div className="text-sm text-destructive bg-destructive/10 rounded-md px-3 py-2">{error}</div>
      )}
      {msg && <div className="text-sm text-emerald-400 bg-emerald-500/10 rounded-md px-3 py-2">{msg}</div>}

      <div className="flex gap-2">
        {(["scalp", "midlong"] as Horizon[]).map((h) => (
          <Button
            key={h}
            size="sm"
            variant={horizon === h ? "default" : "outline"}
            onClick={() => setHorizon(h)}
          >
            {h === "scalp" ? "短线推荐" : "长线推荐"}
          </Button>
        ))}
      </div>

      {boardDegraded && canUse && (
        <Card className="p-3 flex items-start gap-2 text-sm border-amber-500/40 bg-amber-500/5">
          <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5" />
          <div>
            {boardDegraded === "stale_board_need_rescan" ? (
              <>
                <div className="font-medium text-amber-300">看板数据偏旧</div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  管理员 LLM 已就绪，但上一轮扫描误判为无 Key。请管理员点「立即重扫」刷新为真正的 AI 理由。
                </p>
              </>
            ) : boardDegraded === "no_llm_response" ? (
              <>
                <div className="font-medium text-amber-300">AI 审核未返回结果</div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  管理员 LLM 有 Key，但本轮未解析出可用 JSON（no_llm_response）。请点「立即重扫」；若反复失败，确认模型为 deepseek-v4-flash。
                </p>
              </>
            ) : (
              <>
                <div className="font-medium text-amber-300">规则分 · 非 AI</div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  当前看板未使用管理员 LLM（{boardDegraded}）。分数来自共用 RankEngine，不是 AI 深度审核。
                </p>
              </>
            )}
          </div>
        </Card>
      )}

      {canUse && (
        <Card className="p-3 overflow-x-auto">
          <div className="flex flex-nowrap items-center gap-x-4 gap-y-0 text-xs whitespace-nowrap min-w-min">
            <span className="text-muted-foreground shrink-0">筛选</span>
            <label className="inline-flex items-center gap-1.5 shrink-0">
              <span className="text-muted-foreground">最低分</span>
              <input
                className="h-7 w-16 shrink-0 rounded border border-border bg-background px-1.5"
                value={minScore}
                onChange={(e) => setMinScore(e.target.value)}
                placeholder="0.3"
              />
            </label>
            <label className="inline-flex items-center gap-1.5 shrink-0">
              <span className="text-muted-foreground">陷阱上限</span>
              <input
                className="h-7 w-16 shrink-0 rounded border border-border bg-background px-1.5"
                value={maxTrap}
                onChange={(e) => setMaxTrap(e.target.value)}
                placeholder="0.55"
              />
            </label>
            <select
              className="h-7 shrink-0 rounded border border-border bg-background px-2"
              value={verdictFilter}
              onChange={(e) => setVerdictFilter(e.target.value)}
            >
              <option value="">全部 verdict</option>
              <option value="approve">approve</option>
              <option value="watch">watch</option>
            </select>
            <select
              className="h-7 shrink-0 rounded border border-border bg-background px-2"
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
            >
              <option value="confidence">按信心</option>
              <option value="score">按分数</option>
              <option value="liquidity">按流动性</option>
              <option value="trap">按陷阱(低优)</option>
              <option value="hist_hit">按历史命中</option>
            </select>
          </div>
        </Card>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground py-8 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" /> 加载中…
        </div>
      ) : canUse ? (
        <div className="space-y-4">
          <Section title="强烈推荐" items={strong} onAdopt={adopt} busy={busy} />
          <Section title="观察列表" items={watch} onAdopt={adopt} busy={busy} muted />
          {!items.length && (
            <Card className="p-6 text-sm text-muted-foreground text-center">
              暂无推荐。管理员可点「立即重扫」，或等待平台定时扫描。
            </Card>
          )}
        </div>
      ) : null}

      {isAdmin && admin && (
        <Card className="p-4 space-y-3 border-amber-500/30">
          <div className="flex items-center gap-2 font-semibold text-sm">
            <Shield className="w-4 h-4 text-amber-400" />
            管理员面板
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
            <Stat label="管理员租户" value={String(admin.admin_tenant_id ?? "-")} />
            <Stat label="LLM 就绪" value={admin.llm_ready ? "是" : "否"} />
            <Stat label="模型" value={admin.llm_model || "-"} />
            <Stat label="因子暴露" value={String(admin.factor_exposure_hint || "-")} />
          </div>
          {(admin.adopt_stats || []).length > 0 && (
            <div className="text-xs">
              <div className="text-muted-foreground mb-1">VIP 采纳统计（Top）</div>
              <div className="flex flex-wrap gap-2">
                {(admin.adopt_stats as { symbol: string; horizon: string; count: number }[])
                  .slice(0, 12)
                  .map((a) => (
                    <Badge key={`${a.symbol}-${a.horizon}`} variant="outline" className="font-mono text-[10px]">
                      {a.symbol}/{a.horizon} ×{a.count}
                    </Badge>
                  ))}
              </div>
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-muted-foreground">
                <tr className="text-left border-b border-border">
                  <th className="py-1 pr-2">scan</th>
                  <th className="py-1 pr-2">状态</th>
                  <th className="py-1 pr-2">扫描</th>
                  <th className="py-1 pr-2">AI</th>
                  <th className="py-1 pr-2">短/长</th>
                  <th className="py-1 pr-2">耗时</th>
                  <th className="py-1">错误</th>
                </tr>
              </thead>
              <tbody>
                {(admin.scans || []).slice(0, 8).map((s: any) => (
                  <tr key={s.scan_id} className="border-b border-border/50">
                    <td className="py-1 pr-2 font-mono">{s.scan_id}</td>
                    <td className="py-1 pr-2">{s.status}</td>
                    <td className="py-1 pr-2">{s.candidates_scanned}</td>
                    <td className="py-1 pr-2">{s.candidates_ai}</td>
                    <td className="py-1 pr-2">
                      {s.board_scalp}/{s.board_midlong}
                    </td>
                    <td className="py-1 pr-2">{s.duration_sec ?? "-"}</td>
                    <td className="py-1 text-destructive truncate max-w-[180px]">
                      {s.error_message || ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {(admin.board?.items || [])
            .filter((i: BoardItem) => i.ai_verdict === "reject")
            .slice(0, 5)
            .length > 0 && (
            <div className="text-xs text-muted-foreground">
              含被拒候选（仅管理员可见）：{" "}
              {(admin.board.items as BoardItem[])
                .filter((i) => i.ai_verdict === "reject")
                .slice(0, 8)
                .map((i) => i.symbol)
                .join(", ")}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/40 px-2 py-1.5">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className="font-mono truncate">{value}</div>
    </div>
  );
}

function Section({
  title,
  items,
  onAdopt,
  busy,
  muted,
}: {
  title: string;
  items: BoardItem[];
  onAdopt: (i: BoardItem) => void;
  busy: boolean;
  muted?: boolean;
}) {
  if (!items.length) return null;
  return (
    <div className="space-y-2">
      <h2 className={cn("text-sm font-semibold", muted && "text-muted-foreground")}>{title}</h2>
      <div className="grid gap-3 md:grid-cols-2">
        {items.map((item) => (
          <Card key={`${item.id}-${item.symbol}`} className="p-3 space-y-2">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-mono font-bold text-base">{item.symbol}</span>
                <Badge variant="outline" className="text-[10px]">
                  {item.direction_bias || "neutral"}
                </Badge>
                <Badge
                  className={cn(
                    "text-[10px]",
                    item.ai_verdict === "approve"
                      ? "bg-emerald-500/20 text-emerald-300"
                      : "bg-muted text-muted-foreground"
                  )}
                >
                  {item.ai_verdict || "watch"}
                </Badge>
                {item.confidence != null && (
                  <span className="text-[10px] text-muted-foreground">
                    信心 {(Number(item.confidence) * 100).toFixed(0)}%
                  </span>
                )}
              </div>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => onAdopt(item)}>
                加入会话
              </Button>
            </div>
            <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
              <span>分数 {item.score != null ? Number(item.score).toFixed(3) : "-"}</span>
              <span>
                因子{" "}
                {item.factor_match != null
                  ? Number(item.factor_match).toFixed(3)
                  : item.factor_detail?.reason === "no_klines"
                    ? "缺K线"
                    : item.factor_detail?.reason === "disabled"
                      ? "未启用"
                      : item.factor_detail?.reason === "no_active_or_eval_fail"
                        ? "无活跃因子"
                        : "—"}
              </span>
              {item.trap_soft != null && Number(item.trap_soft) > 0 && (
                <span className="text-amber-400">陷阱 {Number(item.trap_soft).toFixed(2)}</span>
              )}
              {item.gate && item.gate !== "pass" && (
                <span className="text-amber-400">门控 {item.gate}</span>
              )}
              {item.hist_samples != null && item.hist_samples > 0 ? (
                <span>
                  24h命中{" "}
                  {item.hist_hit_rate != null
                    ? `${(Number(item.hist_hit_rate) * 100).toFixed(0)}%`
                    : "—"}{" "}
                  (n={item.hist_samples})
                  {item.hist_avg_pnl_24h != null &&
                    ` · 均PnL ${Number(item.hist_avg_pnl_24h).toFixed(2)}%`}
                </span>
              ) : (
                <span title="需会话跟投并满 24 小时后才有命中率；分数/因子不受影响">
                  绩效待积累（跟投满24h）
                </span>
              )}
              {item.degraded && (
                <Badge variant="outline" className="text-[9px] text-amber-400 border-amber-500/40">
                  规则分·非AI
                </Badge>
              )}
              {item.adopt_count != null && item.adopt_count > 0 && (
                <span>已跟投 {item.adopt_count}</span>
              )}
              {item.valid_until && <span>有效至 {item.valid_until}</span>}
            </div>
            {Array.isArray(item.factor_detail?.top) && item.factor_detail!.top!.length > 0 && (
              <div className="text-[10px] text-muted-foreground truncate">
                因子贡献：
                {item.factor_detail!.top!.slice(0, 4)
                  .map((f: any) => (typeof f === "string" ? f : f?.name || f?.id || JSON.stringify(f)))
                  .join(" · ")}
              </div>
            )}
            <p className="text-xs leading-relaxed whitespace-pre-wrap">{item.ai_reason || "暂无 AI 理由"}</p>
            {item.risk_notes && (
              <p className="text-[11px] text-amber-400/90">风险：{item.risk_notes}</p>
            )}
            {item.invalidation && (
              <p className="text-[11px] text-muted-foreground">失效条件：{item.invalidation}</p>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
