# Next.js Frontend Code-Quality & UX Audit — `frontend-next`

Scope: `D:\001Alpha\Hyper-Alpha-Arena\frontend-next\src` (excluded node_modules/.next/out/dist-electron). 109 `.ts/.tsx` files examined. All findings are file:line with an offending snippet.

---

## Top 15 findings (ranked by severity)

| # | Severity | file:line | issue | why it matters | suggested fix |
|---|----------|-----------|-------|----------------|---------------|
| 1 | Critical | `src/app/agent-monitor/page.tsx:214-263` | `usePoll` uses `setInterval(load, interval)` (L258) with a hand-rolled `fetch`; on HTTP 401 `load()` just `setError` (L247-251) and keeps re-polling every 5-15s forever (8 pollers: L275-285). No `refetchIntervalInBackground` machinery → poll continues in a hidden tab. | A 401 (expired/rotated token) never logs the user out and never stops; the page hammers the backend with a failing GET at 5-15s indefinitely. | Route these through `apiRequest` (single-flight refresh + logout-on-invalid) and add visibility-aware polling; stop poll loop on 401. |
| 2 | Critical | `src/app/arbitrage/page.tsx:24-49` | `setInterval(load, 30000)` (L49) fires **14 concurrent raw `fetch` calls** (L26-41) every 30s, no auth header, `Promise.allSettled` swallows every rejection into `{}` (L44). `loading` is set but never rendered as a spinner (only `disabled`/anim on refresh btns), and there is **no error state at all**. | Backend down → 14 rejected requests every 30s; page silently renders all-zero tiles with nothing telling the user it failed. | Use `apiRequest` per endpoint + a real error banner; pause polling when tab hidden; show skeletons while `loading`. |
| 3 | High | `src/app/{arbitrage:15,agent-monitor:24,exchange:23,hyperliquid:14,strategy:22,risk:12}.tsx` | `const BACKEND = getBackendUrl()...` computed once at module scope. Pages do raw `fetch("${BACKEND}/api/...")` bypassing `apiRequest` (auth/refresh/single-flight/timeout). | Changing the backend URL at runtime (login page) does not propagate — these pages keep hitting the old host until a full reload. Runtime backend is treated as a compile-time constant. | Derive the base URL at call time (or read the store); reuse `apiRequest`/`fetchPublic` for auth + timeout. |
| 4 | High | `src/app/dashboard/page.tsx` | Dashboard mounts ~11 concurrent polling sources: `usePaperBalance` 2s (L50), `usePositions` 2s (L61), `useSessions` 5s (L94), `useTierStatus` 60s (L106), `useTierActivity` 30s (L116), `EquityCurve` 60s (charts/EquityCurve.tsx:81), CooldownMatrix 10s×2 + BlockEventStream 10s (CooldownMatrixPanel.tsx:31,159), BlockReport 120s (BlockReportPanel.tsx:32). | A single idle dashboard tab keeps ~10 HTTP requests in flight at 2-60s cadence; heavy backend + network load. | Consolidate into one aggregate query; raise idle intervals; pause when tab hidden. |
| 5 | High | `src/hooks/useTradingData.ts:228-229` | `useMarketOverviewAll` = `staleTime: 1_500, refetchInterval: 2_000` — most aggressive poll in the codebase; mounted alongside `useMarketOverview` (30s, L211) and `useMarketHealth` (60s, L220) on `src/app/intel/page.tsx:120-124`. | Same market data fetched on 3 schedules (2s/30s/60s) concurrently on one page → duplicate traffic + jitter. | Pick one cadence; increase staleTime; reuse the single overview query. |
| 6 | High | `src/app/dashboard/page.tsx:82-109` | `useAccounts` failure → `accounts===undefined` → `paperAccounts=[]` → renders the **empty** card "暂无模拟交易账户" (L86-108). Error is conflated with empty. | A backend/network error shows a misleading "create an account" empty state to an already-authed user; no retry. | Branch on `isError`/`error` and show a retry banner distinct from the true empty state. |
| 7 | Medium | `src/app/risk/page.tsx:15-36` | Queries have `isLoading` but **no `isError` render path**; on failure `data` is undefined and the tables render empty (e.g. L91-209). Same pattern in `intel` (L333-338, error→"暂无数据"), `strategies/pages`, `paper-trading`. | Silent data loss to the user; nothing distinguishes "loading failed" from "nothing to show". | Add an `isError` banner per query (the app already has one in `capital-margin:83`, `factors:196`, `coin-select:329`). |
| 8 | Medium | `src/lib/api.ts:118,253,327,436,etc.` | `apiRequest<T = any>` defaults to untyped, and most endpoints return `apiRequest<any>` / `{ items: any[] }`, `{ entries: any[] }`, `{ signals: any[] }`. Total **`any` occurrences per file**: agent-monitor:56, exchange:42, settings:37, KlineChartPanel:28, arbitrage:24, api.ts:15, hyperliquid:12, LongReportsPanel:9, risk:8, SessionManager:8 (≈304 total). | No compile-time safety on API shapes; every `.map`/`.reduce` on a response is `any`, so field-rename/typo bugs surface only at runtime (often as silent `undefined`). | Type the response contracts in `src/types/api.ts` and remove the `any[]`/`T=any` defaults; derive types for the ~40 untyped endpoints. |
| 9 | Medium | Duplication | **~19 local formatter helpers** across ~18 files: `fmtTime` duplicated in CooldownMatrixPanel:19, ScheduleLogPanel:20, RAGStatusPanel:227, HealthChannelsPanel:344, DecisionChainPanel:138; `fmtPrice` in live-trading:21, MarketOverviewTable:33, TickerBar:19; plus `fmtMoney`(dashboard:35), `fmt`(capital-margin:28, factors:92, agent-monitor:1302), `fmtNum/fmtPct/fmtDt`(compute/common.tsx:320-337 — unused elsewhere), `num()`(OpsMidlongFactors:87, FactorSystemPanels:25), `fmtUsd/fmtPct`(live-trading:33-37). The money-sum `positions.reduce((s,p)=>s+(p.unrealized_pnl||0),0)` is copy-pasted in ≥8 files (dashboard:69, strategy:61, paper-trading:77, live-trading, arbitrage:253-254, hyperliquid:263, exchange:732, agent-monitor:305). | Every fix to a formatting/sum rule must be re-applied in N places → drift and inconsistency (e.g. `toFixed(2)` vs `toFixed(3)` for the same value). | Extract shared `lib/format.ts` and `lib/stats.ts`, and refactor to typed helpers. |
| 10 | Medium | Large files | 13 files >500 lines: agent-monitor/page 1257, SessionManager 1100, exchange 981, settings 840, arbitrage 743, KlineChartPanel 743, live-trading 740, coin-select 737, paper-trading 668, strategy 618, AgentRuntimeTab 601, api.ts 559, intel 546. | Monolithic mixed-intent files (page+fetch+render+sub-components) are hard to test/review and grow unbounded. | Split per-tab sub-components and per-domain hooks; extract `usePoll`/fetch layers. |
| 11 | Medium | `src/components/market/KlineChartPanel.tsx:403-580` | ~30 inline hex magic colors (`#34D399`, `#FB7185`, `#FBBF24`, `#8B5CF6`, `#22D3EE`) inside a single file, plus `applyBars`/`dedupeBars`/`paintAllSeries` all operating on `any[]` (L132-272). | Palette changes require editing dozens of literals; any-typed series make indicator edits fragile. | Move colors to a theme map; type the kline/indicator rows. |
| 12 | Medium | `src/app/dashboard/page.tsx:89,118`; `src/components/layout/TopBar.tsx:184`; `Sidebar.tsx:103` | `min-w-[1024px]` forces horizontal scroll on all viewports, `w-[360px]` fixed notification panel, `w-[216px]` fixed sidebar. Fixed `grid-cols-[2fr_1fr_1fr]` (dashboard:284) and `grid-cols-6` risk ribbon (dashboard:403) have no responsive fallback. | On narrow windows the layouts overflow / become unusable; grid columns don't collapse. | Use responsive prefixes (`md:`, `xl:`) and `min-w-0` with fluid widths. |
| 13 | Low | `src/app/compute/page.tsx:139-148` | Only ONE page implements `role="tablist"/"tab"/aria-selected`; all other tab groups (exchange:61, agent-monitor:103, settings:45, coin-select, factors:143, intel:213) are bare `<button>` groups with no tab semantics. `aria-label` appears in only ~10 files. | Screen readers get no tab/selected context across most of the app. | Add `role="tablist"`/`aria-selected`/`aria-controls`; add `aria-label` on icon-only buttons (many `<Button>` are icon-only). |
| 14 | Low | Dead code | 5 component files are **never imported anywhere** (verified by grep across `src`): `src/components/agent-monitor/AgentRuntimeTab.tsx` (601 lines), `src/components/learning/OpenCodeDisabledCard.tsx`, `src/components/trading/PriceTicker.tsx`, `src/components/ui/scroll-area.tsx`, `src/components/ui/separator.tsx`. | Dead code inflates bundle and misleads maintainers (601-line orphan). | Delete or wire up; drop unused shadcn primitives. |
| 15 | Low | `src/lib/api.ts:118-119`, `:199` | `apiRequest<T = any>` and `fetchPublic<T = any>` are the default payloads for the entire API surface; ~40 endpoints never supply a type. | Any shape change in the FastAPI backend is invisible to TS until runtime. | Require an explicit type param; create typed clients per domain. |

