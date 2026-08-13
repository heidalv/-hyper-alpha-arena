/**
 * 应用内导航
 *
 * - 默认走 Next 软跳转（不整页刷新，登录态/内存状态保留）
 * - 若软跳转卡住（常见于 next dev 的 RSC flight），超时后硬跳转兜底
 * - Electron / 静态 out 下软跳转本身可用，不应每次 location.assign
 */

const HARD_FALLBACK_MS = 1500;

export function normalizeHref(href: string): string {
  if (!href) return "/";
  return href.startsWith("/") ? href : `/${href}`;
}

export function currentPathWithSearch(): string {
  if (typeof window === "undefined") return "";
  return `${window.location.pathname}${window.location.search}`;
}

export function pathOf(href: string): string {
  const t = normalizeHref(href);
  const hash = t.indexOf("#");
  const noHash = hash >= 0 ? t.slice(0, hash) : t;
  const q = noHash.indexOf("?");
  return q >= 0 ? noHash.slice(0, q) : noHash;
}

export function shouldHandleNavClick(event: {
  defaultPrevented?: boolean;
  button?: number;
  metaKey?: boolean;
  ctrlKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
}): boolean {
  if (event.defaultPrevented) return false;
  if ((event.button ?? 0) !== 0) return false;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
  return true;
}

/** 硬跳转（登录/登出等必须整页场景）。已在目标 path 则强制刷新，避免软路由半失败卡死。 */
export function hardNavigate(href: string): void {
  if (typeof window === "undefined") return;
  const target = normalizeHref(href);
  const targetPath = pathOf(target);
  try {
    if (window.location.pathname === targetPath) {
      window.location.reload();
      return;
    }
    window.location.replace(target);
  } catch {
    window.location.href = target;
  }
}

/**
 * 软跳转 + 超时硬兜底。传入 next/navigation 的 router.push。
 */
export function softNavigate(
  href: string,
  push: (url: string) => void,
  opts?: { fallbackMs?: number },
): void {
  if (typeof window === "undefined") return;
  const target = normalizeHref(href);
  const beforePath = window.location.pathname;
  const targetPath = pathOf(target);
  if (currentPathWithSearch() === target) return;

  try {
    push(target);
  } catch {
    hardNavigate(target);
    return;
  }

  const ms = opts?.fallbackMs ?? HARD_FALLBACK_MS;
  window.setTimeout(() => {
    // 仍停在原 path → 软跳转失败/卡住
    if (window.location.pathname === beforePath && beforePath !== targetPath) {
      hardNavigate(target);
    }
  }, ms);
}

/** @deprecated 使用 softNavigate；保留别名避免旧引用炸掉 */
export function navigateApp(href: string): void {
  hardNavigate(href);
}

export function shouldHardNavigate(event: {
  defaultPrevented?: boolean;
  button?: number;
  metaKey?: boolean;
  ctrlKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
}): boolean {
  return shouldHandleNavClick(event);
}
