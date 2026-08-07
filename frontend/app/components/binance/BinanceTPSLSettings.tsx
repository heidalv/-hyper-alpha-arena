/**
 * Binance Take Profit / Stop Loss Settings Component
 * Allows users to set automated TP/SL for positions
 */

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { BinancePosition } from '@/lib/types/binance';
import { formatSize } from '@/lib/priceFormat';

interface BinanceTPSLSettingsProps {
  accountId: number;
  position: BinancePosition;
  enabled: boolean;
  onUpdate?: () => void;
}

export default function BinanceTPSLSettings({ 
  accountId, 
  position, 
  enabled,
  onUpdate 
}: BinanceTPSLSettingsProps) {
  const [tpType, setTpType] = useState<'percentage' | 'price'>('percentage');
  const [tpValue, setTpValue] = useState('');
  const [slType, setSlType] = useState<'percentage' | 'price'>('percentage');
  const [slValue, setSlValue] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const calculateTPPrice = () => {
    if (!tpValue) return null;
    
    if (tpType === 'price') {
      return parseFloat(tpValue);
    }
    
    // Calculate based on percentage
    const percentage = parseFloat(tpValue) / 100;
    if (position.side === 'long') {
      return position.entry_price * (1 + percentage);
    } else {
      return position.entry_price * (1 - percentage);
    }
  };

  const calculateSLPrice = () => {
    if (!slValue) return null;
    
    if (slType === 'price') {
      return parseFloat(slValue);
    }
    
    // Calculate based on percentage
    const percentage = parseFloat(slValue) / 100;
    if (position.side === 'long') {
      return position.entry_price * (1 - percentage);
    } else {
      return position.entry_price * (1 + percentage);
    }
  };

  const handleSetTPSL = async () => {
    const tpPrice = calculateTPPrice();
    const slPrice = calculateSLPrice();

    if (!tpPrice && !slPrice) {
      setMessage({ type: 'error', text: 'Please set at least TP or SL' });
      return;
    }

    try {
      setSubmitting(true);
      setMessage(null);

      const response = await fetch(`/api/binance/accounts/${accountId}/tpsl`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: position.symbol,
          side: position.side === 'long' ? 'sell' : 'buy', // Opposite side to close
          amount: position.size,
          take_profit_price: tpPrice,
          stop_loss_price: slPrice
        })
      });

      const data = await response.json();

      if (response.ok && data.status === 'success') {
        setMessage({ 
          type: 'success', 
          text: `TP/SL set successfully!${tpPrice ? ` TP: ${tpPrice.toFixed(2)}` : ''}${slPrice ? ` SL: ${slPrice.toFixed(2)}` : ''}` 
        });
        
        // Reset form
        setTpValue('');
        setSlValue('');
        
        // Notify parent
        if (onUpdate) {
          onUpdate();
        }
      } else {
        setMessage({ 
          type: 'error', 
          text: data.error || 'Failed to set TP/SL' 
        });
      }
    } catch (err: any) {
      setMessage({ 
        type: 'error', 
        text: err.message || 'Failed to set TP/SL' 
      });
    } finally {
      setSubmitting(false);
    }
  };

  if (!enabled) {
    return null;
  }

  const tpPrice = calculateTPPrice();
  const slPrice = calculateSLPrice();

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle className="text-sm">Set TP/SL for {position.symbol}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {/* Messages */}
          {message && (
            <div className={`p-3 rounded-md text-sm ${
              message.type === 'error' 
                ? 'bg-red-50 text-red-900' 
                : 'bg-green-50 text-green-900'
            }`}>
              {message.text}
            </div>
          )}

          {/* Position Info */}
          <div className="p-3 bg-muted/50 rounded-md text-sm space-y-1">
            <div className="flex justify-between">
              <span>Position Side:</span>
              <span className={`font-semibold ${position.side === 'long' ? 'text-green-600' : 'text-red-600'}`}>
                {position.side.toUpperCase()}
              </span>
            </div>
            <div className="flex justify-between">
              <span>Entry Price:</span>
              <span className="font-semibold">${position.entry_price.toFixed(2)}</span>
            </div>
            <div className="flex justify-between">
              <span>Position Size:</span>
              <span className="font-semibold">{formatSize(position.size, position.symbol)}</span>
            </div>
          </div>

          {/* Take Profit */}
          <div className="space-y-2">
            <Label>Take Profit</Label>
            <div className="grid grid-cols-3 gap-2">
              <Select value={tpType} onValueChange={(v: any) => setTpType(v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="percentage">Percentage (%)</SelectItem>
                  <SelectItem value="price">Price ($)</SelectItem>
                </SelectContent>
              </Select>
              
              <Input
                type="number"
                step="0.01"
                placeholder={tpType === 'percentage' ? '10' : '50000'}
                value={tpValue}
                onChange={(e) => setTpValue(e.target.value)}
                className="col-span-2"
              />
            </div>
            {tpPrice && (
              <p className="text-xs text-muted-foreground">
                TP will trigger at ${tpPrice.toFixed(2)} 
                {position.side === 'long' ? ' (above entry)' : ' (below entry)'}
              </p>
            )}
          </div>

          {/* Stop Loss */}
          <div className="space-y-2">
            <Label>Stop Loss</Label>
            <div className="grid grid-cols-3 gap-2">
              <Select value={slType} onValueChange={(v: any) => setSlType(v)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="percentage">Percentage (%)</SelectItem>
                  <SelectItem value="price">Price ($)</SelectItem>
                </SelectContent>
              </Select>
              
              <Input
                type="number"
                step="0.01"
                placeholder={slType === 'percentage' ? '5' : '40000'}
                value={slValue}
                onChange={(e) => setSlValue(e.target.value)}
                className="col-span-2"
              />
            </div>
            {slPrice && (
              <p className="text-xs text-muted-foreground">
                SL will trigger at ${slPrice.toFixed(2)}
                {position.side === 'long' ? ' (below entry)' : ' (above entry)'}
              </p>
            )}
          </div>

          {/* Submit Button */}
          <Button
            onClick={handleSetTPSL}
            className="w-full"
            disabled={submitting || (!tpValue && !slValue)}
          >
            {submitting ? 'Setting...' : 'Set TP/SL Orders'}
          </Button>

          <p className="text-xs text-muted-foreground">
            * TP/SL orders are limit orders that will automatically execute when price reaches the specified level.
            {position.side === 'long' 
              ? ' For long positions: TP > entry, SL < entry' 
              : ' For short positions: TP < entry, SL > entry'}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
