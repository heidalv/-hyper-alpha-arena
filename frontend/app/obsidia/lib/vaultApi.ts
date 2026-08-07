/**
 * vaultApi —— 前端访问后端 /api/vault/* 的封装
 * --------------------------------------------
 * 对应 backend/api/vault_routes.py：tree / file / index / canvas。
 */

import { apiRequest } from '@/lib/api'

export interface VaultTreeNode {
  type: 'folder' | 'file' | 'canvas'
  name: string
  path: string
  children?: VaultTreeNode[]
}

export interface VaultNote {
  path: string
  name: string
  folder: string
  frontmatter: Record<string, any>
  outlinks_raw: string[]
  outlinks: string[]
  backlinks: string[]
  mtime: number
  size: number
}

export interface VaultIndex {
  vault: string
  count: number
  notes: VaultNote[]
}

export interface VaultFile {
  path: string
  name: string
  frontmatter: Record<string, any>
  body: string
  raw: string
  outlinks: string[]
  mtime: number
}

export interface VaultCanvas {
  path: string
  name: string
  data: {
    nodes?: Array<Record<string, any>>
    edges?: Array<Record<string, any>>
  }
}

export async function fetchVaultTree(): Promise<VaultTreeNode> {
  return (await apiRequest('/vault/tree')).json()
}

export async function fetchVaultIndex(): Promise<VaultIndex> {
  return (await apiRequest('/vault/index')).json()
}

export async function fetchVaultFile(path: string): Promise<VaultFile> {
  return (await apiRequest(`/vault/file?path=${encodeURIComponent(path)}`)).json()
}

export async function fetchVaultCanvas(path: string): Promise<VaultCanvas> {
  return (await apiRequest(`/vault/canvas?path=${encodeURIComponent(path)}`)).json()
}
