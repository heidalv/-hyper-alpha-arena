/**
 * Utils Unit Tests
 */
import { describe, it, expect } from 'vitest'
import { cn } from '@/lib/utils'

describe('cn utility', () => {
  it('merges class names', () => {
    expect(cn('foo', 'bar')).toBe('foo bar')
  })

  it('handles empty strings', () => {
    expect(cn('', 'bar')).toBe('bar')
  })

  it('handles undefined/null', () => {
    expect(cn('foo', undefined, null, 'bar')).toBe('foo bar')
  })

  it('deduplicates tailwind classes', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4')
  })
})
