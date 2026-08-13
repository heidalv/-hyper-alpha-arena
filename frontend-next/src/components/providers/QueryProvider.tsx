"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { AUTH_REFRESHED_EVENT } from "@/lib/api";

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 2,
            refetchOnWindowFocus: false,
            staleTime: 15_000,
          },
        },
      })
  );

  // token 续期成功后刷新缓存，避免刷页面仍是空数据
  useEffect(() => {
    const onRefreshed = () => {
      void client.invalidateQueries();
    };
    window.addEventListener(AUTH_REFRESHED_EVENT, onRefreshed);
    return () => window.removeEventListener(AUTH_REFRESHED_EVENT, onRefreshed);
  }, [client]);

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
