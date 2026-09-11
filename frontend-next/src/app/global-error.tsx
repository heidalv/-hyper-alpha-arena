"use client";

/**
 * 全局错误边界（Next.js 16）
 *
 * 触发场景：root layout / template 自身抛错（普通 error.tsx 覆盖不到）。
 * 该文件会**替换**整个 root layout，因此必须自带 <html>/<body>，
 * 且不能导出 metadata（Error Boundary 必须是客户端组件）。
 * 这里内联最少的样式，不依赖 Tailwind 变量，保证壳挂了也能看。
 */
import { useEffect } from "react";

export default function GlobalError({
  error,
  unstable_retry,
}: {
  error: Error & { digest?: string };
  unstable_retry: () => void;
}) {
  useEffect(() => {
    console.error("[arena] 应用级异常:", error);
  }, [error]);

  return (
    <html lang="zh-CN">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#070B15",
          color: "#EAF0FA",
          fontFamily:
            'system-ui, "Segoe UI", "Microsoft YaHei", Roboto, Helvetica, Arial, sans-serif',
        }}
      >
        <div
          style={{
            maxWidth: 520,
            padding: 28,
            border: "1px solid rgba(255,255,255,0.09)",
            borderRadius: 14,
            background: "rgba(255,255,255,0.045)",
          }}
        >
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>应用启动异常</h1>
          <p style={{ margin: "8px 0 0", fontSize: 13, lineHeight: 1.7, color: "#94A1BC" }}>
            前端外壳未能完成初始化。后端进程不受影响，交易与任务仍在运行。
            请重试，或刷新页面；若反复出现请检查浏览器控制台。
          </p>
          <pre
            style={{
              margin: "16px 0 0",
              padding: "10px 12px",
              maxHeight: 160,
              overflow: "auto",
              borderRadius: 8,
              background: "rgba(0,0,0,0.35)",
              color: "#FB7185",
              fontSize: 11,
              lineHeight: 1.6,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {error.message || String(error)}
          </pre>
          <button
            type="button"
            onClick={() => unstable_retry()}
            style={{
              marginTop: 16,
              height: 34,
              padding: "0 16px",
              border: 0,
              borderRadius: 8,
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 500,
              color: "#041018",
              background: "linear-gradient(135deg, #22D3EE 0%, #8B5CF6 100%)",
            }}
          >
            重试
          </button>
        </div>
      </body>
    </html>
  );
}
