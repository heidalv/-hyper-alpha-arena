/**
 * 最小 Dataview DQL 引擎
 * ----------------------
 * 支持子集：TABLE 列[ AS 别名] / FROM "文件夹" / WHERE 表达式(AND/OR + 比较) / SORT 字段 [ASC|DESC] / LIMIT n
 * 对 /api/vault/index 返回的笔记 frontmatter + file.* 求值。够跑通 Agent进化中心 MOC 的十几张表。
 */

import type { VaultNote } from '../../lib/vaultApi'

export interface DqlColumn {
  expr: string
  header: string
}

export interface DqlQuery {
  columns: DqlColumn[]
  from: string[]
  where?: string
  sort: Array<{ field: string; dir: 'ASC' | 'DESC' }>
  limit?: number
  valid: boolean
  error?: string
}

export interface DqlRow {
  note: VaultNote
  cells: any[]
}

/** 把一篇笔记摊平成可取值对象：frontmatter + file.* */
function toRecord(note: VaultNote): Record<string, any> {
  return {
    ...note.frontmatter,
    file: {
      name: note.name,
      folder: note.folder,
      path: note.path,
      link: note.name,
      mtime: note.mtime,
    },
  }
}

function getField(rec: Record<string, any>, path: string): any {
  const parts = path.split('.')
  let cur: any = rec
  for (const p of parts) {
    if (cur == null) return undefined
    cur = cur[p]
  }
  return cur
}

function parseLiteral(raw: string): any {
  const v = raw.trim()
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
    return v.slice(1, -1)
  }
  if (v === 'true') return true
  if (v === 'false') return false
  if (v === 'null') return null
  if (/^-?\d+$/.test(v)) return parseInt(v, 10)
  if (/^-?\d*\.\d+$/.test(v)) return parseFloat(v)
  return { __field: v }  // 视为字段引用
}

function resolveOperand(operand: any, rec: Record<string, any>): any {
  if (operand && typeof operand === 'object' && '__field' in operand) {
    return getField(rec, operand.__field)
  }
  return operand
}

/** 解析并求值单个原子比较，如 `pnl_pct < 0` / `type = "arbiter"` / `would_block = true` */
function evalAtom(atom: string, rec: Record<string, any>): boolean {
  const m = atom.match(/^(.+?)\s*(>=|<=|!=|=|>|<)\s*(.+)$/)
  if (!m) {
    // 裸字段：真值判断
    const val = getField(rec, atom.trim())
    return !!val
  }
  const [, lhsRaw, op, rhsRaw] = m
  const lhs = getField(rec, lhsRaw.trim())
  const rhs = resolveOperand(parseLiteral(rhsRaw), rec)
  switch (op) {
    case '=': return lhs == rhs // eslint-disable-line eqeqeq
    case '!=': return lhs != rhs // eslint-disable-line eqeqeq
    case '>': return Number(lhs) > Number(rhs)
    case '<': return Number(lhs) < Number(rhs)
    case '>=': return Number(lhs) >= Number(rhs)
    case '<=': return Number(lhs) <= Number(rhs)
    default: return false
  }
}

/** 求值 WHERE：仅支持顶层 OR，其内 AND（不支持括号，够用即可） */
function evalWhere(where: string | undefined, rec: Record<string, any>): boolean {
  if (!where) return true
  const orParts = where.split(/\s+OR\s+/i)
  return orParts.some((orPart) => {
    const andParts = orPart.split(/\s+AND\s+/i)
    return andParts.every((atom) => evalAtom(atom.trim(), rec))
  })
}

export function parseDql(src: string): DqlQuery {
  const query: DqlQuery = { columns: [], from: [], sort: [], valid: false }
  // 归一空白，按关键字切段
  const text = src.trim()

  const tableMatch = text.match(/TABLE\s+([\s\S]*?)(?:\n\s*FROM|\n\s*WHERE|\n\s*SORT|\n\s*LIMIT|$)/i)
  const fromMatch = text.match(/FROM\s+([\s\S]*?)(?:\n\s*WHERE|\n\s*SORT|\n\s*LIMIT|$)/i)
  const whereMatch = text.match(/WHERE\s+([\s\S]*?)(?:\n\s*SORT|\n\s*LIMIT|$)/i)
  const sortMatch = text.match(/SORT\s+([\s\S]*?)(?:\n\s*LIMIT|$)/i)
  const limitMatch = text.match(/LIMIT\s+(\d+)/i)

  if (!tableMatch) {
    query.error = '仅支持 TABLE 查询'
    return query
  }

  // 列
  const colsRaw = tableMatch[1].replace(/\n/g, ' ').trim()
  query.columns = colsRaw
    .split(',')
    .map((c) => c.trim())
    .filter(Boolean)
    .map((c) => {
      const asM = c.match(/^(.*?)\s+AS\s+(.+)$/i)
      if (asM) return { expr: asM[1].trim(), header: asM[2].trim().replace(/^["']|["']$/g, '') }
      return { expr: c, header: c }
    })

  // FROM: 取所有引号内文件夹
  if (fromMatch) {
    const folders = [...fromMatch[1].matchAll(/["']([^"']+)["']/g)].map((m) => m[1])
    query.from = folders
  }

  if (whereMatch) query.where = whereMatch[1].replace(/\n/g, ' ').trim()

  if (sortMatch) {
    query.sort = sortMatch[1]
      .replace(/\n/g, ' ')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .map((s) => {
        const parts = s.split(/\s+/)
        const dir = (parts[1] || 'ASC').toUpperCase() === 'DESC' ? 'DESC' : 'ASC'
        return { field: parts[0], dir: dir as 'ASC' | 'DESC' }
      })
  }

  if (limitMatch) query.limit = parseInt(limitMatch[1], 10)

  query.valid = true
  return query
}

export function runDql(query: DqlQuery, notes: VaultNote[]): DqlRow[] {
  if (!query.valid) return []

  let rows = notes.filter((n) => {
    if (query.from.length === 0) return true
    return query.from.some((f) => n.folder === f || n.folder.startsWith(f + '/'))
  })

  // WHERE
  rows = rows.filter((n) => evalWhere(query.where, toRecord(n)))

  // SORT
  if (query.sort.length) {
    rows = [...rows].sort((a, b) => {
      const ra = toRecord(a)
      const rb = toRecord(b)
      for (const s of query.sort) {
        const va = getField(ra, s.field)
        const vb = getField(rb, s.field)
        let cmp = 0
        if (va == null && vb == null) cmp = 0
        else if (va == null) cmp = 1
        else if (vb == null) cmp = -1
        else if (typeof va === 'number' && typeof vb === 'number') cmp = va - vb
        else cmp = String(va).localeCompare(String(vb))
        if (cmp !== 0) return s.dir === 'DESC' ? -cmp : cmp
      }
      return 0
    })
  }

  if (query.limit != null) rows = rows.slice(0, query.limit)

  return rows.map((n) => {
    const rec = toRecord(n)
    return {
      note: n,
      cells: query.columns.map((c) => getField(rec, c.expr)),
    }
  })
}
