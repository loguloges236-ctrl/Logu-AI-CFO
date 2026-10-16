"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { Notice } from "./parts";
import { Balance, Category, Dashboard, Location, Movement, Page, Product, Warehouse, callCommerce, jsonBody } from "./shared";
// Each view loads from the server after every change. The server is the only source of stock and totals.

function useLoad<T>(path: string, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let active = true;
    callCommerce<T>(path)
      .then((value) => {
        if (active) {
          setData(value);
          setError("");
        }
      })
      .catch((err: Error) => active && setError(err.message));
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, version, ...deps]);
  return { data, error, reload: () => setVersion((v) => v + 1) };
}

export function DashboardView() {
  const { data, error } = useLoad<Dashboard>("/dashboard");
  const tiles: [string, string | number | undefined, string][] = [
    ["Open purchase orders", data?.open_purchase_orders, "/commerce/purchases"],
    ["Bills to post", data?.bills_to_post, "/commerce/purchases"],
    ["Invoices to post", data?.invoices_to_post, "/commerce/sales"],
    ["Documents to review", data?.documents_to_review, "/commerce/intake"],
    ["Stock lines at zero", data?.zero_stock_lines, "/commerce/stock"],
  ];
  return (
    <>
      <Notice message={error} />
      <div className="card-grid">
        {tiles.map(([label, value, href]) => (
          <Link key={label} href={href} className="module-card">
            <h3>{label}</h3>
            <p className="lead">{value ?? "—"}</p>
          </Link>
        ))}
        <div className="module-card">
          <h3>Stock value (weighted average)</h3>
          <p className="lead">{data?.stock_value ?? "—"}</p>
        </div>
      </div>
      <section className="upload-panel">
        <p className="small">
          Start from a document: <Link href="/commerce/intake">Document intake →</Link>
        </p>
      </section>
    </>
  );
}

