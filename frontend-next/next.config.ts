import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // [阶段5] 静态 HTML 导出 —— Electron 生产模式 main.js 直接 loadFile('out/index.html')。
  // 选 output:"export" 的依据:本仓库 src/app 全部是客户端渲染(CSR)页面,
  //   - 无 Route Handlers (src/app/**/route.ts)
  //   - 无 middleware.ts
  //   - 无 server actions / next/headers / next/cookies / Image 优化等 SSR-only 特性
  //   - 无动态路由 ([slug]) 或 generateStaticParams
  // 因此静态导出不会丢任何现有功能,且是 Electron+Next 的标准路径。
  // 开发 (next dev) 不受此选项影响(仍走 dev server + HMR)。
  output: "export",
  // Electron 生产模式由 main.js 起本地 http 提供 out/，并映射 /path → path.html，
  // 因此保持 trailingSlash:false（与 Next 默认导出 login.html 一致）即可。
  trailingSlash: false,
  images: {
    // 静态导出不支持 next/image 默认的优化 server;改 unoptimized 走纯客户端 <img>
    unoptimized: true,
  },
  // 关闭 Next.js 15 开发模式悬浮 DevTools 面板
  devIndicators: false,
  // [2026-07-16 修复] Turbopack dev 持久化缓存(.next/dev/cache/turbopack)跨会话
  // 无限增长、从不清理——这是 Next.js 16.x 官方已确认的活跃缺陷
  // (github.com/vercel/next.js issue #81161 / #94915)。实测本项目开发服务器连续
  // 运行 14 小时后，该缓存涨到 2.94GB/124522 个文件，进程句柄数暴涨到 62000+，
  // 表现为"用得越久、切页面越卡、数据加载越慢"。关闭该实验性缓存后每次冷启动会
  // 慢几秒，但不会再随运行时长持续劣化。
  experimental: {
    turbopackFileSystemCacheForDev: false,
  },
  // [2026-07-18 修复] Next.js 16 默认阻止跨域访问 dev 专用资源(HMR websocket等)，
  // 只允许 localhost。本项目一直通过 127.0.0.1:5273 访问（脚本/文档/习惯都是这个
  // 地址），导致每次 HMR websocket 连接都被服务端直接拒绝("Blocked cross-origin
  // request... from 127.0.0.1")，浏览器随即不断重连不断被拒，长时间攒下大量半关闭
  // TCP 连接——这正是"越用越卡、切页面加载不出数据"的根因之一（另一半是下面已经关闭的
  // turbopackFileSystemCacheForDev 缓存无限增长）。显式加白名单即可正常握手。
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // [2026-07-30 修复] API 代理：前端 /api/* → 后端 localhost:8000/api/*
  // output:"export" 模式下 rewrites 不生效（只有 dev 模式生效），但 dev 模式
  // 下需要这个才能让浏览器 fetch /api/xxx 不返回 404。
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
