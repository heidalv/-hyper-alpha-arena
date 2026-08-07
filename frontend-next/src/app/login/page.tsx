"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Eye, EyeOff, Loader2, Server } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getBackendUrl, setBackendUrl } from "@/lib/backend-config";
import { useAuthStore } from "@/lib/stores/auth";
import { isElectronRuntime } from "@/lib/auth-storage";

type Mode = "login" | "register";

export default function LoginPage() {
  const router = useRouter();
  const { user, hydrated, hydrate, login, register } = useAuthStore();

  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [apiUrl, setApiUrl] = useState("http://localhost:8000");
  const [showServer, setShowServer] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setApiUrl(getBackendUrl());
    void hydrate();
  }, [hydrate]);

  useEffect(() => {
    if (hydrated && user) {
      router.replace("/dashboard");
    }
  }, [hydrated, user, router]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      setBackendUrl(apiUrl.trim() || "http://localhost:8000");
      if (mode === "login") {
        await login(username.trim(), password);
      } else {
        if (!email.trim()) {
          throw new Error("请填写邮箱");
        }
        await register(username.trim(), email.trim(), password);
      }
      router.replace("/dashboard");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "登录失败";
      setError(msg === "invalid credentials" ? "用户名或密码错误" : msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#070b12] text-foreground">
      {/* 氛围层：深空网格 + 青绿能量光，避免紫/奶油模板感 */}
      <div
        className="pointer-events-none absolute inset-0 opacity-80"
        style={{
          background:
            "radial-gradient(ellipse 80% 50% at 20% 10%, rgba(16,185,129,0.14), transparent 55%)," +
            "radial-gradient(ellipse 60% 40% at 90% 80%, rgba(14,165,233,0.10), transparent 50%)," +
            "linear-gradient(180deg, #070b12 0%, #0a1018 100%)",
        }}
      />
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.07]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(148,163,184,0.35) 1px, transparent 1px)," +
            "linear-gradient(90deg, rgba(148,163,184,0.35) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
          maskImage: "radial-gradient(ellipse at center, black 30%, transparent 75%)",
        }}
      />

      <div className="relative z-10 flex min-h-screen flex-col items-center justify-center px-4 py-10">
        <div className="mb-10 text-center">
          <p className="font-mono text-[11px] tracking-[0.28em] text-emerald-400/80 uppercase">
            Heidalv Alpha Arena
          </p>
          <h1 className="mt-3 text-4xl font-semibold tracking-tight text-slate-50 sm:text-5xl">
            交易终端
          </h1>
          <p className="mt-3 text-sm text-slate-400">
            {isElectronRuntime() ? "桌面端安全登录" : "登录后进入量化工作台"}
          </p>
        </div>

        <form
          onSubmit={onSubmit}
          className="w-full max-w-[400px] space-y-4 rounded-xl border border-white/8 bg-[#0d141e]/90 p-6 shadow-[0_20px_60px_rgba(0,0,0,0.45)] backdrop-blur-md"
        >
          <div className="flex gap-1 rounded-lg bg-black/30 p-1">
            <button
              type="button"
              className={`flex-1 rounded-md py-1.5 text-sm transition ${
                mode === "login"
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "text-slate-400 hover:text-slate-200"
              }`}
              onClick={() => setMode("login")}
            >
              登录
            </button>
            <button
              type="button"
              className={`flex-1 rounded-md py-1.5 text-sm transition ${
                mode === "register"
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "text-slate-400 hover:text-slate-200"
              }`}
              onClick={() => setMode("register")}
            >
              注册
            </button>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="username">用户名{mode === "login" ? " / 邮箱" : ""}</Label>
            <Input
              id="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder={mode === "login" ? "username 或 email" : "username"}
              required
              className="h-10 bg-black/25"
            />
          </div>

          {mode === "register" && (
            <div className="space-y-1.5">
              <Label htmlFor="email">邮箱</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
                className="h-10 bg-black/25"
              />
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="password">密码</Label>
            <div className="relative">
              <Input
                id="password"
                type={showPwd ? "text" : "password"}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
                className="h-10 bg-black/25 pr-10"
              />
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                onClick={() => setShowPwd((v) => !v)}
                tabIndex={-1}
              >
                {showPwd ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>

          <button
            type="button"
            className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300"
            onClick={() => setShowServer((v) => !v)}
          >
            <Server className="h-3.5 w-3.5" />
            后端地址
          </button>
          {showServer && (
            <Input
              value={apiUrl}
              onChange={(e) => setApiUrl(e.target.value)}
              placeholder="http://localhost:8000"
              className="h-9 font-mono text-xs bg-black/25"
            />
          )}

          {error && (
            <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}

          <Button
            type="submit"
            disabled={busy || !username || !password}
            className="h-10 w-full bg-emerald-600 text-white hover:bg-emerald-500"
          >
            {busy ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                请稍候…
              </>
            ) : mode === "login" ? (
              "进入终端"
            ) : (
              "创建账户"
            )}
          </Button>
        </form>

        <p className="mt-8 text-center text-[11px] text-slate-600">
          Token 由{isElectronRuntime() ? "系统安全存储加密保存" : "本地会话保存"} · 不依赖浏览器 Cookie
        </p>
      </div>
    </div>
  );
}
