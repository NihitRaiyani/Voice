// Execute the shipped client against synthetic browser boundaries. No network or account.
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const source = fs.readFileSync(process.argv[2], "utf8");
class Element {
  constructor() { this.value = ""; this.hidden = false; this.disabled = false; this.textContent = ""; this.children = []; this.handlers = {}; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.textContent = ""; }
  set innerHTML(_) { throw new Error("Unsafe HTML rendering"); }
}
function environment({admin = true, delayed = false, delayedLogout = false, delayedHistory = false} = {}) {
  const nodes = new Map(), requests = [], sockets = [], history = [], timers = new Map(), windowHandlers = {};
  let timerId = 0;
  const setInterval = (callback, delay) => { const id = ++timerId; timers.set(id, {callback, delay}); return id; };
  const clearInterval = (id) => timers.delete(id);
  const el = (name) => { if (!nodes.has(name)) nodes.set(name, new Element()); return nodes.get(name); };
  let release, rejectLogout;
  const snapshot = {calls: [{call_id: "synthetic-call", observed_at: "2030-01-01T00:00:00Z", stage: "<img src=x>", language: "hi", turn: 2, duration_seconds: 10, latency_p95_ms: null, safety_hits: 1, booking_confirmed: false, costs: [{currency: "INR", amount: "0.0000001"}]}]};
  const responses = {
    "/api/v1/auth/login": {access_token: "synthetic-access", refresh_token: "synthetic-refresh"},
    "/api/v1/auth/me": {is_active: true, roles: [admin ? "admin" : "viewer"]},
    "/api/v1/auth/logout": {revoked: true},
    "/api/v1/supervisor/snapshot": snapshot
  };
  async function fetch(path, options) {
    requests.push({path, options});
    let data = responses[path];
    if (path === "/api/v1/auth/logout" && delayedLogout) await new Promise((_, reject) => { rejectLogout = reject; });
    if (path === "/api/v1/supervisor/snapshot" && delayed) data = await new Promise((resolve) => { release = () => resolve(snapshot); });
    if (path.startsWith("/api/v1/analytics/overview?")) data = {calls: {total: 12, completed: 5, abandoned: 3, active: 4}, converted_calls: 2, latency_ms: {p95: null}, window: {start: "2030-01-01", end: "2030-03-01"}};
    if (path.startsWith("/api/v1/analytics/funnel?")) data = {stages: [{stage: "incoming", calls: 12}]};
    if (path.startsWith("/api/v1/analytics/overview?")) data.window.start = new URL(path, "http://synthetic.invalid").searchParams.get("start_date");
    if (path.startsWith("/api/v1/analytics/") && delayedHistory) data = await new Promise((resolve) => { const value = data; history.push(() => resolve(value)); });
    assert.ok(data, `Unexpected client request ${path}`);
    return {ok: true, json: async () => ({success: true, data})};
  }
  class Socket {
    constructor(url) { this.url = String(url); this.sent = []; sockets.push(this); }
    send(data) { this.sent.push(JSON.parse(data)); }
    close() { this.closed = true; }
  }
  const document = {getElementById: el, createElement: () => new Element(), createDocumentFragment: () => new Element()};
  const window = {location: {href: "http://127.0.0.1:8123/admin/supervisor", protocol: "http:"}, addEventListener(name, handler) { windowHandlers[name] = handler; }};
  vm.runInNewContext(source, {document, window, fetch, WebSocket: Socket, URL, URLSearchParams, AbortSignal, setInterval, clearInterval});
  const submit = (id) => el(id).handlers.submit({preventDefault() {}});
  async function login() {
    el("email").value = "synthetic@example.invalid";
    el("password").value = "Synthetic-password";
    await submit("login-form");
  }
  return {el, requests, sockets, submit, login, timers, windowHandlers, setSnapshotCalls: (calls) => { snapshot.calls = calls; }, release: () => release(), rejectLogout: () => rejectLogout(new Error("Synthetic logout failure")), releaseHistory: (index) => history[index]()};
}
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };
const first = environment();
await first.login();
assert.equal(JSON.parse(first.requests[0].options.body).password, "Synthetic-password", "Login must send the entered password before clearing the input");
assert.equal(first.el("password").value, "");
assert.equal(first.sockets.length, 1);
assert.equal(first.el("dashboard").hidden, false);
const socket = first.sockets[0];
socket.onopen();
assert.deepEqual(socket.sent[0], {token: "synthetic-access"});
assert.equal(socket.url, "ws://127.0.0.1:8123/api/v1/supervisor/ws");
socket.onmessage({data: JSON.stringify({kind: "resync_required"})});
await flush();
const row = first.el("calls").children[0].children[0];
assert.equal(row.children[1].textContent, "<img src=x> / hi");
assert.equal(row.children[4].textContent, "Unknown");
assert.equal(row.children[7].textContent, "INR 0.0000001");
await first.submit("history-form");
assert.equal(first.el("overview").children[0].children[1].textContent, "12");
assert.equal(first.el("funnel").children[0].children[1].textContent, "12");
await first.el("logout").handlers.click();
assert.equal(first.el("calls").children.length, 0);
assert.equal(first.el("dashboard").hidden, true);
assert.ok(first.requests.some((request) => request.path === "/api/v1/auth/logout"));
for (const request of first.requests) {
  assert.equal(request.options.credentials, "omit");
  assert.equal(request.options.redirect, "error");
  assert.ok(!request.path.includes("synthetic-access"));
}
const viewer = environment({admin: false});
await viewer.login();
assert.equal(viewer.sockets.length, 0);
assert.ok(!viewer.requests.some((request) => request.path.includes("supervisor")));
assert.equal(viewer.el("dashboard").hidden, true);
const stale = environment({delayed: true});
await stale.login();
stale.sockets[0].onmessage({data: JSON.stringify({kind: "resync_required"})});
await flush();
stale.sockets[0].onclose();
stale.release();
await flush();
assert.equal(stale.el("calls").children.length, 0);
assert.equal(stale.el("dashboard").hidden, true);
assert.equal(stale.timers.size, 0, "Connection closure cancels periodic SQL refresh");

