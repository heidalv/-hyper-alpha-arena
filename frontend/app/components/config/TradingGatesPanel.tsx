"use client";

/**
 * 交易门禁配置面板
 *
 * 设计依据（业界标准，非瞎猜）：
 * - Hard gate（硬门禁）：风控底线，违反则绝对否决开仓。业界共识 ≤5 个非冗余条件
 *   （QuantConnect/Build Alpha 警告：门禁过多=过拟合=实盘永不触发）
 * - Soft gate（软门禁）：判断性信号，进入综合 score 影响置信度但不绝对否决
 *   （r/algotrading: regime as soft filter not hard gate）
 * - 方向一致性门禁默认关闭：MTFA 标准是"高周期定势、低周期找进场"，pullback 逆小势
 *   进场是教科书推荐做法，主流平台无一内置此门禁（BabyPips/Investopedia/Advisorpedia）
 */
import { useEffect, useState, useCallback, type ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Badge } from "../ui/badge";
import { Input } from "../ui/input";
import { Button } from "../ui/button";
import { Switch } from "../ui/switch";
import { Shield, RefreshCw, TrendingUp, Brain, Layers, RotateCcw, Info } from "lucide-react";

interface Gate {
  key: string;
  layer: string;
  category: string; // hard | soft
  name: string;
  desc: string;
  default: number;
  current: number;
  type: string;
  min: number;
  max: number;
}

