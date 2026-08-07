/**
 * LLM / DeepSeek 计费统计 — 独立主功能页
 */
import React from 'react'
import LLMBillingPanel from '@/components/settings-page/LLMBillingPanel'

export default function LLMBillingPage() {
  return (
    <div className="min-h-full bg-background text-foreground p-6">
      <LLMBillingPanel />
    </div>
  )
}