const logoutRace = environment({delayedLogout: true});
await logoutRace.login();
const oldLogout = logoutRace.el("logout").handlers.click();
await logoutRace.login();
const newStatus = logoutRace.el("status").textContent;
logoutRace.rejectLogout();
await oldLogout;
assert.equal(logoutRace.el("dashboard").hidden, false);
assert.equal(logoutRace.el("status").textContent, newStatus, "A stale logout must not overwrite a later session");

const historyRace = environment({delayedHistory: true});
await historyRace.login();
historyRace.el("start").value = "2030-01-01";
const oldHistory = historyRace.submit("history-form");
historyRace.el("start").value = "2030-02-01";
const newHistory = historyRace.submit("history-form");
historyRace.releaseHistory(2); historyRace.releaseHistory(3);
await newHistory;
const newWindow = historyRace.el("history-status").textContent;
assert.ok(newWindow.includes("2030-02-01"));
historyRace.releaseHistory(0); historyRace.releaseHistory(1);
await oldHistory;
assert.equal(historyRace.el("history-status").textContent, newWindow, "An older history request must not replace a later UTC window");
assert.equal(historyRace.el("overview").children.length, 6);

const periodic = environment();
await periodic.login();
assert.equal(periodic.timers.size, 1, "An authenticated session owns one SQL refresh timer");
const firstTimer = [...periodic.timers.values()][0];
assert.equal(firstTimer.delay, 30000);
periodic.sockets[0].onmessage({data: JSON.stringify({kind: "resync_required"})});
await flush();
assert.equal(periodic.el("calls").children[0].children.length, 1);
periodic.setSnapshotCalls([]);
firstTimer.callback();
await flush();
assert.equal(periodic.el("calls").children[0].children.length, 0, "A periodic SQL snapshot removes calls no longer active");
assert.equal(periodic.el("empty").hidden, false);
await periodic.el("logout").handlers.click();
assert.equal(periodic.timers.size, 0, "Sign out cancels the refresh timer");
await periodic.login();
assert.equal(periodic.timers.size, 1, "A later session has exactly one fresh timer");
const requestsBeforeOldTimer = periodic.requests.length;
firstTimer.callback();
await flush();
assert.equal(periodic.requests.length, requestsBeforeOldTimer, "An old timer callback cannot fetch for a later session");
periodic.windowHandlers.pagehide();
assert.equal(periodic.timers.size, 0, "Leaving the page cancels the timer");

const coalesced = environment({delayed: true});
await coalesced.login();
coalesced.sockets[0].onmessage({data: JSON.stringify({kind: "resync_required"})});
await flush();
const pendingTimer = [...coalesced.timers.values()][0];
pendingTimer.callback(); pendingTimer.callback();
coalesced.el("resync").handlers.click();
coalesced.release();
await flush();
assert.equal(coalesced.requests.filter((r) => r.path === "/api/v1/supervisor/snapshot").length, 1, "Timer and manual refresh coalesce while a SQL snapshot is pending");
assert.equal(coalesced.el("resync").disabled, false);
coalesced.windowHandlers.pagehide();

console.log("Supervisor client: login, Admin denial, safe rendering, snapshots, SQL history, logout, stale requests and periodic session cleanup passed.");
