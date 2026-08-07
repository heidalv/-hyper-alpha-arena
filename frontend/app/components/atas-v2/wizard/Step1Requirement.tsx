/**
 * Step 1: Requirement — strategy name, account, symbols, timeframe, trading style.
 * Extracted from original AiStrategyWizard.tsx.
 */
import React, { useEffect, useState } from "react";
import { Input } from "@/app/components/ui/input";
import { Label } from "@/app/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/app/components/ui/select";
import { Textarea } from "@/app/components/ui/textarea";
import type { StepProps, AccountInfo, SymbolInfo } from "./types";

// Popular symbols fallback list
const POPULAR_SYMBOLS: SymbolInfo[] = [
  { symbol: "BTC", name: "Bitcoin" },
  { symbol: "ETH", name: "Ethereum" },
  { symbol: "SOL", name: "Solana" },
  { symbol: "DOGE", name: "Dogecoin" },
  { symbol: "XRP", name: "Ripple" },
  { symbol: "BNB", name: "BNB" },
];

const TIMEFRAMES = [
  { value: "5m", label: "5 分钟 (超短线)" },
  { value: "15m", label: "15 分钟 (短线)" },
  { value: "1h", label: "1 小时 (中线)" },
  { value: "4h", label: "4 小时 (长线)" },
  { value: "1d", label: "1 天 (趋势)" },
];

export const Step1Requirement: React.FC<StepProps> = ({
  data, updateData,
}) => {
  const [accounts, setAccounts] = useState<AccountInfo[]>([]);
  const [symbols, setSymbols] = useState<SymbolInfo[]>(POPULAR_SYMBOLS);

  useEffect(() => {
    fetch("/api/accounts")
      .then((r) => r.json())
      .then(setAccounts)
      .catch(() => {});
  }, []);

  return (
    <div className="space-y-4">
      {/* Strategy name */}
      <div>
        <Label htmlFor="strat-name">策略名称 *</Label>
        <Input
          id="strat-name"
          placeholder="例如: BTC 中线趋势策略"
          value={data.name}
          onChange={(e) => updateData({ name: e.target.value })}
        />
      </div>

      {/* Description */}
      <div>
        <Label htmlFor="strat-desc">策略描述</Label>
        <Textarea
          id="strat-desc"
          placeholder="描述策略的核心逻辑..."
          value={data.description}
          onChange={(e) => updateData({ description: e.target.value })}
          rows={3}
        />
      </div>

      {/* Account */}
      <div>
        <Label>交易账户 *</Label>
        <Select
          value={data.accountId?.toString() ?? ""}
          onValueChange={(v) => updateData({ accountId: parseInt(v) })}
        >
          <SelectTrigger>
            <SelectValue placeholder="选择账户" />
          </SelectTrigger>
          <SelectContent>
            {accounts.map((a) => (
              <SelectItem key={a.id} value={a.id.toString()}>
                {a.name} (ID: {a.id})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Primary symbol */}
      <div>
        <Label>主交易对</Label>
        <Select
          value={data.primarySymbol}
          onValueChange={(v) => updateData({ primarySymbol: v })}
        >
          <SelectTrigger>
            <SelectValue placeholder="选择交易对" />
          </SelectTrigger>
          <SelectContent>
            {symbols.map((s) => (
              <SelectItem key={s.symbol} value={s.symbol}>
                {s.symbol} — {s.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Timeframe */}
      <div>
        <Label>交易周期</Label>
        <Select
          value={data.timeframe}
          onValueChange={(v) => updateData({ timeframe: v })}
        >
          <SelectTrigger>
            <SelectValue placeholder="选择周期" />
          </SelectTrigger>
          <SelectContent>
            {TIMEFRAMES.map((tf) => (
              <SelectItem key={tf.value} value={tf.value}>
                {tf.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
};

export default Step1Requirement;
