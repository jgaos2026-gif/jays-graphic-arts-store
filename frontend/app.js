/* Jay's Graphic Arts — storefront app (v1). Talks to /api/*. Tokens in
   localStorage in v1; a packaged phone app must move them to secure storage. */
const $ = (s) => document.querySelector(s);
const money = (c) => "$" + (c / 100).toFixed(2);
let CATALOG = null;
let clientToken = localStorage.getItem("jga_client_token") || "";
let ownerToken = localStorage.getItem("jga_owner_token") || "";

async function api(path, body, token) {
  const r = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: Object.assign({"Content-Type": "application/json"},
      token ? {"Authorization": "Bearer " + token} : {}),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || ("request failed: " + r.status));
  return data;
}

function show(view) {
  ["store", "orders", "account", "owner"].forEach((v) => {
    document.getElementById("view-" + v).classList.toggle("hidden", v !== view);
    document.querySelector('nav button[data-view="' + v + '"]').classList.toggle("active", v === view);
  });
  if (view === "orders") renderOrders();
  if (view === "account") renderAccount();
  if (view === "owner") renderOwner();
}
document.querySelectorAll("nav button").forEach((b) =>
  b.addEventListener("click", () => show(b.dataset.view)));

async function renderStore() {
  CATALOG = await api("/api/public");
  const el = $("#view-store");
  const byFamily = {};
  CATALOG.services.forEach((s) => {
    (byFamily[s.family] = byFamily[s.family] || []).push(s);
  });
  let h = `<div class="card"><h2>Design that gives a business a face, a voice, and something worth remembering.</h2>
    <p class="muted">Brand identity &middot; Promotion + events &middot; Social + digital &middot; Print + business essentials &middot; Custom creative</p>
    <p><b>How it works:</b> ${CATALOG.process.join(" &rarr; ")} &mdash; proof approval before anything is final, clean files only after final payment + approval.</p>
    <p><b>Terms:</b> ${CATALOG.terms}</p></div>`;

  h += `<div class="card"><h2>Packages</h2>`;
  CATALOG.packages.forEach((p) => {
    h += `<div class="pkg"><h3>${p.name}</h3><div class="price">${money(p.price_cents)}${p.billing === "monthly" ? " / month" : ""}</div>
      <p class="muted">${p.blurb}${p.revisions ? " " + p.revisions + " revision rounds." : ""}</p>
      <p class="muted">Deposit 35%: <b>${money(Math.round(p.price_cents * 0.35))}</b> to start &middot; balance ${money(p.price_cents - Math.round(p.price_cents * 0.35))} before final clean files.</p>`;
    if (p.full_link) {
      h += `<a class="btn" href="${p.full_link}" target="_blank" rel="noopener">Pay in full — Stripe (test)</a>
            <a class="btn ghost" href="${p.deposit_link}" target="_blank" rel="noopener">Pay 35% deposit — Stripe (test)</a>
            <a class="btn ghost" href="${p.balance_link}" target="_blank" rel="noopener">Pay 65% balance — Stripe (test)</a>`;
    } else {
      h += `<p class="muted">${p.stripe_note}</p>`;
    }
    h += `<button class="btn ghost" onclick="orderPackage('${p.id}')">Order this package</button></div>`;
  });
  h += `</div>`;

  h += `<div class="card"><h2>Services</h2>`;
  Object.keys(byFamily).forEach((fam) => {
    h += `<h3>${fam}</h3>`;
    byFamily[fam].forEach((s) => {
      h += `<p><b>${s.name}</b> ${s.from_cents ? "&mdash; from " + money(s.from_cents) : "&mdash; quoted up front"}<br><span class="muted">${s.blurb}</span>
        <button class="btn ghost" onclick="orderService('${s.id}')">Order</button></p>`;
    });
  });
  h += `</div>`;

  h += `<div class="card"><h2>Print, by Vistaprint</h2>
    <p>Business cards, flyers, posters, event tickets, postcards, brochures — printed by Vistaprint and delivered to you.</p>
    <p class="muted"><b>Pricing:</b> ${CATALOG.vistaprint.formula}. ${CATALOG.vistaprint.note}</p>
    <label>Vistaprint cost (dollars, from the Vistaprint quote)</label>
    <input id="vp-cost" type="number" step="0.01" min="0" placeholder="e.g. 50.00">
    <label>Delivery (dollars, Vistaprint shipping)</label>
    <input id="vp-delivery" type="number" step="0.01" min="0" placeholder="e.g. 8.00">
    <button class="btn" onclick="vpQuote()">Calculate my price</button>
    <div id="vp-result"></div></div>`;

  h += `<div class="card"><h2>Place an order</h2><div id="order-form">
    <p class="muted">Sign in as a client first (Account tab), then pick a package or service above — or order here.</p>
    <label>Package</label><select id="ord-package"><option value="">— none —</option>${CATALOG.packages.map((p) => `<option value="${p.id}">${p.name} — ${money(p.price_cents)}${p.billing === "monthly" ? "/mo" : ""}</option>`).join("")}</select>
    <label>Service</label><select id="ord-service"><option value="">— none —</option>${CATALOG.services.map((s) => `<option value="${s.id}">${s.name}</option>`).join("")}</select>
    <label>Print product</label><select id="ord-print"><option value="">— none —</option>${CATALOG.print_products.map((p) => `<option value="${p.id}">${p.name}</option>`).join("")}</select>
    <label>Your brief — tell us what you need</label>
    <textarea id="ord-brief" rows="3" placeholder="e.g. A logo for my barbershop, bold and clean, black and gold."></textarea>
    <button class="btn" onclick="placeOrder()">Place order</button>
    <div id="ord-result"></div></div></div>`;
  el.innerHTML = h;
}