export default function TradingGatesPanel() {
  const [gates, setGates] = useState<Gate[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);

  const fetchGates = useCallback(async () => {
    try {
      const res = await fetch("/api/config/trading-gates");
      const data = await res.json();
      setGates(data.gates || []);
    } catch {
      setGates([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchGates();
  }, [fetchGates]);

  const handleSave = async (key: string, value: string | number) => {
    setSaving(key);
    try {
      await fetch("/api/config/trading-gates", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, value: typeof value === "string" ? parseFloat(value) : value }),
      });
      setEditing((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      await fetchGates();
    } finally {
      setSaving(null);
    }
  };

  // 布尔开关类门禁（min=0,max=1）：点击 Switch 直接保存
  const handleToggle = (g: Gate) => {
    const newVal = g.current === 1 ? 0 : 1;
    handleSave(g.key, newVal);
  };

  // 重置全部为默认值
  const handleResetAll = async () => {
    if (!confirm("确认将所有门禁重置为默认值？")) return;
    for (const g of gates) {
      if (g.current !== g.default) {
        await fetch("/api/config/trading-gates", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: g.key, value: g.default }),
        });
      }
    }
    await fetchGates();
  };

  const isBoolSwitch = (g: Gate) => g.min === 0 && g.max === 1;

  const sharedGates = gates.filter((g) => g.layer === "共享");
  const scalpGates = gates.filter((g) => g.layer === "短线");
  const mltoGates = gates.filter((g) => g.layer === "中长线");
  const hardCount = gates.filter((g) => g.category === "hard").length;
  const softCount = gates.filter((g) => g.category === "soft").length;

  const renderGate = (g: Gate) => {
    const isEditing = editing[g.key] !== undefined;
    const displayVal = isEditing ? editing[g.key] : String(g.current);
    const changed = editing[g.key] !== undefined && parseFloat(editing[g.key]) !== g.current;
    const boolSw = isBoolSwitch(g);
    const isHard = g.category === "hard";

    return (
      <div
        key={g.key}
        className="flex items-center justify-between gap-3 py-2.5 border-b border-border/40 last:border-0"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium">{g.name}</span>
            <Badge
              variant={isHard ? "destructive" : "secondary"}
              className="text-[10px] px-1.5 py-0"
            >
              {isHard ? "硬门禁" : "软门禁"}
            </Badge>
            {boolSw && (
              <Badge variant={g.current === 1 ? "default" : "outline"} className="text-[10px] px-1.5 py-0">
                {g.current === 1 ? "开启" : "关闭"}
              </Badge>
            )}
            {!boolSw && (
              <Badge variant={changed ? "default" : "secondary"} className="text-xs">
                {g.current}
              </Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5 leading-snug">{g.desc}</p>
          <span className="text-[10px] text-muted-foreground/50 font-mono">{g.key}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {boolSw ? (
            <Switch
              checked={g.current === 1}
              onCheckedChange={() => handleToggle(g)}
              disabled={saving === g.key}
            />
          ) : (
            <>
              <Input
                type="number"
                step={g.type === "float" ? "0.01" : "1"}
                min={g.min}
                max={g.max}
                value={displayVal}
                onChange={(e) => setEditing((p) => ({ ...p, [g.key]: e.target.value }))}
                className="w-20 h-8 text-sm"
              />
              {changed && (
                <Button
                  size="sm"
                  variant="default"
                  className="h-8 px-2"
                  disabled={saving === g.key}
                  onClick={() => handleSave(g.key, editing[g.key])}
                >
                  {saving === g.key ? "..." : "保存"}
                </Button>
              )}
            </>
          )}
        </div>
      </div>
    );
  };

  const renderGroup = (
    title: string,
    icon: ReactNode,
    gates: Gate[]
  ) => (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="text-sm flex items-center gap-2">
          {icon}
          {title}
          <Badge variant="secondary" className="text-xs">{gates.length} 道</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">{gates.map(renderGate)}</CardContent>
    </Card>
  );

  if (loading) {
    return <p className="text-sm text-muted-foreground py-12 text-center">加载门禁配置…</p>;
  }

  return (
    <div className="space-y-4 max-w-6xl">
      {/* 头部 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="w-5 h-5 text-primary" />
          <h3 className="text-base font-semibold">交易门禁配置</h3>
          <Badge variant="outline" className="text-xs">
            {gates.length} 道 · 硬{hardCount} / 软{softCount}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={handleResetAll} className="h-8">
            <RotateCcw className="w-3.5 h-3.5 mr-1" />
            重置默认
          </Button>
          <Button variant="ghost" size="sm" onClick={fetchGates} className="h-8">
            <RefreshCw className="w-3.5 h-3.5 mr-1" />
            刷新
          </Button>
        </div>
      </div>

      {/* 业界标准说明卡 */}
      <Card className="bg-blue-500/5 border-blue-500/20">
        <CardContent className="py-3 px-4">
          <div className="flex items-start gap-2">
            <Info className="w-4 h-4 text-blue-400 mt-0.5 shrink-0" />
            <div className="text-xs text-muted-foreground space-y-1 leading-relaxed">
              <p>
                <span className="font-semibold text-foreground">门禁分层原则（业界标准）：</span>
                <Badge variant="destructive" className="text-[10px] mx-1 px-1.5 py-0">硬门禁</Badge>
                风控底线（盈亏比/单笔风险/杠杆），违反则绝对否决；
                <Badge variant="secondary" className="text-[10px] mx-1 px-1.5 py-0">软门禁</Badge>
                判断性信号，进综合置信度但不绝对否决。
              </p>
              <p>
                <span className="font-semibold text-foreground">方向一致性门禁默认关闭</span>
                —— 业界 MTFA 标准是"高周期定势、低周期找进场"，pullback 逆小势进场是教科书推荐做法，
                主流平台（Freqtrade/Hummingbot/TradingAgents）无一内置此门禁。如需开启可在下方中长线分组切换。
              </p>
              <p className="text-muted-foreground/70">
                依据：BabyPips/Investopedia/Advisorpedia MTFA 指南 · QuantConnect 过拟合警告 · r/algotrading hard/soft gate 讨论 · arXiv 2412.20138 TradingAgents
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 门禁分组 */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {renderGroup(
          "共享硬门禁",
          <Layers className="w-4 h-4 text-red-500" />,
          sharedGates
        )}
        {renderGroup(
          "短线因子门禁",
          <TrendingUp className="w-4 h-4 text-blue-500" />,
          scalpGates
        )}
        {renderGroup(
          "中长线 AI 门禁",
          <Brain className="w-4 h-4 text-purple-500" />,
          mltoGates
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        修改数值后点击「保存」即时生效（写入 .env + 热更新环境变量，无需重启）。
        开关类门禁点击 Switch 直接保存。原系统 17+ 道门禁精简标准化为 24 道可配置项，删除了冗余的方向一致性硬门禁。
      </p>
    </div>
  );
}
