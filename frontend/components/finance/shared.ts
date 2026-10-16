"use client";

import { apiFetch, errorMessage } from "../../lib/api";

// Finance API client and display helpers. Money, rates and dates arrive as strings from the API and are shown
// as given. The browser never recomputes financial totals; formatting only changes how a value looks.

export async function callFinance<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`/finance${path}`, init);
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function jsonPost(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export function money(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  const parsed = Number(value);
  if (Number.isNaN(parsed)) {
    return value;
  }
  return "₹" + parsed.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function dateText(value: string | null | undefined): string {
  return value ? value : "—";
}

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now());
}

export interface Account {
  id: string;
  code: string;
  name: string;
  category: string;
  subtype: string;
  is_active: boolean;
}

export interface Obligation {
  id: string;
  kind: string;
  title: string;
  counterparty: string | null;
  amount: string;
  frequency: string;
  first_due_date: string;
  end_date: string | null;
  is_critical: boolean;
  status: string;
  debt_id: string | null;
}

export interface DueRow {
  key: string;
  kind: string;
  title: string;
  due_date: string;
  amount: string;
  bucket: string;
  obligation_id: string;
  rank: number;
  affordable: boolean;
  reasons: string[];
}

export interface OpenDocument {
  id: string;
  number: string;
  party: string;
  due_date: string | null;
  total: string;
  settled: string;
  outstanding: string;
  payment_status: string;
}

export interface DocumentSummary {
  open_total: string;
  overdue_total: string;
  overdue_count: number;
  items: OpenDocument[];
}

export interface Dashboard {
  cash_available: string;
  bank_balance: string | null;
  receivables_open: string;
  receivables_overdue: string;
  payables_open: string;
  payables_overdue: string;
  debt_outstanding: string;
  monthly_obligations: string;
  monthly_income: string;
  monthly_expenses: string;
  net_cash_flow: string;
  debt_burden: string | null;
  overdue_count: number;
  overdue_total: string;
  unreconciled_count: number;
  expected_cash_in_4_weeks: string;
  forecast: { week: number; week_end: string; inflow: string; outflow: string; closing: string; negative: boolean }[];
  alerts: { code: string; severity: string; message: string }[];
  upcoming: DueRow[];
}

export interface Forecast {
  opening_cash: string;
  weeks: { week: number; week_end: string; inflow: string; outflow: string; closing: string; negative: boolean }[];
}

export interface DebtRow {
  id: string;
  name: string;
  lender: string;
  principal: string;
  outstanding: string;
  emi: string;
  annual_rate: string;
  interest_total: string;
  installments: number;
  installments_paid_by_schedule: number;
  next_due_date: string | null;
  status: string;
}

export interface AlertNotice {
  id: string;
  kind: string;
  title: string;
  body: string;
  due_date: string | null;
  read: boolean;
}

export interface BankAccountRow {
  id: string;
  display_name: string;
  provider: string;
  currency: string;
  balance: string | null;
}

export interface BankTxn {
  id: string;
  date: string;
  amount: string;
  direction: string;
  description: string;
  balance_after: string | null;
}

export interface Suggestion {
  id: string;
  bank_transaction_id: string;
  journal_entry_id: string | null;
  kind: string;
  score: string;
  status: string;
}

export interface Settlement {
  id: string;
  direction: string;
  amount: string;
  settled_on: string;
  method: string;
  reference: string;
  state: string;
}

export interface SettlementRequest {
  id: string;
  direction: string;
  amount: string;
  settled_on: string;
  method: string;
  reference: string;
  status: string;
  settlement_id: string | null;
}

// Accounting endpoints are used only to read the chart of accounts for pickers. No journal is written from here.
export async function callAccounting<T>(path: string): Promise<T> {
  const { apiFetch, errorMessage } = await import("../../lib/api");
  const response = await apiFetch(`/accounting${path}`);
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  return (await response.json()) as T;
}

export const CASH_SUBTYPES = ["cash", "bank", "petty_cash", "other_financial"];