async function vpQuote() {
  const cost = Math.round(parseFloat($("#vp-cost").value || "0") * 100);
  const del = Math.round(parseFloat($("#vp-delivery").value || "0") * 100);
  try {
    const q = await api("/api/vistaprint/quote", {vistaprint_cost_cents: cost, delivery_cents: del});
    $("#vp-result").innerHTML = `<table>
      <tr><td>Vistaprint cost</td><td>${money(q.vistaprint_cost_cents)}</td></tr>
      <tr><td>Product (+20%)</td><td>${money(q.product_cents)}</td></tr>
      <tr><td>Delivery (at cost)</td><td>${money(q.delivery_cents)}</td></tr>
      <tr><td>Subtotal</td><td>${money(q.subtotal_cents)}</td></tr>
      <tr><td>Final markup (+5%)</td><td>${money(q.final_markup_cents)}</td></tr>
      <tr><td><b>Your price</b></td><td><b>${money(q.total_cents)}</b></td></tr></table>`;
  } catch (e) { $("#vp-result").innerHTML = `<p style="color:#e06c5a">${e.message}</p>`; }
}

function orderPackage(id) {
  if (!clientToken) { show("account"); return; }
  $("#ord-package").value = id; $("#ord-service").value = ""; $("#ord-print").value = "";
  document.getElementById("order-form").scrollIntoView();
}
function orderService(id) {
  if (!clientToken) { show("account"); return; }
  $("#ord-service").value = id; $("#ord-package").value = ""; $("#ord-print").value = "";
  document.getElementById("order-form").scrollIntoView();
}

async function placeOrder() {
  if (!clientToken) { $("#ord-result").innerHTML = "<p>Sign in as a client first (Account tab).</p>"; return; }
  const body = {
    package_id: $("#ord-package").value || undefined,
    service_id: $("#ord-service").value || undefined,
    print_product_id: $("#ord-print").value || undefined,
    brief: $("#ord-brief").value,
  };
  try {
    const o = await api("/api/orders", body, clientToken);
    const pricing = o.quote_pending
      ? "Your order is in. The owner will verify the supplier quote and send your price before work starts."
      : `${money(o.price_cents)}. Deposit 35%: ${money(o.deposit_cents)} to start.`;
    $("#ord-result").innerHTML = `<p>Order <b>${o.order_id}</b> placed — ${o.title}. ${pricing} We will send you a proof, and clean files release only after approval and cleared final payment.</p>`;
  } catch (e) { $("#ord-result").innerHTML = `<p style="color:#e06c5a">${e.message}</p>`; }
}

