/**
 * useWizardDraft — localStorage-backed draft persistence for multi-step wizards.
 *
 * Usage:
 *   const { saveDraft, restoreDraft, clearDraft, hasDraft } = useWizardDraft("my-wizard-key");
 *
 * Auto-saves on every data change (debounced 2s by the caller).
 * Restore on mount — prompts the user if a draft exists.
 * Clears on successful submit.
 */
import { useCallback, useMemo } from "react";

const DRAFT_PREFIX = "wizard_draft:";
const DRAFT_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours

interface DraftEntry {
  data: unknown;
  step?: number;
  savedAt: number;
}

interface UseWizardDraftReturn {
  /** Persist current state to localStorage */
  saveDraft: (payload: { data: unknown; step?: number }) => void;
  /** Read and parse the saved draft. Returns null if expired or corrupted. */
  restoreDraft: () => (DraftEntry | null);
  /** Remove the saved draft from storage */
  clearDraft: () => void;
  /** True if a valid, non-expired draft exists */
  hasDraft: boolean;
}

export function useWizardDraft(key: string): UseWizardDraftReturn {
  const storageKey = useMemo(() => DRAFT_PREFIX + key, [key]);

  const saveDraft = useCallback(
    (payload: { data: unknown; step?: number }) => {
      try {
        const entry: DraftEntry = {
          data: payload.data,
          step: payload.step,
          savedAt: Date.now(),
        };
        localStorage.setItem(storageKey, JSON.stringify(entry));
      } catch {
        // Storage full or unavailable — silently fail
      }
    },
    [storageKey],
  );

  const restoreDraft = useCallback((): DraftEntry | null => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return null;
      const entry: DraftEntry = JSON.parse(raw);
      if (Date.now() - entry.savedAt > DRAFT_TTL_MS) {
        localStorage.removeItem(storageKey);
        return null;
      }
      return entry;
    } catch {
      return null;
    }
  }, [storageKey]);

  const clearDraft = useCallback(() => {
    try {
      localStorage.removeItem(storageKey);
    } catch {
      // ignore
    }
  }, [storageKey]);

  const hasDraft = useMemo(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return false;
      const entry: DraftEntry = JSON.parse(raw);
      return Date.now() - entry.savedAt <= DRAFT_TTL_MS;
    } catch {
      return false;
    }
  }, [storageKey]);

  return { saveDraft, restoreDraft, clearDraft, hasDraft };
}
