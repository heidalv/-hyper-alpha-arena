import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Parse a date string from the backend as UTC.
 * Backend returns ISO strings without timezone suffix (e.g. "2026-03-24T10:08:55").
 * Without 'Z', JS treats them as local time — causing an 8-hour offset in UTC+8.
 * This function appends 'Z' when no timezone indicator is present.
 */
export function parseUTC(dateStr: string | null | undefined): Date | null {
  if (!dateStr) return null
  const s = dateStr.trim()
  if (!s) return null
  // Already has timezone info
  if (s.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(s) || /[+-]\d{4}$/.test(s)) {
    return new Date(s)
  }
  return new Date(s + 'Z')
}

/**
 * Format a backend UTC date string to local time string (HH:MM).
 */
export function fmtTime(dateStr: string | null | undefined): string {
  const d = parseUTC(dateStr)
  if (!d || isNaN(d.getTime())) return '--'
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

/**
 * Format a backend UTC date string to local date+time string.
 */
export function fmtDateTime(dateStr: string | null | undefined): string {
  const d = parseUTC(dateStr)
  if (!d || isNaN(d.getTime())) return '--'
  return d.toLocaleString('zh-CN')
}

/**
 * Format a backend UTC date string to short date+time (MM/DD HH:MM).
 */
export function fmtShortDateTime(dateStr: string | null | undefined): string {
  const d = parseUTC(dateStr)
  if (!d || isNaN(d.getTime())) return '-'
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

/**
 * Copy text to clipboard with fallback for non-secure contexts.
 * navigator.clipboard is only available in secure contexts (HTTPS or localhost).
 * This function provides a fallback using execCommand for HTTP environments.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  // Try modern Clipboard API first (requires secure context)
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch (err) {
      console.warn('Clipboard API failed, trying fallback:', err)
    }
  }

  // Fallback for non-secure contexts (HTTP with non-localhost)
  try {
    const textArea = document.createElement('textarea')
    textArea.value = text
    textArea.style.position = 'fixed'
    textArea.style.left = '-9999px'
    textArea.style.top = '-9999px'
    document.body.appendChild(textArea)
    textArea.focus()
    textArea.select()
    const success = document.execCommand('copy')
    document.body.removeChild(textArea)
    if (!success) {
      throw new Error('execCommand copy failed')
    }
    return true
  } catch (err) {
    console.error('Failed to copy text:', err)
    return false
  }
}
