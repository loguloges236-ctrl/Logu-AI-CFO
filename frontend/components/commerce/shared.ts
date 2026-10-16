"use client";

import { apiFetch, errorMessage } from "../../lib/api";

// Shared Phase 3 commerce types and the API call helper. Money, quantities and rates arrive as decimal
// strings from the API and are displayed as given: the browser never recomputes totals.

export { jsonBody, todayIso } from "../accounting/shared";

export async function callCommerce<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`/commerce${path}`, init);
  if (!response.ok) {
    throw new Error(await errorMessage(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export interface Page<T> {
  items: T[];
  total: number;
}

export interface Category {
  id: string;
  name: string;
  description: string | null;
}

export interface Product {
  id: string;
  sku: string;
  barcode: string | null;
  name: string;
  description: string | null;
  category_id: string | null;
  unit_of_measure: string;
  hsn_sac: string | null;
  tax_rate: string | null;
  purchase_price: string | null;
  selling_price: string | null;
  is_stock_item: boolean;
  is_active: boolean;
}

export interface Party {
  id: string;
  kind: "supplier" | "customer" | "both";
  name: string;
  tax_id: string | null;
  email: string | null;
  phone: string | null;
  is_active: boolean;
}

export interface Warehouse {
  id: string;
  code: string;
  name: string;
  allow_negative_stock: boolean;
  is_active: boolean;
}

export interface Location {
  id: string;
  warehouse_id: string;
  code: string;
  name: string;
  is_active: boolean;
}

export interface Balance {
  product_id: string;
  sku: string;
  name: string;
  warehouse_id: string;
  location_id: string;
  quantity: string;
  average_cost: string | null;
  value: string | null;
}

export interface Movement {
  id: string;
  seq: number;
  sku: string;
  location_id: string;
  movement_type: string;
  quantity_delta: string;
  unit_cost: string | null;
  balance_after: string;
  source_type: string | null;
  source_id: string | null;
  reason: string | null;
  created_at: string;
}

export interface DocLine {
  id: string;
  line_number: number;
  product_id: string | null;
  description: string;
  quantity: string;
  rate: string | null;
  tax_rate: string | null;
  line_subtotal: string;
  line_discount: string;
  taxable_amount: string;
  tax_amount: string;
  line_total: string;
}

export interface Totals {
  subtotal: string | null;
  discount_total: string | null;
  taxable_total: string | null;
  tax_total: string | null;
  grand_total: string | null;
}

export type PurchaseOrderLine = DocLine;

export interface ReceiptSummaryRow {
  purchase_order_line_id: string;
  product_id: string | null;
  ordered: string;
  received: string;
  billed: string;
}

export interface PurchaseOrder extends Totals {
  id: string;
  number: string;
  order_date: string;
  expected_date: string | null;
  party_id: string;
  status: string;
  notes: string | null;
  lines: PurchaseOrderLine[];
  receipt_summary: ReceiptSummaryRow[];
}

export interface PurchaseBill extends Totals {
  id: string;
  number: string;
  supplier_invoice_number: string;
  bill_date: string;
  due_date: string | null;
  party_id: string;
  purchase_order_id: string | null;
  status: string;
  payment_reference: string | null;
  payment_date: string | null;
  journal_entry_id: string | null;
  lines: (DocLine & { goods_receipt_line_id: string | null; expense_account_id: string | null })[];
}

export interface Quotation extends Totals {
  id: string;
  number: string;
  quotation_date: string;
  valid_until: string | null;
  party_id: string;
  status: string;
  notes: string | null;
  lines: DocLine[];
}

export interface SalesOrderLine extends DocLine {
  invoiced_quantity: string;
  open_quantity: string;
}

export interface SalesOrder extends Totals {
  id: string;
  number: string;
  order_date: string;
  party_id: string;
  quotation_id: string | null;
  status: string;
  lines: SalesOrderLine[];
}

export interface SalesInvoice extends Totals {
  id: string;
  number: string;
  invoice_date: string;
  due_date: string | null;
  party_id: string;
  sales_order_id: string | null;
  status: string;
  cost_of_goods_total: string | null;
  payment_reference: string | null;
  payment_received_on: string | null;
  journal_entry_id: string | null;
  lines: (DocLine & { sales_order_line_id: string | null; unit_cost: string | null; cost_amount: string | null })[];
}

export interface JobIssue {
  field: string;
  code: string;
  message: string;
}

// Mirrors the server's job shape (app/services/commerce_documents.py job_summary and the detail fields).
export interface Job {
  id: string;
  document_id: string;
  status: "queued" | "processing" | "needs_review" | "ready" | "approved" | "rejected" | "failed";
  classification: string | null;
  classification_confidence: string | null;
  provider_name: string;
  provider_available: boolean;
  issues: JobIssue[];
  approved_document_type: string | null;
  approved_document_id: string | null;
  created_at: string;
  updated_at: string;
  current_payload?: Record<string, unknown> | null;
  field_confidence?: Record<string, string> | null;
  review_acknowledged?: boolean;
}

export interface Dashboard {
  open_purchase_orders: number;
  bills_to_post: number;
  invoices_to_post: number;
  documents_to_review: number;
  zero_stock_lines: number;
  stock_value: string;
}

export const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  approved: "Approved",
  rejected: "Rejected",
  ordered: "Ordered",
  partially_received: "Partly received",
  received: "Received",
  cancelled: "Cancelled",
  posted: "Posted",
  voided: "Voided",
  sent: "Sent",
  accepted: "Accepted",
  expired: "Expired",
  converted: "Converted",
  confirmed: "Confirmed",
  partially_invoiced: "Partly invoiced",
  invoiced: "Invoiced",
  queued: "Queued",
  processing: "Processing",
  needs_review: "Needs review",
  ready: "Ready to approve",
  failed: "Failed",
};

export function statusClass(status: string): string {
  if (["approved", "posted", "received", "invoiced", "accepted", "converted", "ready"].includes(status)) return "status-active";
  if (["rejected", "cancelled", "voided", "failed", "expired"].includes(status)) return "status-revoked";
  return "status-pending";
}
