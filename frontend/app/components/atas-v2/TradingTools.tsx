/**
 * ATAS V2 交易工具
 * 提供风险检查、仓位计算等实用功能
 */
import { useState }from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Badge } from '../ui/badge';
import { 
  Calculator, Shield, TrendingUp, AlertCircle, 
  CheckCircle, Info 
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";

const API_BASE = '/api/atas/v2';

interface TradingToolsProps {
  accountId: number;
}

export default function TradingTools({ accountId }: TradingToolsProps) {
  // 风险检查状态
  const [riskCheckLoading, setRiskCheckLoading] = useState(false);
  const [riskCheckResult, setRiskCheckResult] = useState<any>(null);
  const [riskCheckSymbol, setRiskCheckSymbol] = useState('BTC');
  const [riskCheckSide, setRiskCheckSide] = useState('buy');
  const [riskCheckQuantity, setRiskCheckQuantity] = useState('0.1');
  const [riskCheckPrice, setRiskCheckPrice] = useState('50000');

  // 仓位计算状态
  const [positionCalcLoading, setPositionCalcLoading] = useState(false);
  const [positionCalcResult, setPositionCalcResult] = useState<any>(null);
  const [positionCalcSymbol, setPositionCalcSymbol] = useState('BTC');
  const [positionCalcPrice, setPositionCalcPrice] = useState('50000');
  const [positionCalcMethod, setPositionCalcMethod] = useState('fixed_ratio');
  const [positionCalcRatio, setPositionCalcRatio] = useState('0.1');
  const [positionCalcStopLoss, setPositionCalcStopLoss] = useState('');

  // 执行风险检查
  const handleRiskCheck = async () => {
    setRiskCheckLoading(true);
    setRiskCheckResult(null);

    try {
      // 修复：POST 请求参数仍通过 query string 传递（匹配 FastAPI 路由签名）
      // FastAPI 的 check_trade_risk 函数参数定义为 Query 参数，非 Body
      const params = new URLSearchParams({
        symbol: riskCheckSymbol,
        side: riskCheckSide,
        quantity: riskCheckQuantity,
        price: riskCheckPrice
      });

      const response = await fetch(
        `${API_BASE}/account/${accountId}/check-trade?${params}`,
        { 
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        }
      );

      if (response.ok) {
        const data = await response.json();
        setRiskCheckResult(data);
        
        if (data.passed) {
          toast.success('风险检查通过');
        } else {
          toast.error(`风险检查未通过: ${data.violations?.length || 0} 个违规项`);
        }
      } else {
        const errorData = await response.json().catch(() => null);
        toast.error(errorData?.detail || '风险检查失败');
      }
    } catch (error) {
      console.error('风险检查错误:', error);
      toast.error('风险检查失败，请检查网络连接');
    } finally {
      setRiskCheckLoading(false);
    }
  };

  // 执行仓位计算
  const handlePositionCalc = async () => {
    setPositionCalcLoading(true);
    setPositionCalcResult(null);

    try {
      const params = new URLSearchParams({
        symbol: positionCalcSymbol,
        entry_price: positionCalcPrice,
        method: positionCalcMethod,
        ratio: positionCalcRatio
      });

      if (positionCalcStopLoss) {
        params.append('stop_loss_price', positionCalcStopLoss);
      }

      const response = await fetch(
        `${API_BASE}/account/${accountId}/calculate-position?${params}`,
        { 
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        }
      );

      if (response.ok) {
        const data = await response.json();
        setPositionCalcResult(data);
        if (data.error) {
          toast.error(data.error);
        } else {
          toast.success('仓位计算完成');
        }
      } else {
        const errorData = await response.json().catch(() => null);
        toast.error(errorData?.detail || '仓位计算失败');
      }
    } catch (error) {
      console.error('仓位计算错误:', error);
      toast.error('仓位计算失败，请检查网络连接');
    } finally {
      setPositionCalcLoading(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* 风险检查工具 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-blue-600" />
            交易风险检查
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-3">
            <div>
              <Label>交易品种</Label>
              <Input
                value={riskCheckSymbol}
                onChange={(e) => setRiskCheckSymbol(e.target.value)}
                placeholder="BTC"
              />
            </div>

            <div>
              <Label>交易方向</Label>
              <Select value={riskCheckSide} onValueChange={setRiskCheckSide}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="buy">买入 (Buy)</SelectItem>
                  <SelectItem value="sell">卖出 (Sell)</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div>
              <Label>数量</Label>
              <Input
                type="number"
                step="0.01"
                value={riskCheckQuantity}
                onChange={(e) => setRiskCheckQuantity(e.target.value)}
                placeholder="0.1"
              />
            </div>

            <div>
              <Label>价格</Label>
              <Input
                type="number"
                step="0.01"
                value={riskCheckPrice}
                onChange={(e) => setRiskCheckPrice(e.target.value)}
                placeholder="50000"
              />
            </div>
          </div>

          <Button 
            onClick={handleRiskCheck} 
            disabled={riskCheckLoading}
            className="w-full"
          >
            {riskCheckLoading ? '检查中...' : '执行风险检查'}
          </Button>

          {/* 风险检查结果 */}
          {riskCheckResult && (
            <div className={`p-4 rounded-lg border-2 ${
              riskCheckResult.passed 
                ? 'border-green-600 bg-green-50 dark:bg-green-900/20' 
                : 'border-red-600 bg-red-50 dark:bg-red-900/20'
            }`}>
              <div className="flex items-center gap-2 mb-3">
                {riskCheckResult.passed ? (
                  <CheckCircle className="h-5 w-5 text-green-600" />
                ) : (
                  <AlertCircle className="h-5 w-5 text-red-600" />
                )}
                <span className="font-semibold">
                  {riskCheckResult.passed ? '风险检查通过' : '风险检查未通过'}
                </span>
              </div>

              <div className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">风险等级:</span>
                  <Badge variant={riskCheckResult.risk_level === 'low' ? 'default' : 'destructive'}>
                    {riskCheckResult.risk_level}
                  </Badge>
                </div>

                {riskCheckResult.violations && riskCheckResult.violations.length > 0 && (
                  <div>
                    <p className="font-semibold text-red-600 mb-1">违规项:</p>
                    <ul className="space-y-1">
                      {riskCheckResult.violations.map((v: string, i: number) => (
                        <li key={i} className="text-red-700 dark:text-red-400">• {v}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {riskCheckResult.warnings && riskCheckResult.warnings.length > 0 && (
                  <div>
                    <p className="font-semibold text-yellow-600 mb-1">警告:</p>
                    <ul className="space-y-1">
                      {riskCheckResult.warnings.map((w: string, i: number) => (
                        <li key={i} className="text-yellow-700 dark:text-yellow-400">• {w}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {riskCheckResult.metrics && (
                  <div className="mt-3 pt-3 border-t">
                    <p className="font-semibold mb-2">风险指标:</p>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                      {Object.entries(riskCheckResult.metrics).map(([key, value]: [string, any]) => (
                        <div key={key}>
                          <span className="text-muted-foreground">{key}:</span>
                          <span className="ml-1 font-medium">{typeof value === 'number' ? value.toFixed(2) : value}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 仓位计算工具 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Calculator className="h-5 w-5 text-green-600" />
            最优仓位计算
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-3">
            <div>
              <Label>交易品种</Label>
              <Input
                value={positionCalcSymbol}
                onChange={(e) => setPositionCalcSymbol(e.target.value)}
                placeholder="BTC"
              />
            </div>

            <div>
              <Label>入场价格</Label>
              <Input
                type="number"
                step="0.01"
                value={positionCalcPrice}
                onChange={(e) => setPositionCalcPrice(e.target.value)}
                placeholder="50000"
              />
            </div>

            <div>
              <Label>计算方法</Label>
              <Select value={positionCalcMethod} onValueChange={setPositionCalcMethod}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="fixed_ratio">固定比例</SelectItem>
                  <SelectItem value="fixed_amount">固定金额</SelectItem>
                  <SelectItem value="kelly">Kelly公式</SelectItem>
                  <SelectItem value="atr_based">ATR基础</SelectItem>
                  <SelectItem value="volatility_adjusted">波动率调整</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div>
              <Label>资金比例 (0-1)</Label>
              <Input
                type="number"
                step="0.01"
                value={positionCalcRatio}
                onChange={(e) => setPositionCalcRatio(e.target.value)}
                placeholder="0.1"
              />
            </div>

            <div>
              <Label>止损价格 (可选)</Label>
              <Input
                type="number"
                step="0.01"
                value={positionCalcStopLoss}
                onChange={(e) => setPositionCalcStopLoss(e.target.value)}
                placeholder="45000"
              />
            </div>
          </div>

          <Button 
            onClick={handlePositionCalc} 
            disabled={positionCalcLoading}
            className="w-full"
          >
            {positionCalcLoading ? '计算中...' : '计算最优仓位'}
          </Button>

          {/* 仓位计算结果 */}
          {positionCalcResult && (
            <div className="p-4 rounded-lg border-2 border-green-600 bg-green-50 dark:bg-green-900/20">
              <div className="flex items-center gap-2 mb-3">
                <TrendingUp className="h-5 w-5 text-green-600" />
                <span className="font-semibold">计算结果</span>
              </div>

              <div className="space-y-2">
                <div className="flex justify-between items-center">
                  <span className="text-sm text-muted-foreground">建议数量:</span>
                  <span className="text-lg font-bold text-green-600">
                    {positionCalcResult.quantity?.toFixed(6) || '0'} {positionCalcSymbol}
                  </span>
                </div>

                <div className="flex justify-between items-center">
                  <span className="text-sm text-muted-foreground">持仓价值:</span>
                  <span className="text-lg font-bold">
                    ${positionCalcResult.value?.toFixed(2) || '0.00'}
                  </span>
                </div>

                <div className="flex justify-between items-center">
                  <span className="text-sm text-muted-foreground">风险金额:</span>
                  <span className="text-sm font-semibold text-red-600">
                    ${positionCalcResult.risk_amount?.toFixed(2) || '0.00'}
                  </span>
                </div>

                {positionCalcResult.stop_loss_price && (
                  <div className="flex justify-between items-center">
                    <span className="text-sm text-muted-foreground">止损价格:</span>
                    <span className="text-sm font-semibold">
                      ${positionCalcResult.stop_loss_price?.toFixed(2)}
                    </span>
                  </div>
                )}

                <div className="mt-3 pt-3 border-t">
                  <div className="flex items-start gap-2">
                    <Info className="h-4 w-4 text-blue-600 flex-shrink-0 mt-0.5" />
                    <p className="text-xs text-muted-foreground">
                      计算方法: {positionCalcResult.method}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
