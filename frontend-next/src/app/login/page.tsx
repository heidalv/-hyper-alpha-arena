"use client";

import { FormEvent, useEffect, useState } from "react";
import { Eye, EyeOff, Loader2, Server } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { hardNavigate } from "@/lib/app-nav";
import { getBackendUrl, setBackendUrl } from "@/lib/backend-config";
import { useAuthStore } from "@/lib/stores/auth";
import { isElectronRuntime } from "@/lib/auth-storage";

type Mode = "login" | "register";

export default function LoginPage() {
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
  const [appVersion, setAppVersion] = useState("");
  const [updBusy, setUpdBusy] = useState(false);
  const [updHint, setUpdHint] = useState("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // Electron：优先用主进程持久化的后端地址（远程 Tailscale），禁止被
      // 本地静态页 origin(127.0.0.1:随机端口) 推断成 127.0.0.1:8000 覆盖。
      let preferred = "";
      if (isElectronRuntime() && window.electronAPI?.config?.getBackendUrl) {
        try {
          const r = await window.electronAPI.config.getBackendUrl();
          preferred = String(r?.url || "").trim().replace(/\/$/, "");
        } catch {
          /* ignore */
        }
      }
      const inferred = getBackendUrl();
      const isLoop = (u: string) =>
        /^(https?:\/\/)?(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/i.test(u);
      const next =
        preferred && !isLoop(preferred) ? preferred : preferred || inferred;
      if (cancelled) return;
      setApiUrl(next);
      if (next && !isLoop(next)) {
        setBackendUrl(next);
      } else if (!preferred) {
        setBackendUrl(next || inferred);
      }
      if (isElectronRuntime() && window.electronAPI?.updater) {
        void window.electronAPI.updater.getVersion().then((r) => {
          if (!cancelled && r?.version) setAppVersion(r.version);
        });
      }
    })();
    void hydrate();
    return () => {
      cancelled = true;
    };
  }, [hydrate]);

  useEffect(() => {
    // 已登录进登录页：整页进工作台，避免软跳转卡在登录壳里一直转圈
    if (hydrated && user) {
      hardNavigate("/dashboard");
    }
  }, [hydrated, user]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const base = apiUrl.trim() || "http://localhost:8000";
      setBackendUrl(base);
      try {
        await window.electronAPI?.config?.setBackendUrl?.(base);
      } catch {
        /* ignore */
      }
      if (mode === "login") {
        await login(username.trim(), password);
      } else {
        if (!email.trim()) {
          throw new Error("请填写邮箱");
        }
        await register(username.trim(), email.trim(), password);
      }
      // 登录成功后硬进仪表盘：最稳，不再依赖可能卡住的 soft nav
      hardNavigate("/dashboard");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "登录失败";
      const mapped =
        msg === "invalid credentials" || /401|unauthorized/i.test(msg)
          ? "用户名或密码错误"
          : msg;
      setError(mapped);
      setBusy(false);
    }
    // 成功硬跳转不关 busy，避免闪一下又回表单
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
            {appVersion ? ` · v${appVersion}` : ""}
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
            <div className="space-y-1.5">
              <Input
                value={apiUrl}
                onChange={(e) => setApiUrl(e.target.value)}
                placeholder="http://100.x.x.x:8000"
                className="h-9 font-mono text-xs bg-black/25"
              />
              <p className="text-[10px] leading-relaxed text-slate-600">
                远程电脑请填这台服务器的 Tailscale/局域网地址，例如{" "}
                <span className="font-mono text-slate-500">http://100.x.x.x:8000</span>
                。填 127.0.0.1 只能连本机，远程更新会失败。
                {appVersion ? ` 当前桌面端 v${appVersion}` : ""}
              </p>
            </div>
          )}

          {error && (
            <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              {error}
            </p>
          )}

          <Button
            type="submit"
            disabled={busy || !username || !password}
            className="btn-glow h-10 w-full"
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

        {isElectronRuntime() ? (
          <div className="mt-6 flex flex-col items-center gap-2">
            <button
              type="button"
              disabled={updBusy}
              onClick={() => {
                void (async () => {
                  if (!window.electronAPI?.updater) return;
                  setUpdBusy(true);
                  setUpdHint("检查中…");
                  try {
                    const base = apiUrl.trim() || "http://localhost:8000";
                    if (/^(https?:\/\/)?(localhost|127\.0\.0\.1|\[::1\])/i.test(base)) {
                      setUpdHint("请先点「后端地址」，填远程机器的 http://尾鳞IP:8000，不要用 127.0.0.1");
                      return;
                    }
                    setBackendUrl(base);
                    await window.electronAPI.config?.setBackendUrl?.(base);
                    // 先探活更新源，便于看清是网络问题还是已是最新
                    try {
                      const vr = await fetch(`${base}/api/desktop/version`, { cache: "no-store" });
                      const meta = await vr.json();
                      if (!vr.ok || !meta?.available) {
                        setUpdHint(`更新源不可用：${base}/arena-updates/（服务端可能没发布 exe）`);
                        return;
                      }
                      if (meta.version && appVersion && meta.version === appVersion) {
                        setUpdHint(
                          `远程与本机同为 v${meta.version}——本地尚未发布更高版本（需重新打包发布后再点更新）`,
                        );
                        return;
                      }
                      setUpdHint(`远程最新 v${meta.version} · 本机 v${appVersion || "?"} · 开始下载检查…`);
                    } catch {
                      setUpdHint(`连不上更新源 ${base}，请确认远程后端已开且 Tailscale 通`);
                      return;
                    }
                    const r = await window.electronAPI.updater.check();
                    if (!r.ok && r.reason === "dev") setUpdHint("开发模式不检查更新");
                    else if (!r.ok) setUpdHint(r.error || "检查失败");
                    else setUpdHint("已发起检查（有新版本会顶部提示下载）");
                  } catch (e) {
                    setUpdHint(e instanceof Error ? e.message : "检查失败");
                  } finally {
                    setUpdBusy(false);
                  }
                })();
              }}
              className="text-[11px] text-slate-500 underline-offset-2 hover:text-emerald-400 hover:underline disabled:opacity-50"
            >
              {updBusy ? "正在检查更新…" : "检查桌面端更新"}
            </button>
            {updHint ? <p className="text-[11px] text-slate-600">{updHint}</p> : null}
          </div>
        ) : null}

        <p className="mt-8 text-center text-[11px] text-slate-600">
          Token 由{isElectronRuntime() ? "系统安全存储加密保存" : "本地会话保存"} · 不依赖浏览器 Cookie
        </p>
      </div>
    </div>
  );
}
