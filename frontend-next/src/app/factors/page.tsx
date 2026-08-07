"use client";

import { useEffect, useState, useMemo } from "react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { FlaskConical, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
// [2026-08-05 v6 9.1] 因子报告卡雏形：admission 徽章 + 显著性 + 衰减 + 数据完整率
import FactorOverviewPanel from "@/components/factors/FactorOverviewPanel";

type Tab = "values" | "report";

type Factor = {
  name: string;
  value: number;
  normalized: number;
  category: string;
};

const SYMBOLS = ["BTC", "ETH", "SOL", "BNB"] as const;
type Symbol = (typeof SYMBOLS)[number];

export default function FactorsPage() {
  const [symbol, setSymbol] = useState<Symbol>("BTC");
  const [tab, setTab] = useState<Tab>("values");
  const [factors, setFactors] = useState<Factor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async (sym: Symbol) => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetch(`/api/factors/values/${sym}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setFactors(Array.isArray(json?.factors) ? json.factors : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setFactors([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load(symbol);
  }, [symbol]);

  // 按类别分组
  const grouped = useMemo(() => {
    const map = new Map<string, Factor[]>();
    for (const f of factors) {
      const cat = f.category ?? "其他";
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(f);
    }
    return Array.from(map.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [factors]);

  return (
    <div className="p-4 space-y-4">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FlaskConical className="w-5 h-5 text-primary" />
          <h1 className="text-lg font-bold">因子系统</h1>
        </div>
        <Button variant="outline" size="sm" onClick={() => load(symbol)} disabled={loading}>
          <RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />
          刷新
        </Button>
      </div>

      {/* 币种选择 */}
      <Card className="p-3">
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground mr-1">币种</span>
          {SYMBOLS.map((s) => (
            <Button
              key={s}
              variant={symbol === s ? "default" : "outline"}
              size="sm"
              className="font-mono text-xs h-7"
              onClick={() => setSymbol(s)}
            >
              {s}
            </Button>
          ))}
          <span className="ml-auto text-xs text-muted-foreground tabular-nums">
            {loading ? "加载中" : `${factors.length} 个因子`}
          </span>
        </div>
      </Card>

      {error && (
        <Card className="p-3 text-xs text-loss bg-loss/5 border-loss/30">
          加载失败：{error}
        </Card>
      )}

      {/* Tab 切换：实时因子值 / 因子报告卡 */}
      <Card className="p-2">
        <div className="flex items-center gap-1">
          <Button
            variant={tab === "values" ? "default" : "ghost"}
            size="sm"
            className="text-xs h-7"
            onClick={() => setTab("values")}
          >
            实时因子值
          </Button>
          <Button
            variant={tab === "report" ? "default" : "ghost"}
            size="sm"
            className="text-xs h-7"
            onClick={() => setTab("report")}
          >
            因子报告卡
          </Button>
          <span className="ml-auto text-xs text-muted-foreground">v6 9.1 admission 门槛可视化</span>
        </div>
      </Card>

      {tab === "report" ? (
        <FactorOverviewPanel symbol={symbol} />
      ) : (
      <>
      {/* 因子表格 — 按类别分组 */}
      {loading ? (
        <Card className="p-6 text-center text-xs text-muted-foreground">加载中...</Card>
      ) : grouped.length === 0 ? (
        <Card className="p-6 text-center text-xs text-muted-foreground">
          暂无因子数据
        </Card>
      ) : (
        <div className="space-y-3">
          {grouped.map(([category, items]) => (
            <Card key={category} className="p-0 overflow-hidden">
              <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/50 bg-muted/30">
                <Badge variant="secondary" className="bg-muted text-muted-foreground">
                  {category}
                </Badge>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {items.length} 个
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-muted-foreground border-b border-border/50">
                      <th className="px-4 py-2 font-medium">名称</th>
                      <th className="px-4 py-2 font-medium text-right">原始值</th>
                      <th className="px-4 py-2 font-medium text-right">归一化</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((f) => (
                      <tr key={f.name} className="border-b border-border/30">
                        <td className="px-4 py-2.5 font-mono text-xs">{f.name}</td>
                        <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground">
                          {formatNum(f.value)}
                        </td>
                        <td className="px-4 py-2.5 text-right">
                          <span
                            className={cn(
                              "tabular-nums font-medium",
                              f.normalized > 0
                                ? "text-profit"
                                : f.normalized < 0
                                  ? "text-loss"
                                  : "text-muted-foreground"
                            )}
                          >
                            {f.normalized > 0 ? "+" : ""}
                            {formatNum(f.normalized)}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ))}
        </div>
      )}
      </>
      )}
    </div>
  );
}

function formatNum(n: number): string {
  if (typeof n !== "number" || !isFinite(n)) return "—";
  if (Math.abs(n) >= 1000) return n.toFixed(0);
  if (Math.abs(n) >= 1) return n.toFixed(3);
  return n.toFixed(5);
}
