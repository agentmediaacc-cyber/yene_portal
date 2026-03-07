set -euo pipefail

echo "==> 1) Writing Supabase SQL schema file (run it in Supabase SQL Editor)..."
cat > supabase_schema_core.sql <<'SQL'
-- =========================
-- CORE PORTAL TABLES (V1)
-- =========================

-- Agent profiles (you already have this table; we add fields if missing)
alter table if exists public.agent_profiles
  add column if not exists auth_id uuid,
  add column if not exists role text default 'AGENT',
  add column if not exists referral_code text,
  add column if not exists referred_by uuid,
  add column if not exists town text,
  add column if not exists region text,
  add column if not exists status text default 'ACTIVE';

create unique index if not exists agent_profiles_auth_id_uq on public.agent_profiles(auth_id);
create unique index if not exists agent_profiles_referral_code_uq on public.agent_profiles(referral_code);

-- Registrations table: every driver/client registered by an agent
create table if not exists public.agent_registrations (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  agent_auth_id uuid not null,
  subject_type text not null check (subject_type in ('driver','client')),
  full_name text not null,
  phone text not null,
  town text,
  external_code text, -- driver code from app / client code
  notes text
);

create index if not exists agent_registrations_agent_idx on public.agent_registrations(agent_auth_id, created_at desc);
create index if not exists agent_registrations_type_idx on public.agent_registrations(subject_type, created_at desc);

-- Weekly invoices (Mon-Sun)
create table if not exists public.agent_weekly_invoices (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  week_start date not null,
  week_end date not null,
  agent_auth_id uuid not null,
  drivers_registered int not null default 0,
  clients_registered int not null default 0,
  amount numeric not null default 0,
  status text not null default 'PENDING', -- PENDING/APPROVED/PAID
  admin_note text
);

create index if not exists agent_weekly_invoices_agent_week_idx on public.agent_weekly_invoices(agent_auth_id, week_start desc);

-- Wallet ledger (admin controlled payouts + adjustments)
create table if not exists public.agent_wallet_ledger (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  agent_auth_id uuid not null,
  entry_type text not null check (entry_type in ('credit','debit')),
  amount numeric not null,
  reference text,
  note text
);

create index if not exists agent_wallet_ledger_agent_idx on public.agent_wallet_ledger(agent_auth_id, created_at desc);

-- =========================
-- RLS POLICIES (READ OWN)
-- =========================
alter table public.agent_profiles enable row level security;
alter table public.agent_registrations enable row level security;
alter table public.agent_weekly_invoices enable row level security;
alter table public.agent_wallet_ledger enable row level security;

drop policy if exists "agents_read_own_profile" on public.agent_profiles;
create policy "agents_read_own_profile"
on public.agent_profiles
for select
to authenticated
using (auth_id = auth.uid());

drop policy if exists "agents_read_own_regs" on public.agent_registrations;
create policy "agents_read_own_regs"
on public.agent_registrations
for select
to authenticated
using (agent_auth_id = auth.uid());

drop policy if exists "agents_read_own_invoices" on public.agent_weekly_invoices;
create policy "agents_read_own_invoices"
on public.agent_weekly_invoices
for select
to authenticated
using (agent_auth_id = auth.uid());

drop policy if exists "agents_read_own_wallet" on public.agent_wallet_ledger;
create policy "agents_read_own_wallet"
on public.agent_wallet_ledger
for select
to authenticated
using (agent_auth_id = auth.uid());

-- NOTE:
-- We will NOT allow browser inserts to registrations (to avoid abuse).
-- Server inserts via SERVICE_ROLE key will bypass RLS.
SQL

echo "==> 2) Patching app.py: add Bearer-token auth + core agent APIs..."
python3 - <<'PY'
from pathlib import Path
import re

p = Path("app.py")
s = p.read_text(encoding="utf-8")

# ---- Add imports if missing
need_imports = [
    "import datetime",
    "import requests",
]
for imp in need_imports:
    if imp not in s:
        s = imp + "\n" + s

