/**
 * Shared types for the AiStrategyWizard component tree.
 * Extracted from the original 2121-line monolithic component.
 */
import type { Dispatch, SetStateAction } from "react";

/** Trading pair metadata */
export interface SymbolInfo {
  symbol: string;
  name?: string;
  type?: string;
}

/** Account reference */
export interface AccountInfo {
  id: number;
  name: string;
}

/** Aggregate wizard data — updated via partial merges */
export interface WizardData {
  // Step 1: Requirement
  name: string;
  description: string;
  accountId: number | null;
  targetSymbols: string[];
  primarySymbol: string;
  timeframe: string;
  tradingStyleId: number | null;

  // Step 2: AI Framework
  frameworkPrompt: string;
  generatedFramework: Record<string, unknown> | null;
  generatedConfidence: number;

  // Step 3: Signals
  signalDescription: string;
  generatedSignals: Record<string, unknown> | null;
  signalPoolIds: number[];

  // Step 4: Factor Pool
  enabledFactors: string[];
  factorWeights: Record<string, number>;

  // Step 5: Risk Config
  maxPositionSize: number;
  stopLossPct: number;
  takeProfitPct: number;
  maxLeverage: number;
  defaultLeverage: number;

  // Step 6: Preview / submit
  strategyId: string | null;
}

/** Default wizard data */
export const DEFAULT_WIZARD_DATA: WizardData = {
  name: "",
  description: "",
  accountId: null,
  targetSymbols: ["BTC"],
  primarySymbol: "BTC",
  timeframe: "15m",
  tradingStyleId: null,
  frameworkPrompt: "",
  generatedFramework: null,
  generatedConfidence: 0,
  signalDescription: "",
  generatedSignals: null,
  signalPoolIds: [],
  enabledFactors: [],
  factorWeights: {},
  maxPositionSize: 0.2,
  stopLossPct: 0.05,
  takeProfitPct: 0.1,
  maxLeverage: 20,
  defaultLeverage: 10,
  strategyId: null,
};

/** Props shared by all step components */
export interface StepProps {
  data: WizardData;
  updateData: (partial: Partial<WizardData>) => void;
  submitting: boolean;
  setSubmitting: Dispatch<SetStateAction<boolean>>;
  generating: boolean;
  setGenerating: Dispatch<SetStateAction<boolean>>;
}

/** Step definition for the container */
export interface StepDefinition {
  id: number;
  title: string;
  description: string;
  /** Returns true if the user can proceed past this step */
  canProceed: (data: WizardData) => boolean;
}
