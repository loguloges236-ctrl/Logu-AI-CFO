"use client";

import { apiFetch, errorMessage } from "../../lib/api";

// Shared accounting types, API call helper and money helpers. All money is handled as decimal
// strings from the API; totals are computed in integer paise so no binary floats are used.

export type Category = "asset" | "liability" | "equity" | "income" | "expense";

export const CATEGORY_LABELS: Record<Category, string> = {
  asset: "Asset",
  liability: "Liability",
  equity: "Equity",
  income: "Income",
  expense: "Expense",
};

export const SUBTYPES: { value: string; label: string }[] = [
  { value: "header", label: "Header (group only)" },
  { value: "cash", label: "Cash" },
  { value: "petty_cash", label: "Petty cash" },
  { value: "bank", label: "Bank" },
  { value: "other_financial", label: "Other financial account" },
  { value: "receivable", label: "Receivable" },
  { value: "inventory", label: "Inventory" },
  { value: "payable", label: "Payable" },
  { value: "loan", label: "Loan" },
  { value: "capital", label: "Capital" },
  { value: "sales", label: "Sales" },
  { value: "operating_expense", label: "Operating expense" },
  { value: "general", label: "General" },
];

export interface Account {
  id: string;
  code: string;
  name: string;
  category: Category;
  subtype: string;
  description: string | null;
  parent_id: string | null;
  is_active: boolean;
  is_system: boolean;
}

export interface EntryLine {
  id: string;
  line_number: number;
  account_id: string;
  account_code: string;
  debit: string;
  credit: string;
  description: string | null;
  tax_name: string | null;
  tax_type: string | null;
  tax_rate: string | null;
  tax_amount: string | null;
}

export interface Entry {
  id: string;
  entry_number: number | null;
  transaction_date: string;
  posting_date: string | null;
  reference_type: string | null;
  reference_id: string | null;
  description: string;
  status: "draft" | "posted";
  reverses_entry_id: string | null;
  lines: EntryLine[];
}

export interface EntryList {
  items: Entry[];
  total: number;
  limit: number;
  offset: number;
}

export interface Period {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
  status: "open" | "closed";
  closed_at: string | null;
}

export interface LedgerRow {
  posting_date: string;
  entry_number: number;
  description: string;
  debit: string;
  credit: string;
  balance: string;
}

export interface Ledger {
  account_id: string;
  account_code: string;
  account_name: string;
  category: Category;
  opening_balance: string;
  debit_total: string;
  credit_total: string;
  closing_balance: string;
  rows: LedgerRow[];
}

export interface TrialBalance {
  rows: { code: string; name: string; category: Category; debit_balance: string; credit_balance: string }[];
  total_debit: string;
  total_credit: string;
  is_balanced: boolean;
}

export interface ProfitAndLoss {
  income: string;
  expenses: string;
  net_profit: string;
}

export interface BalanceSheet {
  assets: string;
  liabilities: string;
  equity: string;
  current_earnings: string;
  liabilities_and_equity: string;
  is_balanced: boolean;
}

export interface Dashboard {
  draft_entries: number;
  posted_entries: number;
  open_period: string | null;
  cash_total: string;
  cash_accounts: number;
  trial_balance_balanced: boolean;
  balance_sheet_balanced: boolean;
}

/** Calls the accounting API. Throws an Error whose message is the server's safe message. */
export async function callAccounting<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`/accounting${path}`, init);
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function jsonBody(method: "POST" | "PUT" | "PATCH", body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

/** Groups thousands for display only. The input string is never converted to a float. */
export function money(value: string | null | undefined): string {
  const text = (value ?? "0.00").trim();
  const negative = text.startsWith("-");
  const unsigned = negative ? text.slice(1) : text;
  const [whole = "0", fraction = "00"] = unsigned.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${grouped}.${fraction.padEnd(2, "0").slice(0, 2)}`;
}

/** Parses a user-entered amount into integer paise. Returns null when the text is not a valid amount. */
export function toPaise(value: string): number | null {
  const text = value.trim();
  if (text === "") return 0;
  if (!/^\d{1,13}(\.\d{1,2})?$/.test(text)) return null;
  const [whole = "0", fraction = ""] = text.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}

export function fromPaise(paise: number): string {
  const sign = paise < 0 ? "-" : "";
  const absolute = Math.abs(paise);
  const whole = Math.floor(absolute / 100);
  const fraction = String(absolute % 100).padStart(2, "0");
  return `${sign}${whole}.${fraction}`;
}

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}
