"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { Notice, StatusPill } from "./parts";
import {
  Job,
  Page,
  Party,
  Product,
  PurchaseBill,
  PurchaseOrder,
  Quotation,
  SalesInvoice,
  SalesOrder,
  callCommerce,
  jsonBody,
  todayIso,
} from "./shared";

// Purchase and sales screens. Documents are created through the controlled services. Totals come from the API.

function useFetch<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (path === null) return;
    let active = true;
    callCommerce<T>(path)
      .then((value) => {
        if (active) {
          setData(value);
          setError("");
        }
      })
      .catch((err: Error) => {
        if (active) setError(err.message);
      });
    return () => {
      active = false;
    };
  }, [path, version]);
  return { data, error, reload: () => setVersion((v) => v + 1) };
}

interface LineDraft {
  key: number;
  product_id: string;
  description: string;
  quantity: string;
  rate: string;
  tax_rate: string;
}

let lineKey = 0;
function blankLine(): LineDraft {
  lineKey += 1;
  return { key: lineKey, product_id: "", description: "", quantity: "", rate: "", tax_rate: "" };
}

// A product-based line editor. Optional rate is omitted when empty so the server can apply the item's own price.
function LineEditor({ lines, setLines, products, withRate }: { lines: LineDraft[]; setLines: (l: LineDraft[]) => void; products: Product[]; withRate: boolean }) {
  function update(key: number, patch: Partial<LineDraft>) {
    setLines(lines.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  }
  return (
    <fieldset className="line-editor">
      <legend>Items</legend>
      {lines.map((line, index) => (
        <div key={line.key} className="line-row">
          <label>Item {index + 1}
            <select value={line.product_id} onChange={(e) => update(line.key, { product_id: e.target.value })}>
              <option value="">Choose…</option>
              {products.map((p) => <option key={p.id} value={p.id}>{p.sku} · {p.name}</option>)}
            </select>
          </label>
          <label>Quantity<input required inputMode="decimal" value={line.quantity} onChange={(e) => update(line.key, { quantity: e.target.value })} /></label>
          {withRate && <label>Rate<input inputMode="decimal" value={line.rate} onChange={(e) => update(line.key, { rate: e.target.value })} /></label>}
          <label>Tax %<input inputMode="decimal" value={line.tax_rate} onChange={(e) => update(line.key, { tax_rate: e.target.value })} /></label>
        </div>
      ))}
      <button className="secondary-button" type="button" onClick={() => setLines([...lines, blankLine()])}>Add item</button>
    </fieldset>
  );
}

// Purchase requests carry no price or tax; orders, quotations and invoices do.
function linePayload(lines: LineDraft[], priced: boolean) {
  return lines
    .filter((l) => l.product_id)
    .map((l) => ({
      product_id: l.product_id,
      quantity: l.quantity,
      ...(priced ? { tax_rate: l.tax_rate || null, ...(l.rate ? { rate: l.rate } : {}) } : {}),
    }));
}

function useProducts() {
  return useFetch<Page<Product>>("/products?active_only=true&limit=200");
}

function useParties(kind: "supplier" | "customer") {
  return useFetch<Party[]>(`/parties?kind=${kind}&active_only=true`);
}

// ---------------------------------------------------------------- purchases

interface OrderSummary {
  id: string;
  number: string;
  order_date: string;
  status: string;
  grand_total: string | null;
}

export function PurchasesView() {
  const { data: requests, reload: reloadRequests, error: requestsError } = useFetch<{ id: string; number: string; status: string; request_date: string }[]>("/purchase-requests");
  const { data: orders, reload: reloadOrders, error: ordersError } = useFetch<OrderSummary[]>("/purchase-orders");
  const { data: bills } = useFetch<{ id: string; number: string; supplier_invoice_number: string; status: string; grand_total: string | null }[]>("/purchase-bills");
  const { data: suppliers } = useParties("supplier");
  const { data: products } = useProducts();
  const [message, setMessage] = useState("");
  const [prLines, setPrLines] = useState([blankLine()]);
  const [prDate, setPrDate] = useState(todayIso());
  const [poForm, setPoForm] = useState({ party_id: "", order_date: todayIso(), expected_date: "" });
  const [poLines, setPoLines] = useState([blankLine()]);

  async function createRequest(e: FormEvent) {
    e.preventDefault();
    setMessage("");
    try {
      await callCommerce("/purchase-requests", jsonBody("POST", { request_date: prDate, lines: linePayload(prLines, false) }));
      setPrLines([blankLine()]);
      reloadRequests();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function createOrder(e: FormEvent) {
    e.preventDefault();
    setMessage("");
    try {
      await callCommerce("/purchase-orders", jsonBody("POST", { ...poForm, expected_date: poForm.expected_date || null, lines: linePayload(poLines, true) }));
      setPoLines([blankLine()]);
      reloadOrders();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function decide(id: string, decision: "approve" | "reject") {
    setMessage("");
    try {
      await callCommerce(`/purchase-requests/${id}/decision?decision=${decision}`, { method: "POST" });
      reloadRequests();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  return (
    <>
      <Notice message={message || requestsError || ordersError} />
      <h2>Purchase requests</h2>
      <ul className="document-list">
        {requests?.map((r) => (
          <li key={r.id} className="document-card">
            <div className="document-head">
              <div><h3>{r.number}</h3><p className="muted small">{r.request_date}</p></div>
              <StatusPill status={r.status} />
            </div>
            {r.status === "draft" && (
              <div className="document-actions">
                <button className="primary-button" type="button" onClick={() => decide(r.id, "approve")}>Approve</button>
                <button className="secondary-button" type="button" onClick={() => decide(r.id, "reject")}>Reject</button>
              </div>
            )}
          </li>
        ))}
      </ul>
      <form className="settings-card" onSubmit={createRequest}>
        <h3>New purchase request</h3>
        <label>Date<input type="date" value={prDate} onChange={(e) => setPrDate(e.target.value)} /></label>
        <LineEditor lines={prLines} setLines={setPrLines} products={products?.items ?? []} withRate={false} />
        <button className="primary-button" type="submit">Save request</button>
      </form>

      <h2>Purchase orders</h2>
      <ul className="document-list">
        {orders?.map((o) => (
          <li key={o.id} className="document-card">
            <Link href={`/commerce/purchases/${o.id}`}>
              <div className="document-head">
                <div><h3>{o.number}</h3><p className="muted small">{o.order_date} · Total {o.grand_total ?? "—"}</p></div>
                <StatusPill status={o.status} />
              </div>
            </Link>
          </li>
        ))}
      </ul>
      <form className="settings-card" onSubmit={createOrder}>
        <h3>New purchase order</h3>
        <label>Supplier
          <select required value={poForm.party_id} onChange={(e) => setPoForm({ ...poForm, party_id: e.target.value })}>
            <option value="">Choose…</option>
            {suppliers?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label>Order date<input type="date" value={poForm.order_date} onChange={(e) => setPoForm({ ...poForm, order_date: e.target.value })} /></label>
        <LineEditor lines={poLines} setLines={setPoLines} products={products?.items ?? []} withRate />
        <button className="primary-button" type="submit">Save order</button>
      </form>

      <h2>Purchase bills</h2>
      <ul className="document-list">
        {bills?.map((b) => (
          <li key={b.id} className="document-card">
            <Link href={`/commerce/purchases/bills/${b.id}`}>
              <div className="document-head">
                <div><h3>{b.number}</h3><p className="muted small">Supplier invoice {b.supplier_invoice_number} · {b.grand_total ?? "—"}</p></div>
                <StatusPill status={b.status} />
              </div>
            </Link>
          </li>
        ))}
      </ul>
      {bills?.length === 0 && <p className="muted">No bills yet. Bills are created from receipts on an order, or from an uploaded bill in Document intake.</p>}
    </>
  );
}

export function PurchaseDetailView({ orderId }: { orderId: string }) {
  const { data: order, error, reload } = useFetch<PurchaseOrder>(`/purchase-orders/${orderId}`);
  const { data: receipts, reload: reloadReceipts } = useFetch<{ id: string; number: string; receipt_date: string }[]>(`/goods-receipts?purchase_order_id=${orderId}`);
  const { data: warehouses } = useFetch<{ id: string; name: string }[]>("/warehouses");
  const { data: locations } = useFetch<{ id: string; warehouse_id: string; code: string }[]>("/locations");
  const [message, setMessage] = useState("");
  const [grn, setGrn] = useState({ warehouse_id: "", location_id: "", receipt_date: todayIso() });
  const [grnQty, setGrnQty] = useState<Record<string, string>>({});
  const [bill, setBill] = useState({ supplier_invoice_number: "", bill_date: todayIso(), due_date: "" });

  async function run(path: string, body?: unknown, method: "POST" | "PUT" = "POST") {
    setMessage("");
    try {
      await callCommerce(path, body === undefined ? { method } : jsonBody(method, body));
      reload();
      reloadReceipts();
      return true;
    } catch (err) {
      setMessage((err as Error).message);
      return false;
    }
  }

  async function receive(e: FormEvent) {
    e.preventDefault();
    const lines = order?.lines
      .map((l) => ({ purchase_order_line_id: l.id, quantity: grnQty[l.id] ?? "" }))
      .filter((l) => l.quantity !== "" && Number(l.quantity) > 0);
    if (!lines?.length) {
      setMessage("Enter a received quantity for at least one line. Partial receipts are allowed.");
      return;
    }
    if (await run("/goods-receipts", { purchase_order_id: orderId, ...grn, location_id: grn.location_id, lines })) setGrnQty({});
  }

  async function createBill(e: FormEvent) {
    e.preventDefault();
    setMessage("");
    try {
      const lines: { goods_receipt_line_id: string; quantity: string }[] = [];
      for (const r of receipts ?? []) {
        const detail = await callCommerce<{ lines: { id: string; quantity: string }[] }>(`/goods-receipts/${r.id}`);
        for (const line of detail.lines) lines.push({ goods_receipt_line_id: line.id, quantity: line.quantity });
      }
      if (!lines.length) throw new Error("Nothing has been received yet, so there is nothing to bill.");
      await callCommerce("/purchase-bills", jsonBody("POST", { party_id: order?.party_id, purchase_order_id: orderId, ...bill, due_date: bill.due_date || null, lines }));
      reload();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  if (error) return <Notice message={error} />;
  if (!order) return <p className="muted">Loading…</p>;
  const canReceive = order.status === "approved" || order.status === "partially_received";

  return (
    <>
      <Notice message={message} />
      <section className="settings-card">
        <div className="document-head"><h2>{order.number}</h2><StatusPill status={order.status} /></div>
        <p className="muted small">Ordered {order.order_date}{order.expected_date ? ` · Expected ${order.expected_date}` : ""}</p>
        <ul className="document-list">
          {order.lines.map((l) => {
            const s = order.receipt_summary.find((r) => r.purchase_order_line_id === l.id);
            return (
              <li key={l.id} className="document-card small">
                <strong>{l.description}</strong> · Ordered {l.quantity} @ {l.rate} · Total {l.line_total}
                <br />Received {s?.received ?? "0"} · Billed {s?.billed ?? "0"}
              </li>
            );
          })}
        </ul>
        <p className="small">Grand total {order.grand_total ?? "—"}</p>
        {order.status === "draft" && (
          <button className="primary-button" type="button" onClick={() => run(`/purchase-orders/${orderId}/status?status=approved`)}>Approve order</button>
        )}
        {(order.status === "draft" || order.status === "approved") && (
          <button className="secondary-button" type="button" onClick={() => run(`/purchase-orders/${orderId}/status?status=cancelled`)}>Cancel order</button>
        )}
      </section>

      <h2>Goods receipts</h2>
      <ul className="document-list">
        {receipts?.map((r) => <li key={r.id} className="document-card">{r.number} · {r.receipt_date}</li>)}
      </ul>

      {canReceive && (
        <form className="settings-card" onSubmit={receive}>
          <h3>Record receipt (partial allowed)</h3>
          <label>Warehouse
            <select required value={grn.warehouse_id} onChange={(e) => setGrn({ ...grn, warehouse_id: e.target.value, location_id: "" })}>
              <option value="">Choose…</option>
              {warehouses?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </label>
          <label>Location
            <select required value={grn.location_id} onChange={(e) => setGrn({ ...grn, location_id: e.target.value })}>
              <option value="">Choose…</option>
              {locations?.filter((l) => l.warehouse_id === grn.warehouse_id).map((l) => <option key={l.id} value={l.id}>{l.code}</option>)}
            </select>
          </label>
          <label>Received on<input type="date" value={grn.receipt_date} onChange={(e) => setGrn({ ...grn, receipt_date: e.target.value })} /></label>
          {order.lines.map((l) => {
            const s = order.receipt_summary.find((r) => r.purchase_order_line_id === l.id);
            const open = s ? Number(l.quantity) - Number(s.received) : Number(l.quantity);
            return (
              <label key={l.id}>{l.description} (open {open})
                <input inputMode="decimal" value={grnQty[l.id] ?? ""} onChange={(e) => setGrnQty({ ...grnQty, [l.id]: e.target.value })} />
              </label>
            );
          })}
          <button className="primary-button" type="submit">Save receipt</button>
        </form>
      )}

      {(receipts?.length ?? 0) > 0 && (
        <form className="settings-card" onSubmit={createBill}>
          <h3>Create purchase bill from receipts</h3>
          <p className="hint">Stock lines are priced from the receipt cost (three-way match). Quantities above what was received are refused.</p>
          <label>Supplier invoice number<input required maxLength={60} value={bill.supplier_invoice_number} onChange={(e) => setBill({ ...bill, supplier_invoice_number: e.target.value })} /></label>
          <label>Bill date<input required type="date" value={bill.bill_date} onChange={(e) => setBill({ ...bill, bill_date: e.target.value })} /></label>
          <label>Due date<input type="date" value={bill.due_date} onChange={(e) => setBill({ ...bill, due_date: e.target.value })} /></label>
          <button className="primary-button" type="submit">Create bill (draft)</button>
        </form>
      )}
    </>
  );
}

export function PurchaseBillDetailView({ billId }: { billId: string }) {
  const { data: bill, error, reload } = useFetch<PurchaseBill>(`/purchase-bills/${billId}`);
  const [message, setMessage] = useState("");
  const [payment, setPayment] = useState({ reference: "", paid_on: todayIso() });

  async function act(path: string, body?: unknown) {
    setMessage("");
    try {
      await callCommerce(path, body === undefined ? { method: "POST" } : jsonBody("POST", body));
      reload();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  if (error) return <Notice message={error} />;
  if (!bill) return <p className="muted">Loading…</p>;
  return (
    <>
      <Notice message={message} />
      <section className="settings-card">
        <div className="document-head"><h2>{bill.number}</h2><StatusPill status={bill.status} /></div>
        <p className="muted small">Supplier invoice {bill.supplier_invoice_number} · Bill date {bill.bill_date}</p>
        <ul className="document-list">
          {bill.lines.map((l) => (
            <li key={l.id} className="document-card small">{l.description} · {l.quantity} · Total {l.line_total}</li>
          ))}
        </ul>
        <p className="small">Grand total {bill.grand_total ?? "—"} · Journal entry {bill.journal_entry_id ?? "not posted"}</p>
        {bill.status === "draft" && <button className="primary-button" type="button" onClick={() => act(`/purchase-bills/${billId}/post`)}>Post bill to accounts</button>}
        {bill.status === "posted" && <button className="secondary-button" type="button" onClick={() => act(`/purchase-bills/${billId}/void`)}>Void (reversing entry)</button>}
      </section>
      {bill.status === "posted" && (
        <form className="settings-card" onSubmit={(e) => { e.preventDefault(); act(`/purchase-bills/${billId}/payment-reference`, payment); }}>
          <h3>Payment reference</h3>
          <p className="hint">Records a reference only. Phase 3 does not execute payments.</p>
          <label>Reference<input required maxLength={80} value={payment.reference} onChange={(e) => setPayment({ ...payment, reference: e.target.value })} /></label>
          <label>Paid on<input required type="date" value={payment.paid_on} onChange={(e) => setPayment({ ...payment, paid_on: e.target.value })} /></label>
          <button className="primary-button" type="submit">Save reference</button>
        </form>
      )}
    </>
  );
}

// ---------------------------------------------------------------- sales

export function SalesView() {
  const { data: quotes, reload: reloadQuotes, error: quotesError } = useFetch<{ id: string; number: string; status: string; quotation_date: string; grand_total: string | null }[]>("/quotations");
  const { data: orders } = useFetch<{ id: string; number: string; status: string; order_date: string; grand_total: string | null }[]>("/sales-orders");
  const { data: invoices } = useFetch<{ id: string; number: string; status: string; invoice_date: string; grand_total: string | null }[]>("/sales-invoices");
  const { data: customers } = useParties("customer");
  const { data: products } = useProducts();
  const [message, setMessage] = useState("");
  const [form, setForm] = useState({ party_id: "", quotation_date: todayIso(), valid_until: "" });
  const [lines, setLines] = useState([blankLine()]);

  async function createQuote(e: FormEvent) {
    e.preventDefault();
    setMessage("");
    try {
      await callCommerce("/quotations", jsonBody("POST", { ...form, valid_until: form.valid_until || null, lines: linePayload(lines, true) }));
      setForm({ party_id: "", quotation_date: todayIso(), valid_until: "" });
      setLines([blankLine()]);
      reloadQuotes();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  return (
    <>
      <Notice message={message || quotesError} />
      <h2>Quotations</h2>
      <ul className="document-list">
        {quotes?.map((q) => (
          <li key={q.id} className="document-card">
            <Link href={`/commerce/sales/quotations/${q.id}`}>
              <div className="document-head"><div><h3>{q.number}</h3><p className="muted small">{q.quotation_date} · {q.grand_total ?? "—"}</p></div><StatusPill status={q.status} /></div>
            </Link>
          </li>
        ))}
      </ul>
      <h2>Sales orders</h2>
      <ul className="document-list">
        {orders?.map((o) => (
          <li key={o.id} className="document-card">
            <Link href={`/commerce/sales/orders/${o.id}`}>
              <div className="document-head"><div><h3>{o.number}</h3><p className="muted small">{o.order_date} · {o.grand_total ?? "—"}</p></div><StatusPill status={o.status} /></div>
            </Link>
          </li>
        ))}
      </ul>
      <h2>Invoices</h2>
      <ul className="document-list">
        {invoices?.map((i) => (
          <li key={i.id} className="document-card">
            <Link href={`/commerce/sales/invoices/${i.id}`}>
              <div className="document-head"><div><h3>{i.number}</h3><p className="muted small">{i.invoice_date} · {i.grand_total ?? "—"}</p></div><StatusPill status={i.status} /></div>
            </Link>
          </li>
        ))}
      </ul>
      <form className="settings-card" onSubmit={createQuote}>
        <h3>New quotation</h3>
        <label>Customer
          <select required value={form.party_id} onChange={(e) => setForm({ ...form, party_id: e.target.value })}>
            <option value="">Choose…</option>
            {customers?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label>Quotation date<input type="date" value={form.quotation_date} onChange={(e) => setForm({ ...form, quotation_date: e.target.value })} /></label>
        <label>Valid until<input type="date" value={form.valid_until} onChange={(e) => setForm({ ...form, valid_until: e.target.value })} /></label>
        <LineEditor lines={lines} setLines={setLines} products={products?.items ?? []} withRate />
        <button className="primary-button" type="submit">Save quotation</button>
      </form>
    </>
  );
}

export function QuotationDetailView({ quotationId }: { quotationId: string }) {
  const { data: quote, error, reload } = useFetch<Quotation>(`/quotations/${quotationId}`);
  const [message, setMessage] = useState("");

  async function act(path: string, body?: unknown) {
    setMessage("");
    try {
      await callCommerce(path, body === undefined ? { method: "POST" } : jsonBody("POST", body));
      reload();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  if (error) return <Notice message={error} />;
  if (!quote) return <p className="muted">Loading…</p>;
  return (
    <>
      <Notice message={message} />
      <section className="settings-card">
        <div className="document-head"><h2>{quote.number}</h2><StatusPill status={quote.status} /></div>
        <p className="muted small">Quoted {quote.quotation_date}{quote.valid_until ? ` · Valid until ${quote.valid_until}` : ""}</p>
        <ul className="document-list">
          {quote.lines.map((l) => (
            <li key={l.id} className="document-card small">{l.description} · {l.quantity} @ {l.rate} · Total {l.line_total}</li>
          ))}
        </ul>
        <p className="small">Grand total {quote.grand_total ?? "—"}</p>
        {quote.status === "draft" && <button className="primary-button" type="button" onClick={() => act(`/quotations/${quotationId}/status`, { status: "sent" })}>Mark sent</button>}
        {quote.status === "sent" && (
          <>
            <button className="primary-button" type="button" onClick={() => act(`/quotations/${quotationId}/status`, { status: "accepted" })}>Mark accepted</button>
            <button className="secondary-button" type="button" onClick={() => act(`/quotations/${quotationId}/status`, { status: "rejected" })}>Mark rejected</button>
          </>
        )}
        {quote.status === "accepted" && <button className="primary-button" type="button" onClick={() => act(`/quotations/${quotationId}/convert`)}>Convert to sales order</button>}
      </section>
    </>
  );
}

export function SalesOrderDetailView({ orderId }: { orderId: string }) {
  const { data: order, error, reload } = useFetch<SalesOrder>(`/sales-orders/${orderId}`);
  const { data: warehouses } = useFetch<{ id: string; name: string }[]>("/warehouses");
  const { data: locations } = useFetch<{ id: string; warehouse_id: string; code: string }[]>("/locations");
  const [message, setMessage] = useState("");
  const [inv, setInv] = useState({ invoice_date: todayIso(), due_date: "", warehouse_id: "", location_id: "" });
  const [qty, setQty] = useState<Record<string, string>>({});

  async function act(path: string, body?: unknown) {
    setMessage("");
    try {
      await callCommerce(path, body === undefined ? { method: "POST" } : jsonBody("POST", body));
      reload();
      return true;
    } catch (err) {
      setMessage((err as Error).message);
      return false;
    }
  }

  async function createInvoice(e: FormEvent) {
    e.preventDefault();
    const lines = order?.lines
      .map((l) => ({ sales_order_line_id: l.id, quantity: qty[l.id] ?? "" }))
      .filter((l) => l.quantity !== "" && Number(l.quantity) > 0);
    if (!lines?.length) {
      setMessage("Enter a quantity for at least one line. Partial invoicing is allowed.");
      return;
    }
    const body = {
      sales_order_id: orderId,
      invoice_date: inv.invoice_date,
      due_date: inv.due_date || null,
      warehouse_id: inv.warehouse_id || null,
      location_id: inv.warehouse_id ? inv.location_id || null : null,
      lines,
    };
    if (await act("/sales-invoices", body)) setQty({});
  }

  if (error) return <Notice message={error} />;
  if (!order) return <p className="muted">Loading…</p>;
  const invoiceable = order.status === "confirmed" || order.status === "partially_invoiced";
  return (
    <>
      <Notice message={message} />
      <section className="settings-card">
        <div className="document-head"><h2>{order.number}</h2><StatusPill status={order.status} /></div>
        <ul className="document-list">
          {order.lines.map((l) => (
            <li key={l.id} className="document-card small">{l.description} · Ordered {l.quantity} · Invoiced {l.invoiced_quantity} · Open {l.open_quantity} · {l.line_total}</li>
          ))}
        </ul>
        <p className="small">Grand total {order.grand_total ?? "—"}</p>
        {order.status === "draft" && <button className="primary-button" type="button" onClick={() => act(`/sales-orders/${orderId}/status`, { status: "confirmed" })}>Confirm order</button>}
        {(order.status === "draft" || order.status === "confirmed") && <button className="secondary-button" type="button" onClick={() => act(`/sales-orders/${orderId}/status`, { status: "cancelled" })}>Cancel order</button>}
      </section>
      {invoiceable && (
        <form className="settings-card" onSubmit={createInvoice}>
          <h3>Create invoice (draft)</h3>
          <label>Invoice date<input type="date" value={inv.invoice_date} onChange={(e) => setInv({ ...inv, invoice_date: e.target.value })} /></label>
          <label>Due date<input type="date" value={inv.due_date} onChange={(e) => setInv({ ...inv, due_date: e.target.value })} /></label>
          <label>Ship from warehouse
            <select value={inv.warehouse_id} onChange={(e) => setInv({ ...inv, warehouse_id: e.target.value, location_id: "" })}>
              <option value="">Not a stock sale</option>
              {warehouses?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </label>
          {inv.warehouse_id && (
            <label>Location
              <select value={inv.location_id} onChange={(e) => setInv({ ...inv, location_id: e.target.value })}>
                <option value="">Choose…</option>
                {locations?.filter((l) => l.warehouse_id === inv.warehouse_id).map((l) => <option key={l.id} value={l.id}>{l.code}</option>)}
              </select>
            </label>
          )}
          {order.lines.map((l) => (
            <label key={l.id}>{l.description} (open {l.open_quantity})
              <input inputMode="decimal" value={qty[l.id] ?? ""} onChange={(e) => setQty({ ...qty, [l.id]: e.target.value })} />
            </label>
          ))}
          <button className="primary-button" type="submit">Save invoice</button>
        </form>
      )}
    </>
  );
}

export function SalesInvoiceDetailView({ invoiceId }: { invoiceId: string }) {
  const { data: invoice, error, reload } = useFetch<SalesInvoice>(`/sales-invoices/${invoiceId}`);
  const [message, setMessage] = useState("");
  const [payment, setPayment] = useState({ reference: "", paid_on: todayIso() });

  async function act(path: string, body?: unknown) {
    setMessage("");
    try {
      await callCommerce(path, body === undefined ? { method: "POST" } : jsonBody("POST", body));
      reload();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  if (error) return <Notice message={error} />;
  if (!invoice) return <p className="muted">Loading…</p>;
  return (
    <>
      <Notice message={message} />
      <section className="settings-card">
        <div className="document-head"><h2>{invoice.number}</h2><StatusPill status={invoice.status} /></div>
        <p className="muted small">Invoice date {invoice.invoice_date}{invoice.due_date ? ` · Due ${invoice.due_date}` : ""}</p>
        <ul className="document-list">
          {invoice.lines.map((l) => (
            <li key={l.id} className="document-card small">{l.description} · {l.quantity} · Tax {l.tax_amount} · Total {l.line_total}</li>
          ))}
        </ul>
        <p className="small">Grand total {invoice.grand_total ?? "—"} · Cost of goods {invoice.cost_of_goods_total ?? "—"}</p>
        {invoice.status === "draft" && <button className="primary-button" type="button" onClick={() => act(`/sales-invoices/${invoiceId}/post`)}>Post invoice to accounts</button>}
        {invoice.status === "posted" && <button className="secondary-button" type="button" onClick={() => act(`/sales-invoices/${invoiceId}/void`)}>Void (reversing entry)</button>}
      </section>
      {invoice.status === "posted" && (
        <form className="settings-card" onSubmit={(e) => { e.preventDefault(); act(`/sales-invoices/${invoiceId}/payment-reference`, payment); }}>
          <h3>Payment reference</h3>
          <p className="hint">Records a reference only. Phase 3 does not collect payments.</p>
          <label>Reference<input required maxLength={80} value={payment.reference} onChange={(e) => setPayment({ ...payment, reference: e.target.value })} /></label>
          <label>Received on<input required type="date" value={payment.paid_on} onChange={(e) => setPayment({ ...payment, paid_on: e.target.value })} /></label>
          <button className="primary-button" type="submit">Save reference</button>
        </form>
      )}
    </>
  );
}

// ---------------------------------------------------------------- document intake

export function IntakeView() {
  const { data: documents } = useFetch<{ id: string; display_name: string; kind: string; status: string }[]>("/documents");
  const { data: jobs, reload, error } = useFetch<Job[]>("/document-jobs");
  const [message, setMessage] = useState("");
  const jobDocs = new Set(jobs?.map((j) => j.document_id));

  async function start(documentId: string) {
    setMessage("");
    try {
      const job = await callCommerce<Job>("/document-jobs", jsonBody("POST", { document_id: documentId }));
      await callCommerce(`/document-jobs/${job.id}/run`, { method: "POST" });
      reload();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  return (
    <>
      <Notice message={message || error} />
      <p className="muted small">Upload a bill, invoice or receipt in <Link href="/documents">Documents</Link>. Then start processing it here. Automatic reading needs a document AI provider; until one is connected, you enter the details on the review screen.</p>
      <h2>Documents</h2>
      <ul className="document-list">
        {documents?.map((d) => (
          <li key={d.id} className="document-card">
            <div className="document-head"><div><h3>{d.display_name}</h3><p className="muted small">{d.kind}</p></div></div>
            {d.status === "active" && !jobDocs.has(d.id) && <button className="primary-button" type="button" onClick={() => start(d.id)}>Start processing</button>}
          </li>
        ))}
      </ul>
      <h2>Processing</h2>
      <ul className="document-list">
        {jobs?.map((j) => (
          <li key={j.id} className="document-card">
            <Link href={`/commerce/intake/${j.id}`}>
              <div className="document-head"><div><h3>{j.classification ? j.classification.replaceAll("_", " ") : "Unclassified"}</h3><p className="muted small">Provider {j.provider_name}</p></div><StatusPill status={j.status} /></div>
            </Link>
          </li>
        ))}
      </ul>
      {jobs?.length === 0 && <p className="muted">Nothing is being processed.</p>}
    </>
  );
}

// Warnings the owner may acknowledge. Every other issue blocks approval. The server enforces the same rule.
const ACKNOWLEDGEABLE = new Set(["low_confidence", "ambiguous_classification", "duplicate_file", "duplicate_reference"]);
const DUPLICATE = new Set(["duplicate_file", "duplicate_reference"]);
const FINANCE_LINKS: Record<string, { href: string; label: string }> = {
  debt: { href: "/finance/debts", label: "Debts" },
  emi: { href: "/finance/emi", label: "EMI" },
  interest: { href: "/finance/interest", label: "Interest" },
  salary: { href: "/finance/salaries", label: "Salaries" },
  recurring_payment: { href: "/finance/recurring", label: "Recurring payments" },
};

export function IntakeDetailView({ jobId }: { jobId: string }) {
  const { data: job, error, reload } = useFetch<Job>(`/document-jobs/${jobId}`);
  const [message, setMessage] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [ack, setAck] = useState(false);
  const [approved, setApproved] = useState<{ type: string; id: string } | null>(null);

  async function act<T = unknown>(path: string, body?: unknown, method: "POST" | "PATCH" = "POST"): Promise<T | null> {
    setMessage("");
    try {
      const result = await callCommerce<T>(path, jsonBody(method, body ?? {}));
      reload();
      return result;
    } catch (err) {
      setMessage((err as Error).message);
      return null;
    }
  }

  if (error) return <Notice message={error} />;
  if (!job) return <p className="muted">Loading…</p>;
  const payload = job.current_payload ?? {};
  const issues = job.issues ?? [];
  const duplicates = issues.filter((i) => DUPLICATE.has(i.code));
  const needsAck = issues.some((i) => ACKNOWLEDGEABLE.has(i.code));
  const blocking = issues.filter((i) => !ACKNOWLEDGEABLE.has(i.code));
  const editable = job.status === "needs_review" || job.status === "ready";
  const canDecide = job.status === "needs_review" || job.status === "ready";
  const approveDisabled = blocking.length > 0 || (needsAck && !ack);
  const financeLink = approved ? FINANCE_LINKS[approved.type] : undefined;

  return (
    <>
      <Notice message={message} />
      <section className="settings-card">
        <div className="document-head"><h2>{job.classification ? job.classification.replaceAll("_", " ") : "Unclassified"}</h2><StatusPill status={job.status} /></div>
        <p className="small">Classification confidence {job.classification_confidence ?? "—"} · Provider {job.provider_name}{job.provider_available ? "" : " (not connected)"}</p>
        {duplicates.length > 0 && (
          <div className="settings-card" role="alert">
            <strong>Possible duplicate.</strong>
            <p className="small">Check this before approving. Approving creates a draft, so a duplicate would be recorded twice.</p>
            <ul className="document-list">{duplicates.map((i, n) => <li key={n} className="small">{i.message}</li>)}</ul>
          </div>
        )}
        {issues.length > 0 && (
          <ul className="document-list">
            {issues.map((issue, i) => (
              <li key={i} className="document-card small">
                {ACKNOWLEDGEABLE.has(issue.code) ? "Warning" : "Blocking"}: {issue.message}{issue.field ? ` (${issue.field.replaceAll("_", " ")})` : ""}
              </li>
            ))}
          </ul>
        )}
        <h3>Extracted details</h3>
        <dl className="small">
          {Object.entries(payload).filter(([k]) => k !== "lines").map(([k, v]) => (
            <div key={k}><dt className="muted">{k.replaceAll("_", " ")}</dt><dd>{String(v ?? "—")} {job.field_confidence?.[k] ? `(confidence ${job.field_confidence[k]})` : ""}</dd></div>
          ))}
        </dl>
      </section>

      {editable && (
        <form className="settings-card" onSubmit={(e) => { e.preventDefault(); act(`/document-jobs/${jobId}`, Object.fromEntries(Object.entries(fields).filter(([, v]) => v !== "")), "PATCH"); setFields({}); }}>
          <h3>Correct extracted details</h3>
          <p className="hint">Manual correction is always possible. Corrected values are re-validated before approval.</p>
          {["party_name", "reference_number", "document_date", "due_date", "grand_total", "notes"].map((k) => (
            <label key={k}>{k.replaceAll("_", " ")}
              <input value={fields[k] ?? String(payload[k] ?? "")} onChange={(e) => setFields({ ...fields, [k]: e.target.value })} />
            </label>
          ))}
          <button className="secondary-button" type="submit">Save corrections</button>
        </form>
      )}

      {canDecide ? (
        <section className="settings-card">
          <h3>Approve or reject</h3>
          <p className="hint">Approval creates a draft only. Stock and accounts change only when you post the draft. Finance documents create a finance record directly and are shown in Finance.</p>
          {blocking.length > 0 && <p className="form-error small">Correct the blocking issues above before approving.</p>}
          {needsAck && (
            <label className="upload-form"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} /> I have reviewed the warnings above{duplicates.length > 0 ? ", including the possible duplicate" : ""}</label>
          )}
          <button className="primary-button" type="button" disabled={approveDisabled} onClick={async () => {
            const result = await act<{ draft_type: string; draft_id: string }>(`/document-jobs/${jobId}/approve`, { acknowledge_warnings: ack });
            if (result) setApproved({ type: result.draft_type, id: result.draft_id });
          }}>Approve</button>
          <button className="secondary-button" type="button" onClick={() => act(`/document-jobs/${jobId}/reject`, { reason: "Rejected on review" })}>Reject</button>
        </section>
      ) : null}

      {approved && (
        <section className="settings-card">
          <p className="small">Approved as {approved.type.replaceAll("_", " ")}.</p>
          {financeLink ? (
            <Link href={financeLink.href}>Open in Finance: {financeLink.label}</Link>
          ) : (
            <p className="hint">Review and post the draft from its screen.</p>
          )}
        </section>
      )}
      {!approved && job.status === "approved" && (
        <p className="muted">Approved{job.approved_document_type ? ` as a ${job.approved_document_type.replaceAll("_", " ")}` : ""}.</p>
      )}
    </>
  );
}
