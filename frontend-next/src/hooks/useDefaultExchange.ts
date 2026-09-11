"use client";

import { useEffect, useState } from "react";
import { configApi } from "@/lib/api";

/**
 * 全局默认交易所（后端 settings.DEFAULT_EXCHANGE）。
 * 新建账户表单的默认值跟随后端配置（当前 .env DEFAULT_EXCHANGE=binance），
 * 不再前端硬编码 "asterdex"。失败回退 "binance"（全币安定调）。
 */
export function useDefaultExchange(fallback = "binance") {
  const [ex, setEx] = useState<string>(fallback);
  useEffect(() => {
    let cancelled = false;
    configApi
      .defaultExchange()
      .then((d: any) => {
        if (!cancelled && d?.default_exchange) setEx(String(d.default_exchange));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);
  return ex;
}