export function ProductsView() {
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const { data, error, reload } = useLoad<Page<Product>>(`/products?limit=200${query ? `&search=${encodeURIComponent(query)}` : ""}`, [query]);
  const { data: categories } = useLoad<Category[]>("/categories");
  const [message, setMessage] = useState("");
  const [form, setForm] = useState({ sku: "", name: "", unit_of_measure: "pcs", category_id: "", purchase_price: "", selling_price: "", tax_rate: "", hsn_sac: "", barcode: "" });

  async function create(event: FormEvent) {
    event.preventDefault();
    setMessage("");
    const body = Object.fromEntries(Object.entries({ ...form, is_stock_item: true }).map(([k, v]) => [k, v === "" ? null : v]));
    try {
      await callCommerce<Product>("/products", jsonBody("POST", body));
      setForm({ sku: "", name: "", unit_of_measure: "pcs", category_id: "", purchase_price: "", selling_price: "", tax_rate: "", hsn_sac: "", barcode: "" });
      reload();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  async function toggle(product: Product) {
    try {
      await callCommerce<Product>(`/products/${product.id}`, jsonBody("PATCH", { is_active: !product.is_active }));
      reload();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  return (
    <>
      <Notice message={message || error} />
      <section className="upload-panel">
        <form className="upload-form" onSubmit={(e) => { e.preventDefault(); setQuery(search.trim()); }}>
          <input placeholder="Search SKU, barcode or name" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={80} />
          <button className="secondary-button" type="submit">Search</button>
        </form>
      </section>
      <ul className="document-list">
        {data?.items.map((p) => (
          <li key={p.id} className="document-card">
            <div className="document-head">
              <div>
                <h3>{p.name}</h3>
                <p className="muted small">SKU {p.sku}{p.barcode ? ` · ${p.barcode}` : ""} · {p.unit_of_measure}{p.hsn_sac ? ` · HSN/SAC ${p.hsn_sac}` : ""}</p>
                <p className="small">Buy {p.purchase_price ?? "—"} · Sell {p.selling_price ?? "—"} · Tax {p.tax_rate ?? "—"}%</p>
              </div>
              <span className={`status-pill ${p.is_active ? "status-active" : "status-revoked"}`}>{p.is_active ? "Active" : "Inactive"}</span>
            </div>
            <button className="secondary-button" type="button" onClick={() => toggle(p)}>
              {p.is_active ? "Deactivate" : "Reactivate"}
            </button>
          </li>
        ))}
      </ul>
      {data?.items.length === 0 && <p className="muted">No products match.</p>}
      <form className="settings-card" onSubmit={create}>
        <h2>New product</h2>
        <label>SKU<input required maxLength={60} value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} /></label>
        <label>Name<input required maxLength={160} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label>Unit
          <select value={form.unit_of_measure} onChange={(e) => setForm({ ...form, unit_of_measure: e.target.value })}>
            {["pcs", "kg", "g", "l", "ml", "m", "box", "pack", "pair", "set", "hr"].map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
        </label>
        <label>Category
          <select value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value })}>
            <option value="">None</option>
            {categories?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label>Purchase price<input inputMode="decimal" pattern="^\d{1,16}(\.\d{1,2})?$" value={form.purchase_price} onChange={(e) => setForm({ ...form, purchase_price: e.target.value })} /></label>
        <label>Selling price<input inputMode="decimal" pattern="^\d{1,16}(\.\d{1,2})?$" value={form.selling_price} onChange={(e) => setForm({ ...form, selling_price: e.target.value })} /></label>
        <label>Tax rate %<input inputMode="decimal" value={form.tax_rate} onChange={(e) => setForm({ ...form, tax_rate: e.target.value })} /></label>
        <label>HSN/SAC<input inputMode="numeric" value={form.hsn_sac} onChange={(e) => setForm({ ...form, hsn_sac: e.target.value })} /></label>
        <label>Barcode<input value={form.barcode} onChange={(e) => setForm({ ...form, barcode: e.target.value })} /></label>
        <button className="primary-button" type="submit">Create product</button>
      </form>
    </>
  );
}

export function WarehousesView() {
  const { data: warehouses, error, reload } = useLoad<Warehouse[]>("/warehouses");
  const { data: locations, reload: reloadLocations } = useLoad<Location[]>("/locations");
  const [message, setMessage] = useState("");
  const [wh, setWh] = useState({ code: "", name: "", allow_negative_stock: false });
  const [loc, setLoc] = useState({ warehouse_id: "", code: "", name: "" });

  async function submit(path: string, body: unknown, onDone: () => void) {
    setMessage("");
    try {
      await callCommerce(path, jsonBody("POST", body));
      onDone();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  return (
    <>
      <Notice message={message || error} />
      <ul className="document-list">
        {warehouses?.map((w) => (
          <li key={w.id} className="document-card">
            <h3>{w.name}</h3>
            <p className="muted small">{w.code} · {w.allow_negative_stock ? "Negative stock allowed" : "Negative stock refused"} · {w.is_active ? "Active" : "Inactive"}</p>
            <ul>
              {locations?.filter((l) => l.warehouse_id === w.id).map((l) => (
                <li key={l.id} className="small">{l.code} · {l.name}</li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <form className="settings-card" onSubmit={(e) => { e.preventDefault(); submit("/warehouses", { ...wh, code: wh.code.trim().toUpperCase() }, () => { setWh({ code: "", name: "", allow_negative_stock: false }); reload(); }); }}>
        <h2>New warehouse / store</h2>
        <label>Code<input required maxLength={20} value={wh.code} onChange={(e) => setWh({ ...wh, code: e.target.value })} /></label>
        <label>Name<input required maxLength={120} value={wh.name} onChange={(e) => setWh({ ...wh, name: e.target.value })} /></label>
        <label className="upload-form"><input type="checkbox" checked={wh.allow_negative_stock} onChange={(e) => setWh({ ...wh, allow_negative_stock: e.target.checked })} /> Allow negative stock (explicit, off by default)</label>
        <button className="primary-button" type="submit">Create warehouse</button>
      </form>
      <form className="settings-card" onSubmit={(e) => { e.preventDefault(); submit("/locations", loc, () => { setLoc({ warehouse_id: loc.warehouse_id, code: "", name: "" }); reloadLocations(); }); }}>
        <h2>New stock location</h2>
        <label>Warehouse
          <select required value={loc.warehouse_id} onChange={(e) => setLoc({ ...loc, warehouse_id: e.target.value })}>
            <option value="">Choose…</option>
            {warehouses?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>
        <label>Code<input required maxLength={20} value={loc.code} onChange={(e) => setLoc({ ...loc, code: e.target.value })} /></label>
        <label>Name<input required maxLength={120} value={loc.name} onChange={(e) => setLoc({ ...loc, name: e.target.value })} /></label>
        <button className="primary-button" type="submit">Create location</button>
      </form>
    </>
  );
}

type StockAction = "receive" | "issue" | "damage" | "adjust";

export function StockView() {
  const [warehouseId, setWarehouseId] = useState("");
  const { data: balances, error, reload } = useLoad<Balance[]>(`/stock/balances${warehouseId ? `?warehouse_id=${warehouseId}` : ""}`, [warehouseId]);
  const { data: warehouses } = useLoad<Warehouse[]>("/warehouses");
  const { data: locations } = useLoad<Location[]>("/locations");
  const { data: products } = useLoad<Page<Product>>("/products?active_only=true&limit=200");
  const [action, setAction] = useState<StockAction>("receive");
  const [message, setMessage] = useState("");
  const [form, setForm] = useState({ product_id: "", location_id: "", quantity: "", unit_cost: "", reason: "", direction: "increase" });

  async function run(event: FormEvent) {
    event.preventDefault();
    setMessage("");
    const { direction, unit_cost, ...rest } = form;
    const body: Record<string, unknown> = { ...rest, reason: rest.reason || null };
    let path = `/stock/${action === "receive" ? "receive" : action === "issue" ? "issue" : action === "damage" ? "damage" : "adjust"}`;
    if (action === "receive") body.unit_cost = unit_cost;
    if (action === "adjust") Object.assign(body, { direction, unit_cost: unit_cost || null, reason: rest.reason || "Adjustment" });
    try {
      await callCommerce(path, jsonBody("POST", body));
      setForm({ ...form, quantity: "", unit_cost: "", reason: "" });
      setMessage("Recorded. Stock updated from the movement ledger.");
      reload();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  return (
    <>
      <Notice message={message || error} />
      <section className="upload-panel">
        <label className="upload-form">Warehouse
          <select value={warehouseId} onChange={(e) => setWarehouseId(e.target.value)}>
            <option value="">All</option>
            {warehouses?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>
        <p className="small"><Link href="/commerce/stock/movements">Movement ledger →</Link></p>
      </section>
      <ul className="document-list">
        {balances?.map((b) => (
          <li key={`${b.product_id}-${b.location_id}`} className="document-card">
            <div className="document-head">
              <div>
                <h3>{b.name}</h3>
                <p className="muted small">{b.sku}</p>
              </div>
              <strong>{b.quantity}</strong>
            </div>
            <p className="small">Avg cost {b.average_cost ?? "—"} · Value {b.value ?? "—"}</p>
          </li>
        ))}
      </ul>
      {balances?.length === 0 && <p className="muted">No stock recorded yet.</p>}
      <form className="settings-card" onSubmit={run}>
        <h2>Record stock</h2>
        <div className="row-item" role="group" aria-label="Action">
          {(["receive", "issue", "damage", "adjust"] as StockAction[]).map((a) => (
            <button key={a} type="button" className={a === action ? "primary-button" : "secondary-button"} onClick={() => setAction(a)}>{a}</button>
          ))}
        </div>
        <label>Product
          <select required value={form.product_id} onChange={(e) => setForm({ ...form, product_id: e.target.value })}>
            <option value="">Choose…</option>
            {products?.items.map((p) => <option key={p.id} value={p.id}>{p.sku} · {p.name}</option>)}
          </select>
        </label>
        <label>Location
          <select required value={form.location_id} onChange={(e) => setForm({ ...form, location_id: e.target.value })}>
            <option value="">Choose…</option>
            {locations?.filter((l) => l.is_active).map((l) => <option key={l.id} value={l.id}>{l.code} · {l.name}</option>)}
          </select>
        </label>
        {action === "adjust" && (
          <label>Direction
            <select value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}>
              <option value="increase">Increase</option>
              <option value="decrease">Decrease</option>
            </select>
          </label>
        )}
        <label>Quantity<input required inputMode="decimal" pattern="^\d{1,15}(\.\d{1,3})?$" value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></label>
        {(action === "receive" || action === "adjust") && (
          <label>Unit cost<input inputMode="decimal" pattern="^\d{1,14}(\.\d{1,4})?$" required={action === "receive"} value={form.unit_cost} onChange={(e) => setForm({ ...form, unit_cost: e.target.value })} /></label>
        )}
        <label>Reason<input maxLength={255} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></label>
        <button className="primary-button" type="submit">Record {action}</button>
        <p className="hint">Stock never changes by editing a quantity. Each action writes a traceable movement.</p>
      </form>
    </>
  );
}

export function MovementsView() {
  const [type, setType] = useState("");
  const { data, error } = useLoad<Page<Movement>>(`/stock/movements?limit=100${type ? `&movement_type=${encodeURIComponent(type)}` : ""}`, [type]);
  return (
    <>
      <Notice message={error} />
      <section className="upload-panel">
        <label className="upload-form">Type
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">All</option>
            {["stock_in", "grn_in", "stock_out", "sale_out", "damage_out", "return_in", "return_out", "transfer_in", "transfer_out", "adjustment_in", "adjustment_out"].map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
      </section>
      <ul className="document-list">
        {data?.items.map((m) => (
          <li key={m.id} className="document-card">
            <div className="document-head">
              <div>
                <h3>#{m.seq} · {m.sku}</h3>
                <p className="muted small">{m.movement_type} · {new Date(m.created_at).toLocaleString()}</p>
              </div>
              <strong>{m.quantity_delta.startsWith("-") ? "" : "+"}{m.quantity_delta}</strong>
            </div>
            <p className="small">Balance after {m.balance_after}{m.unit_cost ? ` · Unit cost ${m.unit_cost}` : ""}</p>
            {m.source_type && <p className="muted small">Source {m.source_type}{m.reason ? ` · ${m.reason}` : ""}</p>}
          </li>
        ))}
      </ul>
      {data?.items.length === 0 && <p className="muted">No movements.</p>}
    </>
  );
}

export function MappingsView() {
  const { data: mappings, error, reload } = useLoad<{ role: string; account_id: string | null; updated_at: string }[]>("/accounting-roles");
  const [message, setMessage] = useState("");
  const [accountId, setAccountId] = useState<Record<string, string>>({});
  const roles = ["accounts_payable", "inventory", "input_tax", "accounts_receivable", "sales_revenue", "output_tax", "cost_of_goods_sold", "default_expense"];

  async function save(role: string) {
    setMessage("");
    try {
      await callCommerce(`/accounting-roles/${role}`, jsonBody("PUT", { account_id: accountId[role] }));
      reload();
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  return (
    <>
      <Notice message={message || error} />
      <p className="muted small">Map each business role to an account from the Phase 2 chart. Accounting entries are created only through the accounting service.</p>
      <ul className="document-list">
        {roles.map((role) => {
          const current = mappings?.find((m) => m.role === role);
          return (
            <li key={role} className="document-card">
              <h3>{role.replaceAll("_", " ")}</h3>
              <p className="muted small">{current?.account_id ? `Mapped to ${current.account_id}` : "Not mapped"}</p>
              <label>Account ID
                <input value={accountId[role] ?? ""} placeholder={current?.account_id ?? "Paste account ID from the chart"} onChange={(e) => setAccountId({ ...accountId, [role]: e.target.value })} />
              </label>
              <button className="secondary-button" type="button" disabled={!accountId[role]} onClick={() => save(role)}>Save mapping</button>
            </li>
          );
        })}
      </ul>
    </>
  );
}
