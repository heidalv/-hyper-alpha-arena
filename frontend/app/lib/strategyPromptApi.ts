/**
 * 策略提示词管理 API 客户端
 */
import { apiRequest } from './api';

export interface FieldSchema {
  type: string;
  required: boolean;
  enum?: string[];
  range?: [number, number];
}

export interface PromptData {
  label: string;
  system_prompt: string;
  task_prompt: string;
  is_overridden: boolean;
  locked_fields: string[];
  schema: Record<string, FieldSchema>;
}

export interface TestResult {
  success: boolean;
  json_valid?: boolean;
  parsed?: Record<string, any>;
  present_fields?: string[];
  missing_fields?: string[];
  all_fields_present?: boolean;
  raw_response?: string;
  error?: string;
}

export type Tier = 'mid' | 'long';

export async function fetchPrompts(tier: Tier): Promise<Record<string, PromptData>> {
  const resp = await apiRequest(`/strategy-prompt/${tier}`);
  return (await resp.json()).prompts;
}

export async function updatePrompt(tier: Tier, taskId: string, systemPrompt: string, taskPrompt: string): Promise<{ success: boolean; error?: string }> {
  const resp = await apiRequest(`/strategy-prompt/${tier}`, {
    method: 'PUT',
    body: JSON.stringify({ task_id: taskId, system_prompt: systemPrompt, task_prompt: taskPrompt }),
  });
  return resp.json();
}

export async function resetPrompt(tier: Tier, taskId: string): Promise<{ success: boolean }> {
  const resp = await apiRequest(`/strategy-prompt/${tier}/reset`, {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId }),
  });
  return resp.json();
}

export async function testPrompt(tier: Tier, taskId: string, systemPrompt: string, taskPrompt: string, symbol?: string): Promise<TestResult> {
  const resp = await apiRequest(`/strategy-prompt/${tier}/test`, {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId, system_prompt: systemPrompt, task_prompt: taskPrompt, symbol: symbol || 'BTC' }),
  });
  return resp.json();
}
