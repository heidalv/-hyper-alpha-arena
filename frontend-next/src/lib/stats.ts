/**
 * Shared aggregation helpers (pure functions, no React, no side effects).
 */

/**
 * Sum of `unrealized_pnl || 0` across positions.
 * Matches the copy-pasted `positions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0)`.
 */
export function sumUnrealizedPnl(positions: Array<{ unrealized_pnl?: number | null }>): number {
  return positions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
}

/**
 * Generic sum by a picker. `null` / `undefined` / falsy picked values count as 0.
 */
export function sumBy<T>(rows: T[], pick: (row: T) => number | null | undefined): number {
  return rows.reduce((s, row) => s + (pick(row) || 0), 0);
}