---

## 1. Polling & request storms

Global defaults are sound: `QueryProvider` sets `refetchOnWindowFocus: false` and `staleTime: 15_000` (src/components/providers/QueryProvider.tsx:14-15). **No file sets `refetchIntervalInBackground`** — so React Query `refetchInterval` pollers pause when the tab is hidden, but **the 13+ raw `setInterval` pollers do not**.

### React Query poll sites (`refetchInterval`)

| Query hook | file:line | interval | polled endpoint |
|---|---|---|---|
| `usePaperBalance` | src/hooks/useTradingData.ts:50 | **2s** (stale 2s) | `/paper/balance/{id}` |
| `usePositions` | src/hooks/useTradingData.ts:61 | **2s** | `/paper/positions/{id}` |
| `useOrders` | src/hooks/useTradingData.ts:71 | **5s** | `/paper/orders/{id}` |
| `usePaperSummary` | src/hooks/useTradingData.ts:81 | **5s** | `/paper/summary/{id}` |
| `useSessions` | src/hooks/useTradingData.ts:94 | **5s** (stale 2s) | `/full-auto/sessions` |
| `useTierStatus` | src/hooks/useTradingData.ts:106 | 60s | `/full-auto/tier-status/{id}` |
| `useTierActivity` | src/hooks/useTradingData.ts:116 | 30s | `/full-auto/tier-activity/{id}` |
| `useDashboard` | src/hooks/useTradingData.ts:136 | 10s | `/account/overview` |
| `useAssetCurve` | src/hooks/useTradingData.ts:145 | 120s | `/account/asset-curve/timeframe` |
| `useAiDecisions` | src/hooks/useTradingData.ts:157 | 60s | `/arena/model-chat` |
| `useScalpSignals` | src/hooks/useTradingData.ts:166 | 60s | `/atas/signals` |
| `useMarketOverview` | src/hooks/useTradingData.ts:211 | 30s | `/market-intel/overview` |
| `useMarketHealth` | src/hooks/useTradingData.ts:220 | 60s | `/market-intel/data-health` |
| `useMarketOverviewAll` | src/hooks/useTradingData.ts:229 | **2s** (stale 1.5s) | `/market/overview/all` |
| `useWatchlist` | src/hooks/useTradingData.ts:238 | 120s | `/market-intel/watchlist` |
| `src/app/live-trading/page.tsx` | :295-317 | 3s/3s/5s/60s | live balance/positions/orders |
| `src/app/risk/page.tsx` | :18-36 | 30s/30s/30s/60s | risk status/liq/pd |
| `src/app/strategy/page.tsx` | :405,:512 | 30s (stale 15s) | atas decisions / ai decisions |
| `src/components/trading/BlockReportPanel.tsx` | :32 | 120s | block report |
| `src/components/trading/CooldownMatrixPanel.tsx` | :31,:159 | 10s (stale 5s) | cooldown / block events |
| `src/components/trading/SessionManager.tsx` | :677,:726 | 10s / 15s | session info |
| `src/components/charts/EquityCurve.tsx` | :81 | 60s | `/paper/equity-curve/{id}` |

