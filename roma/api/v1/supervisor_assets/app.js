"use strict";

(() => {
  const el = (id) => document.getElementById(id);
  let access = null, refresh = null, socket = null, session = 0;
  let pending = [], draining = false, historyRequest = 0;
  let resyncTimer = null, resyncPending = false;
  const calls = new Map();
  const number = (value, suffix = "") => typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * 10) / 10}${suffix}` : "Unknown";

  function clearView(message) {
    session += 1;
    access = refresh = null;
    if (resyncTimer !== null) { clearInterval(resyncTimer); resyncTimer = null; }
    if (socket) { socket.onclose = null; socket.close(); socket = null; }
    pending = [];
    resyncPending = false;
    calls.clear();
    for (const id of ["calls", "overview", "funnel"]) el(id).replaceChildren();
    el("dashboard").hidden = el("logout").hidden = true;
    el("login-form").hidden = false;
    el("signin").disabled = false;
    el("resync").disabled = el("history-refresh").disabled = false;
    el("password").value = "";
    el("status").textContent = message;
    el("history-status").textContent = "Choose a window of at most 366 days.";
  }

  async function api(path, options = {}, expected = session) {
    const response = await fetch(path, {
      ...options, credentials: "omit", redirect: "error", cache: "no-store",
      headers: {"Content-Type": "application/json", ...(access ? {Authorization: `Bearer ${access}`} : {})},
      signal: AbortSignal.timeout(8000)
    });
    if (!response.ok) throw new Error("Request unavailable or access denied.");
    const body = await response.json();
    if (expected !== session) throw new Error("Session changed.");
    if (body.success !== true) throw new Error("Request unavailable.");
    return body.data;
  }

  function cells(row, values) {
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    }
  }

  function drawCalls() {
    const fragment = document.createDocumentFragment();
    const values = [...calls.values()].sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at));
    for (const call of values) {
      const row = document.createElement("tr");
      const costs = (call.costs || []).map((cost) => `${cost.currency} ${cost.amount}`).join(" / ") || "No priced usage";
      cells(row, [call.call_id, `${call.stage} / ${call.language}`, number(call.turn), number(call.duration_seconds, " s"),
        number(call.latency_p95_ms, " ms"), number(call.safety_hits), call.booking_confirmed ? "SQL confirmed" : "Unconfirmed", costs,
        new Date(call.observed_at).toLocaleTimeString()]);
      fragment.append(row);
    }
    el("calls").replaceChildren(fragment);
    el("empty").hidden = calls.size > 0;
  }

  function updateCall(call) {
    if (!call || typeof call.call_id !== "string" || !Number.isFinite(Date.parse(call.observed_at))) return;
    const old = calls.get(call.call_id);
    if (!old || Date.parse(call.observed_at) > Date.parse(old.observed_at)) calls.set(call.call_id, call);
    if (calls.size > 100) {
      const oldest = [...calls.values()].sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at))[0];
      calls.delete(oldest.call_id);
    }
  }

  async function drain(expected) {
    if (draining) return;
    draining = true;
    try {
      while (pending.length && expected === session) {
        const event = pending.shift();
        if (event.kind === "resync_required") {
          el("resync").disabled = true;
          const data = await api("/api/v1/supervisor/snapshot", {}, expected);
          calls.clear();
          for (const call of data.calls || []) updateCall(call);
          resyncPending = false;
          el("resync").disabled = false;
          el("status").textContent = "Connected · SQL snapshot loaded; receiving live metadata.";
        } else if (event.kind === "call_update") updateCall(event);
        drawCalls();
      }
    } catch (_) {
      if (expected === session) clearView("Live data unavailable. Sign in again to reconnect and load a fresh SQL snapshot.");
    } finally {
      draining = false;
      if (pending.length && access) void drain(session);
    }
  }

  function enqueue(event, expected) {
    if (expected !== session || !access) return;
    if (event.kind === "resync_required") {
      if (resyncPending) return;
      resyncPending = true;
    }
    if (pending.length >= 128) { clearView("Live updates exceeded this display's buffer. Sign in again to resync."); return; }
    pending.push(event);
    void drain(expected);
  }

  function connect(expected) {
    const url = new URL("/api/v1/supervisor/ws", window.location.href);
    url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(url);
    const owned = socket;
    owned.onopen = () => { if (expected === session) owned.send(JSON.stringify({token: access})); };
    owned.onmessage = (message) => {
      if (expected !== session) return;
      try {
        if (typeof message.data !== "string" || message.data.length > 4096) throw new Error();
        enqueue(JSON.parse(message.data), expected);
      } catch (_) { clearView("Invalid live update. Sign in again to resync."); }
    };
    owned.onclose = () => { if (expected === session) clearView("Live connection closed or authorization expired. Sign in again to reconnect."); };
  }

  el("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const credentials = {email: el("email").value, password: el("password").value};
    clearView("Signing in…");
    el("signin").disabled = true;
    const expected = session;
    try {
      const tokens = await api("/api/v1/auth/login", {method: "POST", body: JSON.stringify(credentials)}, expected);
      access = tokens.access_token;
      refresh = tokens.refresh_token;
      const principal = await api("/api/v1/auth/me", {}, expected);
      if (principal.is_active !== true || !principal.roles.includes("admin")) throw new Error();
      el("dashboard").hidden = el("logout").hidden = false;
      el("login-form").hidden = true;
      el("status").textContent = "Authenticated Admin · Connecting to the supervisor…";
      connect(expected);
      resyncTimer = setInterval(() => enqueue({kind: "resync_required"}, expected), 30000);
    } catch (_) {
      if (expected === session) clearView("Sign in failed or active Admin access is unavailable.");
    } finally { credentials.password = ""; }
  });

  el("logout").addEventListener("click", async () => {
    const token = refresh;
    clearView("Signed out. Call metadata has been cleared.");
    const expected = session;
    if (token) {
      try { await api("/api/v1/auth/logout", {method: "POST", body: JSON.stringify({refresh_token: token})}, expected); }
      catch (_) { if (expected === session) el("status").textContent = "Display signed out. Server logout could not be confirmed; the session expires under its existing policy."; }
    }
  });

  el("resync").addEventListener("click", () => enqueue({kind: "resync_required"}, session));
  el("history-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const expected = session;
    const request = ++historyRequest;
    const query = new URLSearchParams({start_date: el("start").value, end_date: el("end").value});
    el("history-refresh").disabled = true;
    el("history-status").textContent = "Loading SQL aggregates…";
    el("overview").replaceChildren();
    el("funnel").replaceChildren();
    try {
      const [overview, funnel] = await Promise.all([
        api(`/api/v1/analytics/overview?${query}`, {}, expected), api(`/api/v1/analytics/funnel?${query}`, {}, expected)
      ]);
      if (expected !== session || request !== historyRequest) return;
      for (const [label, value] of [["Calls", overview.calls.total], ["Completed", overview.calls.completed],
        ["Abandoned", overview.calls.abandoned], ["Active", overview.calls.active], ["SQL conversions", overview.converted_calls],
        ["Software latency P95", number(overview.latency_ms.p95, " ms")]]) {
        const group = document.createElement("div"), term = document.createElement("dt"), detail = document.createElement("dd");
        term.textContent = label;
        detail.textContent = typeof value === "string" ? value : number(value);
        group.append(term, detail); el("overview").append(group);
      }
      for (const stage of funnel.stages) {
        const row = document.createElement("tr"); cells(row, [stage.stage, number(stage.calls)]); el("funnel").append(row);
      }
      el("history-status").textContent = `SQL window: ${overview.window.start} to ${overview.window.end} (exclusive).`;
    } catch (_) {
      if (expected === session && request === historyRequest) el("history-status").textContent = "History unavailable or access denied. Check the UTC window and sign in again if needed.";
    } finally { if (expected === session && request === historyRequest) el("history-refresh").disabled = false; }
  });

  const today = new Date(), tomorrow = new Date(today.getTime() + 86400000);
  el("start").value = today.toISOString().slice(0, 10);
  el("end").value = tomorrow.toISOString().slice(0, 10);
  window.addEventListener("pagehide", () => clearView("Session closed."));
})();