function renderAccount() {
  const el = $("#view-account");
  el.innerHTML = `<div class="card"><h2>Client account</h2>
    ${clientToken ? `<p>Signed in as a client. <button class="btn ghost" onclick="clientLogout()">Sign out</button></p>` : `
    <h3>Create an account</h3>
    <label>Name</label><input id="su-name" placeholder="Your name">
    <label>Email</label><input id="su-email" placeholder="you@example.com">
    <label>Password (8+ characters)</label><input id="su-pass" type="password">
    <button class="btn" onclick="clientSignup()">Create account</button>
    <h3>Sign in</h3>
    <label>Email</label><input id="si-email" placeholder="you@example.com">
    <label>Password</label><input id="si-pass" type="password">
    <button class="btn" onclick="clientSignin()">Sign in</button>
    <div id="acct-msg"></div>`}</div>`;
}
async function clientSignup() {
  try {
    await api("/api/clients", {name: $("#su-name").value, email: $("#su-email").value, password: $("#su-pass").value});
    $("#acct-msg").innerHTML = "<p>Account created — now sign in below.</p>";
  } catch (e) { $("#acct-msg").innerHTML = `<p style="color:#e06c5a">${e.message}</p>`; }
}
async function clientSignin() {
  try {
    const r = await api("/api/login-client", {email: $("#si-email").value, password: $("#si-pass").value});
    clientToken = r.token; localStorage.setItem("jga_client_token", clientToken);
    renderAccount(); show("orders");
  } catch (e) { $("#acct-msg").innerHTML = `<p style="color:#e06c5a">${e.message}</p>`; }
}
function clientLogout() {
  if (clientToken) api("/api/logout", {}, clientToken).catch(() => {});
  clientToken = ""; localStorage.removeItem("jga_client_token"); renderAccount();
}

async function renderOrders() {
  const el = $("#view-orders");
  if (!clientToken) { el.innerHTML = `<div class="card"><p>Sign in as a client first (Account tab) to see your orders. You only ever see your own orders.</p></div>`; return; }
  try {
    const orders = await api("/api/orders", undefined, clientToken);
    if (!orders.length) { el.innerHTML = `<div class="card"><h2>My Orders</h2><p>No orders yet. Pick a package or service on the Store tab.</p></div>`; return; }
    el.innerHTML = `<div class="card"><h2>My Orders</h2>` + orders.map((o) => `
      <div class="pkg"><h3>${o.title}</h3>
      <p><span class="tag">${o.order_id}</span> <span class="state ${o.state}">${o.state}</span> &middot; proof: <b>${o.proof}</b></p>
      <p class="muted">${o.brief || ""}</p>
      <p>Price ${money(o.price_cents)} &middot; deposit ${money(o.deposit_cents)} ${o.deposit_paid ? "(paid)" : "(due)"} &middot; balance ${money(o.balance_cents)} ${o.balance_paid ? "(paid)" : "(due)"}</p>
      ${o.vistaprint ? `<p class="muted">Vistaprint cost ${money(o.vistaprint.vistaprint_cost_cents)} +20% = ${money(o.vistaprint.product_cents)}, delivery ${money(o.vistaprint.delivery_cents)}, +5% = ${money(o.vistaprint.final_markup_cents)}, total ${money(o.vistaprint.total_cents)}</p>` : ""}
      ${o.proof === "sent" ? `<button class="btn" onclick="approveProof('${o.order_id}')">Approve this proof</button>` : ""}
      ${o.clean_files_released ? `<p><b>Clean files released — thank you!</b></p>` : ""}
      </div>`).join("") + `</div>`;
  } catch (e) { el.innerHTML = `<div class="card"><p style="color:#e06c5a">${e.message}</p></div>`; }
}
async function approveProof(oid) {
  try { await api("/api/orders/" + oid + "/approve-proof", {}, clientToken); renderOrders(); }
  catch (e) { alert(e.message); }
}