Flags:
- **Aggressive (<5s):** `usePaperBalance`/`usePositions` (2s), `useMarketOverviewAll` (2s). These are the worst offenders; 2s polling of a trading dashboard is high-volume and the data (positions/balance) rarely changes that fast.
- **`staleTime` > `refetchInterval` (self-defeating):** several hooks set `staleTime` equal to or *longer* than `refetchInterval` (e.g. `useTierStatus` stale 30s / refetch 60s is fine; but `useSessions` stale 2s / refetch 5s and `useMarketOverviewAll` stale 1.5s / refetch 2s mean there is almost never a "fresh" window). Minor, but indicates the interval is picking the cadence, not staleness.
- **Duplicated same endpoint:** `intel/page.tsx:120-124` mounts `useMarketOverview` (30s) + `useMarketOverviewAll` (2s) + `useMarketHealth` (60s) + `useWatchlist` (120s) → 4 market queries concurrently. `paper/positions` and `paper/balance` are polled from `useTradingData` hooks AND from the raw `usePoll` in agent-monitor (`/api/paper/positions`, `/api/paper/balance`).
- **Poll continues when tab hidden:** all raw pollers below.

### Raw `setInterval` pollers (do NOT pause when hidden / no React Query)

| file:line | interval | endpoint / purpose |
|---|---|---|
| `src/app/arbitrage/page.tsx:49` | 30s | **14 fetches** (arbitrage/status, positions, opportunities, capital-pool, rebate/* ×8) |
| `src/app/exchange/page.tsx:584` | 30s | statuses |
| `src/app/hyperliquid/page.tsx:60` | 30s | statuses |
| `src/app/ops/page.tsx:104` | 15s | ops refresh; plus 1s clock tick (L126) |
| `src/components/layout/TickerBar.tsx:54,88` | auto poll + price poll | market overview / prices |
| `src/components/layout/TopBar.tsx:33,56` | alert poll | alerts |
| `src/components/layout/StatusBar.tsx:17` | status tick | connectivity |
| `src/components/agent-monitor/AgentRuntimeTab.tsx:124` | dynamic interval | runtime tab data |
| `src/components/factors/FactorOverviewPanel.tsx:125` | 30s | factor overview |
| `src/components/factors/FactorSystemPanels.tsx:55,91,185,240,289` | 15s×3 / 30s / 60s | 5 panel pollers |
| `src/components/learning/{LearningLineagePanel:67,ScheduleLogPanel:59,RAGStatusPanel:44,ComputeChannelsPanel:36,WisdomLifecyclePanel:48,HealthChannelsPanel:67}` | 30s each | learning panels |
| `src/components/monitor/{DataCenterOverviewPanel:67,DataQualityPanel:181,FundingMatrixPanel:80}` | 30s/30s/60s | monitor panels |
| `src/components/ops/{OpsMidlongFactors:114,121,OpsMiningBoost:92}` | 15s | ops mining |
| `src/components/operations/{CoinFeedbackPanel:37,DecisionChainPanel:35}` | 60s | operations |
| `src/components/exchange/AsterdexPointsPanel.tsx:84` | 60s | asterdex points |
| `src/components/trading/PriceTicker.tsx:28` | setTimeout flash | price flash |
| `src/components/market/KlineChartPanel.tsx:633` | **2s** | `tick` (kline internal replay) |
| `src/hooks/useWebSocket.ts:60,67` | 3s status + 15s fallback | `/dashboard` when WS down |
| `src/app/agent-monitor/page.tsx:258` (in `usePoll`) | 5-15s | 8 endpoints |

The single worst offender set is `agent-monitor/page.tsx:275-285` — **8 concurrent `usePoll` hooks** at 5s/10s/15s each, all raw `fetch`, continuing on 401 and in hidden tabs. `src/app/ops/page.tsx:104` and the six `learning/*` panels each fire a network call every 15-30s with no visibility guard.

---

## 2. Auth flow (`src/lib/api.ts`, `src/lib/stores/auth.ts`, `src/lib/auth-storage.ts`)

This is the **best-engineered** area; described accurately in the code comments.

- **Single-flight refresh (no thundering herd):** `src/lib/api.ts:52` module-level `let refreshInFlight`, and `tryRefreshAccessToken` (L63-107) returns the existing promise if one is in flight (L64). 5 concurrent 401s → 1 refresh call.
- **401 handling in `apiRequest`:** `src/lib/api.ts:158-167` — on `resp.status===401 && !skipAuth`, call `tryRefreshAccessToken()`:
  - `"ok"` → retry the request once with the new token (L160-161).
  - `"invalid"` → `logout()` (clears tokens + stops keepalive) (L163).
  - `"network"` → `throw new ApiError(0, "后端暂不可达…")`, session preserved (L165).
  - Retry is **not** looped — if the second attempt also 401s it falls into `!resp.ok` and throws. **No infinite 401 loop.**
- **Proactive refresh:** `ensureFreshAccessToken` (L110-116) refreshes before the request when `isAccessTokenExpiringSoon(access, 90_000)` (L113); called by every non-whitelisted request (L152-154). This is the documented fix for the "idle page goes blank" bug.
- **Failed-refresh does NOT loop:** the original loop (964 req/hr) was fixed — `tryRefreshAccessToken`'s invalid branch now calls `useAuthStore.getState().logout()` (L83) which calls `stopAuthKeepalive` (auth.ts:467-472), so the 5s keepalive re-arm guard at `auth.ts:460-461` (`user && refreshToken`) becomes false and the timer stops. `armAuthKeepalive` delay is `max(5_000, expMs - now - 90s)` (auth.ts:449), so even the network-degraded path re-arms at ≥5s, not an immediate tight loop.
- **Keepalive:** `AuthGate` re-arms on visibility/focus and re-checks the token on tab focus (AuthGate.tsx:37-55), plus the proactive `ensureFreshAccessToken` — this keeps a "valid on screen" session fresh without a background storm.

### Remaining auth defects
1. **Hand-rolled fetches bypass single-flight refresh.** `agent-monitor`'s `usePoll` (page.tsx:236-243), arbitrage (L26-41), exchange/hyperliquid/strategy/risk all use bare `fetch` — none call `ensureFreshAccessToken`/`tryRefreshAccessToken`. On a 401, `usePoll` just `setError` and keeps polling (page.tsx:247-251) → the storm in finding #1. `arbitrage` sends **no Authorization header at all**.
2. **Stale module-scope base URL** (`getBackendUrl()` captured at import) — finding #3.
3. **`ensureFreshAccessToken` returns `!!getRefreshToken()` when there is no access token** (api.ts:112) — a session with a refresh token but a null/expired access token proceeds with `getAccessToken()===null`, guarantees a 401, then refreshes. Harmless but a wasted round-trip; a null access token should force a refresh immediately.
4. **`apiRequest` retries after refresh even if `skipAuth` semantics drift** — 401 already excluded for whitelisted; OK.

Conclusion: core single-flight + no-loop behavior is correct; the failure mode is that the many raw-fetch paths don't participate in it.

---

## 3. Loading / error / empty states (sample of 14 pages)

| Page (src/app) | loading | error | empty | notes |
|---|---|---|---|---|
| dashboard/page.tsx | ✅ (L82) | ❌ conflated with empty (L86-108) | ✅ | `accounts` error → "暂无模拟交易账户" |
| capital-margin/page.tsx | ✅ (L77) | ✅ (L83-85) | ✅ (`Empty`, L268) | best-practice pattern |
| live-trading/page.tsx | ✅ (L94) | ✅ `isError` (L96) | ✅ (L261,407,742) | |
| risk/page.tsx | ✅ (loadingStatus) | ❌ no `isError` render | ✅ (L205) | failures render empty tables |
| intel/page.tsx | ✅ (L333-336) | ❌ error→"暂无数据" (L337-338) | ✅ | guarded `!overview?.symbols`; no crash |
| coin-select/page.tsx | ~ | ✅ (L329-330) | ✅ (L504) | |
| factors/page.tsx | ✅ | ✅ (L196-197) | ✅ (L202) | |
| exchange/page.tsx | ✅ (L154) | ❌ only `alert()` (L839) | ✅ (L289) | error via browser alert |
| arbitrage/page.tsx | ❌ `loading` never rendered | ❌ none | ✅ (`Empty`, L745) | silent all-zero on failure |
| strategy/page.tsx | ✅ (L449,530) | ~ | ✅ (L452-454) | |
| agent-monitor/page.tsx | ✅ (per usePoll) | ✅ (per usePoll error) | ✅ (per tab) | but 401 loops (see §1) |
| paper-trading/page.tsx | ✅ | ❌ | ✅ (L305,368,451) | |
| hyperliquid/page.tsx | ✅ | ✅ (L85) | ✅ (L208-214) | |
| mid/page.tsx & long/page.tsx & scalp/page.tsx | ✅ (`isLoading||!config||!data`, L59/63/55) | ❌ | ~ | guard returns blank, no error text |

**Summary:** loading ✅ 13/14, empty ✅ 14/14, **error ❌ 5/14** (dashboard, risk, intel, exchange, arbitrage, paper-trading, mid/long/scalp). The dominant defect is **error states either absent or hidden behind the empty state**, not hard crashes — the codebase defends against null with `?? {}` / `?.` / `|| []` extensively.

**Unguarded-access (crash) candidates found are mostly guarded**, but these render invalid data rather than crashing:
- `src/components/agent-monitor/AgentRuntimeTab` (unused) and `agent-monitor/page.tsx:511` — `new Date(Math.max(...theses.map(...)))` with empty `theses` → `new Date(-Infinity)` → "Invalid Date" string rendered via `toLocaleTimeString`. (page.tsx:299 is guarded by `tierTheses.length`; :511 is **not**.)
- `src/app/agents-monitor/page.tsx:311` — `backendAlive = !positions.error && positions.data != null` treats "no data yet" as "backend down".
- `src/app/paper-trading/page.tsx:65` — `(created as any)?.id` blind cast; if `createMut` returns a different shape, accounts break.

---

## 4. Type safety

**Total `: any` / `as any` in `src`: 304** across 109 files. Top files:

| file | `any` count |
|---|---|
| src/app/agent-monitor/page.tsx | 56 |
| src/app/exchange/page.tsx | 42 |
| src/app/settings/page.tsx | 37 |
| src/components/market/KlineChartPanel.tsx | 28 |
| src/app/arbitrage/page.tsx | 24 |
| src/lib/api.ts | 15 |
| src/app/hyperliquid/page.tsx | 12 |
| src/components/long/LongReportsPanel.tsx | 9 |
| src/app/risk/page.tsx | 8 |
| src/components/trading/SessionManager.tsx | 8 |

Observations:
- **API responses are largely untyped**: `apiRequest<T = any>` (api.ts:118), `fetchPublic<T = any>` (api.ts:199). ~40 endpoints return `apiRequest<any>` or `apiRequest<{ items: any[] }>` / `{ entries: any[] }` / `{ signals: any[] }` (e.g. api.ts:425-430, 495, 546). The typed contracts live in `src/types/api.ts` but are wired to only a subset (paper/live/account/full-auto); most of `configApi`, `coinSelectApi`, `marketApi`, `signalApi`, `decisionApi` are `any`.
- `agent-monitor`, `exchange`, `settings` cast large response objects to `any` and navigate them with `(x: any).field` — no compile-time shape.
- `usePoll<T>` defaults to `any` (agent-monitor:214), so all 8 pollers' data are untyped.
- Blind `as any` casts at mutation boundaries: `paper-trading:65`, `exchange:139` (`{...} as any`), `options as any`.

---

## 5. Component size & duplication

**Files over 500 lines (13):**
`app/agent-monitor/page.tsx` 1257 · `components/trading/SessionManager.tsx` 1100 · `app/exchange/page.tsx` 981 · `app/settings/page.tsx` 840 · `app/arbitrage/page.tsx` 743 · `components/market/KlineChartPanel.tsx` 743 · `app/live-trading/page.tsx` 740 · `app/coin-select/page.tsx` 737 · `app/paper-trading/page.tsx` 668 · `app/strategy/page.tsx` 618 · `components/agent-monitor/AgentRuntimeTab.tsx` 601 (dead code) · `lib/api.ts` 559 · `app/intel/page.tsx` 546.

**Near-duplicate logic:**
- **Formatters** (see finding #9): `fmtTime` ×5 files, `fmtPrice` ×3, plus ~15 more bespoke `fmt*`/`num()` helpers. A shared `lib/format.ts` exists only as `compute/common.tsx` (`fmtNum/fmtPct/fmtTime/fmtDt`) which isn't reused.
- **Money-sum reduce** `positions.reduce((s,p)=>s+(p.unrealized_pnl||0),0)` in dashboard:69/112-114/221, strategy:61-63, paper-trading:77, live-trading:434, arbitrage:253-254/778-779, hyperliquid:263-264, exchange:732, agent-monitor:305.
- **`Object.values(...).reduce(...,0).toFixed(n)`** aggregates: arbitrage:64-65/250-256/436-437, exchange:606-620, hyperliquid:94-108.
- **Per-tab table markup** (`<table className="data-table">` + `<thead>` + `.map(row)` + 空态 `<tr><td colSpan={N}>`) repeated across dashboard, live-trading, paper-trading, exchange, settings, coin-select, arbitrage — no shared `DataTable`/`PositionTable` (arbitrage defines its own `PositionTable` at :756 used only there).
- **Repeated reduce computed 3× in a single cell:** `src/app/arbitrage/page.tsx:253-254` computes `Object.values(rebA.by_strategy).reduce(...)` three times for the same P&L number.

---

## 6. Inline styles / magic numbers / hardcoded values

- **`style={{` inline styles**: 246 matches. Mostly `width: ${pct}%` progress bars and `background: <color>`. Notable: `login/page.tsx:115-124` huge inline gradient; `KlineChartPanel.tsx:764` large chart options object; `OpsPairs/OpsMidlongFactors/OpsMiningBoost/OpsTraining` heavily use inline `style={{fontSize:11,lineHeight:1.5,...}}` instead of utility classes.
- **Hardcoded hex in `.tsx`**: `#070b12` (AuthGate:90, login:111,119), `#22c55e/#ef4444/#eab308` (dashboard:22, factors, DecisionTimeline:15-17, compute/common:261), 20+ hex literals in `KlineChartPanel.tsx:403-580`, `charts/page.tsx:89-467`, `agent-monitor/page.tsx:761-849`, `AgentRuntimeTab.tsx:139-147`. No central theme constant — palettes are scattered.
- **Hardcoded localhost/ports**: mostly legitimately config-driven through `src/lib/backend-config.ts` (defaults `http://localhost:8000`, L18/44, and the smart inference L16-40). BUT: `login/page.tsx:23` `useState("http://localhost:8000")` is a hardcoded default in component state; `app-nav.ts`, `stores/auth.ts:97` hardcode `"http://主机:8000"` hint strings. The `NEXT_PUBLIC_API_URL ?? "http://localhost:8000"` fallback is documented, not a bug.
- **Magic numbers**: `REFRESH_TIMEOUT_MS=12_000` (api.ts:21), `KEEPALIVE_SKEW_MS=90_000` (auth.ts:26), `FALLBACK_POLL_INTERVAL=15_000` (useWebSocket.ts:13), `ALERT_POLL_MS`/`PRICE_POLL_MS` (TopBar/TickerBar), cooldown `12*3600` sec hardcoded in CooldownMatrixPanel.tsx:119, `10000`/`30000`/`5000` poll literals scattered (agent-monitor:275-285).

---

## 7. Accessibility & responsiveness

- **`aria-label`**: only ~10 files (compute:139-148, dashboard:122,138, agent-monitor:176,192, login:222, PageHeader:48, TopBar:125,168,246, CommandPalette:133, MarketOverviewTable:169).
- **Keyboard**: no `<div onClick>` found without a role (grep for `<div ... onClick` returned 0). `MarketOverviewTable.tsx:169` is `<div role="button">` (OK). All major click targets are native `<button>`. So keyboard handling is largely fine — **except** the many `<button>` icon-only controls (e.g. exchange:325-327, settings:317-318, dashboard tab pills) lack `aria-label`, and tab groups lack `role="tab"`/`aria-selected` (only compute/page.tsx:139-148 has it).
- **Fixed pixel widths / overflow**: `min-w-[1024px]` (dashboard:89 and :118) forces horizontal scroll on any smaller viewport; `w-[360px]` (TopBar:184) fixed notification dropdown; `w-[216px]` sidebar; `min-w-[220px]` (coin-select:294), `min-w-[92px]` (paper-trading:631), `min-w-[14px]`/`max-w-[110px]` etc.
- **Non-responsive grid columns**: `grid-cols-[2fr_1fr_1fr]` (dashboard:284) and `grid-cols-6` risk ribbon (dashboard:403) never collapse; many `grid-cols-3`/`grid-cols-4`/`grid-cols-5` tables have no `md:`/`xl:` fallback (dashboard:216 `grid-cols-3`, live-trading/arbitrage/ops). `min-w-[1024px]` makes some of these deterministic 1024px overflow.
- **Dynamic color/contrast**: transparent/`bg-white/[0.04]`, gradient text (`grad-text`), and many `bg-profit/0`-style utilities have no fallback contrast for screen readers; live status dots use colors + `boxShadow` without an accessible label (CooldownMatrixPanel:59,113).

---

## 8. Dead code / leftovers

- **TODO / FIXME / HACK / XXX: 0** in `src`. Clean.
- **`@ts-ignore` / `@ts-expect-error`: 0**.
- **Unused components (never imported anywhere in `src`, grep-verified):**
  - `src/components/agent-monitor/AgentRuntimeTab.tsx` (601 lines — the whole file is dead; agent-monitor/page.tsx implements all tabs inline).
  - `src/components/learning/OpenCodeDisabledCard.tsx`
  - `src/components/trading/PriceTicker.tsx` (the actual ticker is `layout/TickerBar.tsx`).
  - `src/components/ui/scroll-area.tsx`
  - `src/components/ui/separator.tsx`
- **Commented-out / leftover blocks:** `src/lib/api.ts:218-219` has a duplicated "类型定义（已迁至 src/types/api.ts）" comment for a block that was moved; `SessionManager.tsx:319` has an inline explanatory comment (not a dead block). No large commented-out code regions found.
- **Suspicious leftover:** `src/components/ops/index.ts` (8-line barrel) — verify re-export targets still exist during cleanup.

---

### Quick wins (highest ROI)
1. Route all raw `fetch` (agent-monitor `usePoll`, arbitrage, exchange/hyperliquid/strategy/risk) through `apiRequest` → fixes the 401 storm (#1,#2) and the stale `BACKEND` (#3).
2. Add `isError` banners to dashboard/risk/intel/exchange/paper-trading & the mid/long/scalp guards — the copy for `capital-margin:83` / `factors:196` already exists.
3. Raise `usePaperBalance`/`usePositions` 2s → 5s+ and merge the market queries on intel.
4. Extract `lib/format.ts` + `lib/stats.ts`; collapse the ~19 formatters and the repeated `unrealized_pnl` reduce.
5. Delete the 5 unused components (601-line orphan inclusive).