# ---- Helper block
marker = "### === CORE AGENT PORTAL API (V1) ==="
if marker not in s:
    block = r'''
### === CORE AGENT PORTAL API (V1) ===
import datetime
import requests

def _env(name, default=""):
    import os
    return (os.getenv(name, default) or "").strip()

SUPABASE_URL = _env("SUPABASE_URL")
SUPABASE_ANON_KEY = _env("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = _env("SUPABASE_SERVICE_ROLE_KEY")

def verify_supabase_bearer():
    """
    Validates Supabase JWT by calling Supabase Auth endpoint.
    Returns dict: { "id": <uuid>, "email": ... }
    """
    from flask import request
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return None

    # Supabase Auth: GET /auth/v1/user
    url = f"{SUPABASE_URL}/auth/v1/user"
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": SUPABASE_ANON_KEY,
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def require_supabase_user(fn):
    from functools import wraps
    from flask import jsonify
    @wraps(fn)
    def wrapped(*args, **kwargs):
        u = verify_supabase_bearer()
        if not u or not u.get("id"):
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return fn(u, *args, **kwargs)
    return wrapped

def sr_postgrest(path):
    # service role PostgREST call helper
    base = SUPABASE_URL.rstrip("/") + "/rest/v1"
    return base + path

def sr_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def week_bounds_local(today=None):
    # Mon-Sun
    if today is None:
        today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday
'''
    s += "\n\n" + block + "\n"

# ---- Add /api/agent/me_v1, /api/agent/summary_v1, /api/agent/activity_v1, register endpoints
if "/api/agent/me_v1" not in s:
    apis = r'''
from flask import request, jsonify

@app.get("/api/agent/me_v1")
@require_supabase_user
def api_agent_me_v1(user):
    uid = user["id"]

    # load profile (service role)
    q = f"/agent_profiles?select=auth_id,full_name,phone,email,role,town,region,status,referral_code&auth_id=eq.{uid}&limit=1"
    r = requests.get(sr_postgrest(q), headers=sr_headers(), timeout=10)
    prof = (r.json()[0] if r.status_code == 200 and r.json() else None)

    # If no profile exists yet, return minimal data (still logged in)
    return jsonify({
        "ok": True,
        "auth_id": uid,
        "email": user.get("email"),
        "profile": prof
    })

@app.get("/api/agent/summary_v1")
@require_supabase_user
def api_agent_summary_v1(user):
    uid = user["id"]
    monday, sunday = week_bounds_local()

    # count regs this week
    q = (
        f"/agent_registrations?select=id,created_at"
        f"&agent_auth_id=eq.{uid}"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
    )
    r = requests.get(sr_postgrest(q), headers=sr_headers(), timeout=10)
    rows = r.json() if r.status_code == 200 else []
    drivers = sum(1 for x in rows if True)  # temp; split below

    # split count by type (more accurate query)
    def count_type(t):
        qq = (
            f"/agent_registrations?select=id"
            f"&agent_auth_id=eq.{uid}"
            f"&subject_type=eq.{t}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
        )
        rr = requests.get(sr_postgrest(qq), headers=sr_headers(), timeout=10)
        return len(rr.json()) if rr.status_code == 200 else 0

    drivers_week = count_type("driver")
    clients_week = count_type("client")

    # all-time drivers (type=driver)
    qq = f"/agent_registrations?select=id&agent_auth_id=eq.{uid}&subject_type=eq.driver"
    rr = requests.get(sr_postgrest(qq), headers=sr_headers(), timeout=10)
    drivers_all = len(rr.json()) if rr.status_code == 200 else 0

    return jsonify({
        "ok": True,
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "drivers_week": drivers_week,
        "clients_week": clients_week,
        "drivers_all": drivers_all
    })

@app.get("/api/agent/activity_v1")
@require_supabase_user
def api_agent_activity_v1(user):
    uid = user["id"]
    monday, sunday = week_bounds_local()
    q = (
        f"/agent_registrations?select=id,created_at,subject_type,full_name,phone,town,external_code"
        f"&agent_auth_id=eq.{uid}"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
        f"&order=created_at.desc&limit=30"
    )
    r = requests.get(sr_postgrest(q), headers=sr_headers(), timeout=10)
    rows = r.json() if r.status_code == 200 else []
    return jsonify({"ok": True, "rows": rows})

@app.post("/api/agent/register_driver_v1")
@require_supabase_user
def api_agent_register_driver_v1(user):
    uid = user["id"]
    j = request.get_json(force=True) or {}
    payload = {
        "agent_auth_id": uid,
        "subject_type": "driver",
        "full_name": (j.get("full_name") or "").strip(),
        "phone": (j.get("phone") or "").strip(),
        "town": (j.get("town") or "").strip(),
        "external_code": (j.get("driver_code") or "").strip(),
        "notes": (j.get("notes") or "").strip(),
    }
    if not payload["full_name"] or not payload["phone"] or not payload["external_code"]:
        return jsonify({"ok": False, "error": "Missing full_name / phone / driver_code"}), 400

    r = requests.post(sr_postgrest("/agent_registrations"), headers=sr_headers(), json=payload, timeout=10)
    if r.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r.text}), 500
    return jsonify({"ok": True, "row": r.json()[0]})

@app.post("/api/agent/register_client_v1")
@require_supabase_user
def api_agent_register_client_v1(user):
    uid = user["id"]
    j = request.get_json(force=True) or {}
    payload = {
        "agent_auth_id": uid,
        "subject_type": "client",
        "full_name": (j.get("full_name") or "").strip(),
        "phone": (j.get("phone") or "").strip(),
        "town": (j.get("town") or "").strip(),
        "external_code": (j.get("client_code") or "").strip(),
        "notes": (j.get("notes") or "").strip(),
    }
    if not payload["full_name"] or not payload["phone"] or not payload["external_code"]:
        return jsonify({"ok": False, "error": "Missing full_name / phone / client_code"}), 400

    r = requests.post(sr_postgrest("/agent_registrations"), headers=sr_headers(), json=payload, timeout=10)
    if r.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r.text}), 500
    return jsonify({"ok": True, "row": r.json()[0]})

@app.get("/api/admin/registrations_week_v1")
def api_admin_registrations_week_v1():
    # simple admin view (you can protect later)
    monday, sunday = week_bounds_local()
    q = (
        f"/agent_registrations?select=id,created_at,agent_auth_id,subject_type,full_name,phone,town,external_code"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
        f"&order=created_at.desc&limit=500"
    )
    r = requests.get(sr_postgrest(q), headers=sr_headers(), timeout=10)
    rows = r.json() if r.status_code == 200 else []
    return jsonify({"ok": True, "week_start": monday.isoformat(), "week_end": sunday.isoformat(), "rows": rows})
'''
    s += "\n\n" + apis + "\n"