function renderOwner() {
  const el = $("#view-owner");
  el.innerHTML = `<div class="card"><h2>Owner</h2>
    ${ownerToken ? `<p>Signed in as owner. <button class="btn ghost" onclick="ownerLogout()">Sign out</button></p><div id="owner-dash"></div>` : `
    <h3>First time? Set the owner password</h3>
    <label>Owner password (8+ characters)</label><input id="ow-setup" type="password">
    <button class="btn" onclick="ownerSetup()">Set owner password</button>
    <h3>Sign in as owner</h3>
    <label>Owner password</label><input id="ow-pass" type="password">
    <button class="btn" onclick="ownerSignin()">Sign in</button>
    <div id="owner-msg"></div>`}`;
  if (ownerToken) renderOwnerDash();
}
async function ownerSetup() {
  try { await api("/api/setup-owner", {password: $("#ow-setup").value, setup_token: $("#ow-setup-token").value}); $("#owner-msg").innerHTML = "<p>Owner set — now sign in.</p>"; }
  catch (e) { $("#owner-msg").innerHTML = `<p style="color:#e06c5a">${e.message}</p>`; }
}
async function ownerSignin() {
  try {
    const r = await api("/api/login-owner", {password: $("#ow-pass").value});
    ownerToken = r.token; localStorage.setItem("jga_owner_token", ownerToken); renderOwner();
  } catch (e) { $("#owner-msg").innerHTML = `<p style="color:#e06c5a">${e.message}</p>`; }
}
function ownerLogout() {
  if (ownerToken) api("/api/logout", {}, ownerToken).catch(() => {});
  ownerToken = ""; localStorage.removeItem("jga_owner_token"); renderOwner();
}
async function renderOwnerDash() {
  const el = $("#owner-dash");
  try {
    const orders = await api("/api/orders", undefined, ownerToken);
    el.innerHTML = `<h3>All orders (${orders.length})</h3>` + (orders.length ? orders.map((o) => `
      <div class="pkg"><h3>${o.title}</h3>
      <p><span class="tag">${o.order_id}</span> <span class="state ${o.state}">${o.state}</span> &middot; proof: <b>${o.proof}</b> &middot; client ${o.client_id}</p>
      <p class="muted">${o.brief || ""}</p>
      <p>Price ${o.price_cents == null ? "awaiting owner quote" : money(o.price_cents)} &middot; deposit ${o.deposit_paid ? "paid" : "due"} &middot; balance ${o.balance_paid ? "paid" : "due"} &middot; paid in full: ${o.paid_in_full ? "yes" : "no"}</p>
      ${o.quote_pending ? `<div style="margin:8px 0"><label>Verified Vistaprint cost (dollars)</label><input id="vp-cost-${o.order_id}" type="number" step="0.01" min="0"><label>Delivery (dollars)</label><input id="vp-delivery-${o.order_id}" type="number" step="0.01" min="0"><button class="btn" onclick="setPrintQuote('${o.order_id}')">Set owner quote</button></div>` : ""}
      <button class="btn ghost" onclick="setState('${o.order_id}','IN_DESIGN')">Start design</button>
      <button class="btn ghost" onclick="setState('${o.order_id}','PROOF_SENT')">Send proof</button>
      <button class="btn ghost" onclick="setState('${o.order_id}','DELIVERED')">Deliver clean files</button>
      <button class="btn ghost" onclick="setState('${o.order_id}','CANCELLED')">Cancel</button>
      <div style="margin-top:8px"><label>Record a cleared Stripe payment — Stripe object id (pi_ / cs_ / ch_ / in_)</label>
      <input id="pay-id-${o.order_id}" placeholder="e.g. pi_...">
      <label>Amount (dollars)</label><input id="pay-amt-${o.order_id}" type="number" step="0.01" min="0">
      <label>Kind</label><select id="pay-kind-${o.order_id}"><option value="deposit">Deposit 35%</option><option value="balance">Balance 65%</option><option value="full">Full payment</option></select>
      <button class="btn" onclick="recordPay('${o.order_id}')">Record cleared payment</button></div>
      </div>`).join("") : "<p>No orders yet.</p>");
  } catch (e) { el.innerHTML = `<p style="color:#e06c5a">${e.message}</p>`; }
}
async function setPrintQuote(oid) {
  const cost = Math.round(parseFloat(document.getElementById("vp-cost-" + oid).value || "0") * 100);
  const delivery = Math.round(parseFloat(document.getElementById("vp-delivery-" + oid).value || "0") * 100);
  try {
    await api("/api/orders/" + oid + "/vistaprint-quote",
      {vistaprint_cost_cents: cost, delivery_cents: delivery}, ownerToken);
    renderOwnerDash();
  } catch (e) { alert(e.message); }
}
async function setState(oid, state) {
  try { await api("/api/orders/" + oid + "/state", {state}, ownerToken); renderOwnerDash(); }
  catch (e) { alert(e.message); }
}
async function recordPay(oid) {
  const amt = Math.round(parseFloat(document.getElementById("pay-amt-" + oid).value || "0") * 100);
  try {
    await api("/api/orders/" + oid + "/record-payment", {
      stripe_object_id: document.getElementById("pay-id-" + oid).value,
      amount_cents: amt, kind: document.getElementById("pay-kind-" + oid).value}, ownerToken);
    renderOwnerDash();
  } catch (e) { alert(e.message); }
}

renderStore();
