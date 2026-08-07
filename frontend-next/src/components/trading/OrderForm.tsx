"use client";

import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { TrendingUp, TrendingDown, Loader2 } from "lucide-react";
import { useState } from "react";
import { paperApi } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import { cn } from "@/lib/utils";

export function OrderForm({ accountId }: { accountId: number }) {
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [symbol, setSymbol] = useState("BTC");
  const [quantity, setQuantity] = useState("0.001");
  const [leverage, setLeverage] = useState("10");
  const [tpPrice, setTpPrice] = useState("");
  const [slPrice, setSlPrice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const qc = useQueryClient();

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    setSuccess(null);
    try {
      await paperApi.placeOrder({
        account_id: accountId,
        symbol: symbol.toUpperCase(),
        side,
        quantity: parseFloat(quantity),
        leverage: parseInt(leverage) || 1,
        tp_price: tpPrice ? parseFloat(tpPrice) : undefined,
        sl_price: slPrice ? parseFloat(slPrice) : undefined,
      });
      setSuccess(`${symbol} ${side === "buy" ? "买入" : "卖出"} ${quantity} 成功`);
      // 刷新数据
      qc.invalidateQueries({ queryKey: ["positions", accountId] });
      qc.invalidateQueries({ queryKey: ["balance", accountId] });
      qc.invalidateQueries({ queryKey: ["orders", accountId] });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card className="p-4">
      <div className="text-sm font-medium mb-3">手动下单</div>

      {/* 方向选择 */}
      <div className="flex gap-2 mb-3">
        <button
          onClick={() => setSide("buy")}
          className={cn(
            "flex-1 flex items-center justify-center gap-1.5 py-2 rounded text-sm font-medium transition-colors",
            side === "buy" ? "bg-profit/20 text-profit" : "bg-muted/50 text-muted-foreground"
          )}
        >
          <TrendingUp className="w-4 h-4" />做多
        </button>
        <button
          onClick={() => setSide("sell")}
          className={cn(
            "flex-1 flex items-center justify-center gap-1.5 py-2 rounded text-sm font-medium transition-colors",
            side === "sell" ? "bg-loss/20 text-loss" : "bg-muted/50 text-muted-foreground"
          )}
        >
          <TrendingDown className="w-4 h-4" />做空
        </button>
      </div>

      {/* 参数 */}
      <div className="grid grid-cols-2 gap-3 mb-3">
        <div>
          <Label className="text-xs">交易对</Label>
          <Input value={symbol} onChange={(e) => setSymbol(e.target.value)} className="text-sm" placeholder="BTC" />
        </div>
        <div>
          <Label className="text-xs">数量</Label>
          <Input type="number" step="0.0001" value={quantity} onChange={(e) => setQuantity(e.target.value)} className="text-sm" />
        </div>
        <div>
          <Label className="text-xs">杠杆</Label>
          <Input type="number" value={leverage} onChange={(e) => setLeverage(e.target.value)} className="text-sm" />
        </div>
        <div></div>
        <div>
          <Label className="text-xs">止盈价 (可选)</Label>
          <Input type="number" value={tpPrice} onChange={(e) => setTpPrice(e.target.value)} className="text-sm" placeholder="留空不设" />
        </div>
        <div>
          <Label className="text-xs">止损价 (可选)</Label>
          <Input type="number" value={slPrice} onChange={(e) => setSlPrice(e.target.value)} className="text-sm" placeholder="留空不设" />
        </div>
      </div>

      {/* 消息 */}
      {error && <div className="text-xs text-loss mb-2 bg-loss/10 p-2 rounded">❌ {error}</div>}
      {success && <div className="text-xs text-profit mb-2 bg-profit/10 p-2 rounded">✅ {success}</div>}

      {/* 提交 */}
      <Button
        className={cn("w-full", side === "buy" ? "bg-profit/80 hover:bg-profit" : "bg-loss/80 hover:bg-loss")}
        onClick={handleSubmit}
        disabled={submitting || !symbol || !quantity}
      >
        {submitting ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : null}
        {side === "buy" ? "买入开多" : "卖出开空"} {symbol}
      </Button>
    </Card>
  );
}