p.write_text(s, encoding="utf-8")
print("✅ app.py patched (core agent APIs added)")
PY

echo "==> 3) Replacing templates/agent_dashboard.html with a clean mobile-first dashboard..."
cat > templates/agent_dashboard.html <<'HTML'
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>YENE Agent Dashboard</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:0;background:#0b1220;color:#e8f0ff}
    .wrap{max-width:980px;margin:0 auto;padding:14px}
    .card{background:#101a33;border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:14px;margin:10px 0}
    .row{display:flex;gap:10px;flex-wrap:wrap}
    .stat{flex:1;min-width:160px}
    .muted{opacity:.8}
    input,select,button{width:100%;padding:12px;border-radius:12px;border:1px solid rgba(255,255,255,.12);background:#0b1220;color:#e8f0ff}
    button{background:#2c6cff;border:none;font-weight:700;cursor:pointer}
    button:disabled{opacity:.5;cursor:not-allowed}
    table{width:100%;border-collapse:collapse}
    th,td{padding:10px;border-bottom:1px solid rgba(255,255,255,.08);text-align:left;font-size:14px}
    .pill{display:inline-block;padding:3px 10px;border-radius:999px;background:rgba(44,108,255,.18);border:1px solid rgba(44,108,255,.35)}
    .topbar{display:flex;justify-content:space-between;align-items:center;gap:10px}
    .topbar a{color:#9ec0ff;text-decoration:none}
    .two{display:grid;grid-template-columns:1fr;gap:10px}
    @media (min-width: 900px){.two{grid-template-columns:1fr 1fr}}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <div class="muted">🌐 YENE Portal</div>
        <h2 style="margin:6px 0" id="welcome">Welcome, Agent</h2>
        <div class="muted" id="weekLabel"></div>
      </div>
      <div>
        <button id="signOutBtn">Sign out</button>
      </div>
    </div>

    <div class="card">
      <div class="row">
        <div class="stat">
          <div class="muted">Drivers registered (this week)</div>
          <div style="font-size:28px;font-weight:800" id="driversWeek">0</div>
        </div>
        <div class="stat">
          <div class="muted">Clients registered (this week)</div>
          <div style="font-size:28px;font-weight:800" id="clientsWeek">0</div>
        </div>
        <div class="stat">
          <div class="muted">Drivers (all-time)</div>
          <div style="font-size:28px;font-weight:800" id="driversAll">0</div>
        </div>
      </div>
    </div>

    <div class="two">
      <div class="card">
        <h3 style="margin:0 0 10px">Quick Register Driver</h3>
        <div class="row">
          <div style="flex:1;min-width:220px"><input id="dName" placeholder="Full name" /></div>
          <div style="flex:1;min-width:220px"><input id="dPhone" placeholder="Phone e.g +264..." /></div>
        </div>
        <div class="row">
          <div style="flex:1;min-width:220px"><input id="dTown" placeholder="Town (Rundu, etc)" /></div>
          <div style="flex:1;min-width:220px"><input id="dCode" placeholder="Driver Code (from app)" /></div>
        </div>
        <button id="btnDriver">Submit Driver</button>
        <div class="muted" id="driverMsg" style="margin-top:8px"></div>
      </div>

      <div class="card">
        <h3 style="margin:0 0 10px">Quick Register Client</h3>
        <div class="row">
          <div style="flex:1;min-width:220px"><input id="cName" placeholder="Full name" /></div>
          <div style="flex:1;min-width:220px"><input id="cPhone" placeholder="Phone e.g +264..." /></div>
        </div>
        <div class="row">
          <div style="flex:1;min-width:220px"><input id="cTown" placeholder="Town (Rundu, etc)" /></div>
          <div style="flex:1;min-width:220px"><input id="cCode" placeholder="Client Code (from app)" /></div>
        </div>
        <button id="btnClient">Submit Client</button>
        <div class="muted" id="clientMsg" style="margin-top:8px"></div>
      </div>
    </div>

    <div class="card">
      <h3 style="margin:0 0 10px">Recent Activity (this week)</h3>
      <table>
        <thead>
          <tr><th>Type</th><th>Name</th><th>Phone</th><th>Town</th><th>Code</th><th>Date</th></tr>
        </thead>
        <tbody id="activityBody">
          <tr><td colspan="6" class="muted">Loading…</td></tr>
        </tbody>
      </table>
      <div class="muted" style="margin-top:8px;font-size:12px">Mon–Sun tracking (weekly). Admin validates and pays from the admin dashboard.</div>
    </div>

  </div>

<script>
(async () => {
  // Build supabase client from server at runtime:
  async function getCfg(){
    const r = await fetch("/api/public-config");
    const j = await r.json();
    if(!j.ok) throw new Error("Missing Supabase config");
    return j;
  }

  const cfg = await getCfg();
  const sb = supabase.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY);
  window.sb = sb;

  async function waitForSession(ms=4500){
    const start = Date.now();
    while(Date.now() - start < ms){
      const { data } = await sb.auth.getSession();
      if(data && data.session) return data.session;
      await new Promise(r => setTimeout(r, 150));
    }
    return null;
  }

  const session = await waitForSession();
  if(!session){
    location.href = "/login";
    return;
  }

  async function bearer(){
    const { data } = await sb.auth.getSession();
    if(!data.session) return null;
    return "Bearer " + data.session.access_token;
  }

  document.getElementById("signOutBtn").onclick = async () => {
    await sb.auth.signOut();
    location.href = "/login";
  };

  async function api(path, opts={}){
    const b = await bearer();
    if(!b){ location.href="/login"; return null; }
    opts.headers = Object.assign({}, opts.headers||{}, {"Authorization": b, "Content-Type":"application/json"});
    const r = await fetch(path, opts);
    const j = await r.json().catch(()=>({ok:false,error:"bad json"}));
    if(r.status===401){ location.href="/login"; return null; }
    return j;
  }

  // Load agent profile (name)
  const me = await api("/api/agent/me_v1");
  if(me && me.ok){
    const name = me.profile?.full_name || "Agent";
    document.getElementById("welcome").textContent = "Welcome, " + name;
  }

  // Weekly summary
  const sum = await api("/api/agent/summary_v1");
  if(sum && sum.ok){
    document.getElementById("driversWeek").textContent = sum.drivers_week || 0;
    document.getElementById("clientsWeek").textContent = sum.clients_week || 0;
    document.getElementById("driversAll").textContent = sum.drivers_all || 0;
    document.getElementById("weekLabel").textContent = "Week: " + sum.week_start + " → " + sum.week_end;
  }

  // Activity
  async function loadActivity(){
    const act = await api("/api/agent/activity_v1");
    const body = document.getElementById("activityBody");
    body.innerHTML = "";
    const rows = (act && act.ok) ? (act.rows||[]) : [];
    if(rows.length === 0){
      body.innerHTML = '<tr><td colspan="6" class="muted">No registrations yet this week.</td></tr>';
      return;
    }
    for(const r of rows){
      const d = new Date(r.created_at);
      body.insertAdjacentHTML("beforeend",
        `<tr>
          <td><span class="pill">${r.subject_type}</span></td>
          <td>${r.full_name||""}</td>
          <td>${r.phone||""}</td>
          <td>${r.town||""}</td>
          <td>${r.external_code||""}</td>
          <td>${d.toLocaleString()}</td>
        </tr>`
      );
    }
  }
  await loadActivity();

  // Register Driver
  document.getElementById("btnDriver").onclick = async () => {
    const btn = document.getElementById("btnDriver");
    btn.disabled = true;
    document.getElementById("driverMsg").textContent = "Saving…";
    const payload = {
      full_name: document.getElementById("dName").value,
      phone: document.getElementById("dPhone").value,
      town: document.getElementById("dTown").value,
      driver_code: document.getElementById("dCode").value
    };
    const j = await api("/api/agent/register_driver_v1", {method:"POST", body: JSON.stringify(payload)});
    if(j && j.ok){
      document.getElementById("driverMsg").textContent = "✅ Driver registered.";
      ["dName","dPhone","dTown","dCode"].forEach(id => document.getElementById(id).value="");
      await loadActivity();
      const sum2 = await api("/api/agent/summary_v1");
      if(sum2 && sum2.ok){
        document.getElementById("driversWeek").textContent = sum2.drivers_week || 0;
        document.getElementById("driversAll").textContent = sum2.drivers_all || 0;
      }
    }else{
      document.getElementById("driverMsg").textContent = "❌ " + (j?.error || "Failed");
    }
    btn.disabled = false;
  };

  // Register Client
  document.getElementById("btnClient").onclick = async () => {
    const btn = document.getElementById("btnClient");
    btn.disabled = true;
    document.getElementById("clientMsg").textContent = "Saving…";
    const payload = {
      full_name: document.getElementById("cName").value,
      phone: document.getElementById("cPhone").value,
      town: document.getElementById("cTown").value,
      client_code: document.getElementById("cCode").value
    };
    const j = await api("/api/agent/register_client_v1", {method:"POST", body: JSON.stringify(payload)});
    if(j && j.ok){
      document.getElementById("clientMsg").textContent = "✅ Client registered.";
      ["cName","cPhone","cTown","cCode"].forEach(id => document.getElementById(id).value="");
      await loadActivity();
      const sum2 = await api("/api/agent/summary_v1");
      if(sum2 && sum2.ok){
        document.getElementById("clientsWeek").textContent = sum2.clients_week || 0;
      }
    }else{
      document.getElementById("clientMsg").textContent = "❌ " + (j?.error || "Failed");
    }
    btn.disabled = false;
  };

})();
</script>

</body>
</html>
HTML

echo "==> 4) Compile check..."
python3 -m py_compile app.py

echo "==> 5) Git add/commit/push..."
git add app.py templates/agent_dashboard.html supabase_schema_core.sql
git commit -m "Core agent portal: registrations + weekly summary + activity + profile load (V1)" || true
git push origin main

echo ""
echo "✅ DONE."
echo ""
echo "NEXT:"
echo "1) Open Supabase -> SQL Editor -> Run supabase_schema_core.sql"
echo "2) Wait Render deploy"
echo "3) Test agent dashboard: register a driver/client and confirm counts increase"
