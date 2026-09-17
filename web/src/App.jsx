import { useEffect, useRef, useState } from "react";

// Never hardcoded: Hostinger later is a config change, not a rewrite (docs/13).
const API = import.meta.env.VITE_API_BASE;
// Sent on every request. NOT a secret: Vite inlines this into the bundle, so anyone who
// can load this page can read it. It guards the NETWORK boundary — another machine
// reaching :8020 — which is the threat it was added for. Real auth for a public deploy
// is a session, not a bundled constant (docs/13).
const TOKEN = import.meta.env.VITE_API_TOKEN;
const AUTH = { Authorization: `Bearer ${TOKEN}` };

const POLL_MS = 2000;
// Terminal states stop the poll. `no_answer` is one of them: the backend derives it from the
// record's age because nothing in the system reports an unanswered call, so it will never
// advance on its own and polling past it is just noise.
const TERMINAL = ["connected", "ended", "no_answer", "failed", "not authorised", "rate limited"];

export default function App() {
  const [number, setNumber] = useState("");
  const [status, setStatus] = useState("idle");
  const [reason, setReason] = useState("");
  const uuidRef = useRef(null);

  useEffect(() => {
    if (status !== "dialing" || !uuidRef.current) return;
    const id = setInterval(async () => {
      try {
        const res = await fetch(`${API}/api/call/${uuidRef.current}`, { headers: AUTH });
        if (!res.ok) return; // 404 until the row lands; keep polling
        const body = await res.json();
        setStatus(body.status);
        setReason(body.reason || "");
      } catch {
        // A dropped poll is not a failed call — the call is on the phone network, not in
        // this tab. Leave the state alone and try again on the next tick.
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [status]);

  async function placeCall(e) {
    e.preventDefault();
    setStatus("dialing");
    setReason("");
    uuidRef.current = null;
    try {
      const res = await fetch(`${API}/api/call`, {
        method: "POST",
        headers: { "content-type": "application/json", ...AUTH },
        body: JSON.stringify({ to_number: number.trim() }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        // An OPERATOR problem, not a lead problem. Distinguished on purpose: a misconfigured
        // token or a spent hourly cap has nothing to do with this number, and folding them
        // into the same line as `block:dnd` is how a config mistake gets read as a DND hit
        // and someone goes looking in the wrong place.
        if (res.status === 401 || res.status === 503) {
          setStatus("not authorised");
          setReason(
            res.status === 503
              ? "API_TOKEN is not set on the server — the endpoint is disabled"
              : "check VITE_API_TOKEN matches the server's API_TOKEN"
          );
          return;
        }
        if (res.status === 429) {
          setStatus("rate limited");
          setReason(body?.detail?.reason || "hourly dial cap reached");
          return;
        }
        setStatus("failed");
        // The gate's own string, verbatim — `block:dnd` and `block:budget` mean different
        // things and the operator needs to know WHICH gate refused. Prettifying them here
        // would throw away the only precise part of the answer.
        setReason(body?.detail?.reason || body?.detail || `HTTP ${res.status}`);
        return;
      }
      uuidRef.current = body.request_uuid;
      setStatus("dialing");
    } catch (err) {
      setStatus("failed");
      setReason(String(err));
    }
  }

  const busy = status === "dialing";

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 460, margin: "4rem auto" }}>
      <h1 style={{ fontSize: "1.25rem" }}>Roma — place a call</h1>
      <form onSubmit={placeCall}>
        <input
          value={number}
          onChange={(e) => setNumber(e.target.value)}
          placeholder="+919876543210 (Indian mobile)"
          aria-label="Phone number in E.164 format"
          style={{ width: "100%", padding: "0.5rem", fontSize: "1rem" }}
        />
        <button
          type="submit"
          disabled={busy || !number.trim()}
          style={{ marginTop: "0.75rem", padding: "0.5rem 1rem", fontSize: "1rem" }}
        >
          {busy ? "Dialing…" : "Place call"}
        </button>
      </form>
      <p style={{ marginTop: "1.5rem" }}>
        Status: <strong>{status}</strong>
        {reason ? <span> — {reason}</span> : null}
      </p>
    </main>
  );
}
