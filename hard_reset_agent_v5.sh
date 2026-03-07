set -euo pipefail

# -----------------------------
# 1) write SQL file for Supabase
# -----------------------------
cat > supabase_agent_v5.sql <<'SQL'
create table if not exists public.agent_registrations (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  agent_auth_id uuid not null,
  subject_type text not null check (subject_type in ('driver','client')),
  full_name text not null,
  phone text not null,
  town text,
  external_code text
);

create index if not exists idx_agent_registrations_v5_agent
on public.agent_registrations(agent_auth_id, created_at desc);

create table if not exists public.agent_wallet_ledger (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  agent_auth_id uuid not null,
  week_start date,
  week_end date,
  entry_type text not null check (entry_type in ('credit','debit')),
  amount numeric not null default 0,
  reference text,
  note text
);

create index if not exists idx_agent_wallet_v5_agent
on public.agent_wallet_ledger(agent_auth_id, created_at desc);

create table if not exists public.agent_driver_trip_updates (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  agent_auth_id uuid not null,
  driver_phone text not null,
  driver_name text,
  week_start date not null,
  week_end date not null,
  trips int not null default 0,
  bonus_amount numeric not null default 0,
  admin_note text
);

create index if not exists idx_agent_trips_v5_agent
on public.agent_driver_trip_updates(agent_auth_id, week_start desc);

alter table public.agent_profiles
  add column if not exists auth_id uuid,
  add column if not exists role text default 'AGENT',
  add column if not exists referral_code text,
  add column if not exists referred_by_code text;

update public.agent_profiles ap
set auth_id = u.id,
    user_id = u.id
from auth.users u
where ap.email is not null
  and lower(ap.email) = lower(u.email)
  and (ap.auth_id is null or ap.user_id is null);

update public.agent_profiles
set role = 'AGENT'
where role is null;

alter table public.agent_profiles enable row level security;
alter table public.agent_registrations enable row level security;
alter table public.agent_wallet_ledger enable row level security;
alter table public.agent_driver_trip_updates enable row level security;

drop policy if exists agents_read_own_profile on public.agent_profiles;
create policy agents_read_own_profile
on public.agent_profiles
for select
to authenticated
using (auth_id = auth.uid());

drop policy if exists agents_read_own_regs_v5 on public.agent_registrations;
create policy agents_read_own_regs_v5
on public.agent_registrations
for select
to authenticated
using (agent_auth_id = auth.uid());

drop policy if exists agents_read_own_wallet_v5 on public.agent_wallet_ledger;
create policy agents_read_own_wallet_v5
on public.agent_wallet_ledger
for select
to authenticated
using (agent_auth_id = auth.uid());

drop policy if exists agents_read_own_trips_v5 on public.agent_driver_trip_updates;
create policy agents_read_own_trips_v5
on public.agent_driver_trip_updates
for select
to authenticated
using (agent_auth_id = auth.uid());
SQL

# -----------------------------
# 2) patch app.py with one clean V5 API set
# -----------------------------
python3 - <<'PY'
from pathlib import Path

p = Path("app.py")
s = p.read_text(encoding="utf-8")

block = r'''
# =========================
# AGENT V5 HARD RESET
# =========================
import os
import io
import csv
import datetime
import requests
from flask import request, jsonify, Response

def _v5_env(name, default=""):
    return (os.getenv(name, default) or "").strip()

_V5_SUPABASE_URL = _v5_env("SUPABASE_URL")
_V5_SUPABASE_ANON = _v5_env("SUPABASE_ANON_KEY")
_V5_SUPABASE_SERVICE = _v5_env("SUPABASE_SERVICE_ROLE_KEY")

def _v5_rest(path):
    return _V5_SUPABASE_URL.rstrip("/") + "/rest/v1" + path

def _v5_headers():
    return {
        "apikey": _V5_SUPABASE_SERVICE,
        "Authorization": f"Bearer {_V5_SUPABASE_SERVICE}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def _v5_verify_bearer():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        r = requests.get(
            _V5_SUPABASE_URL.rstrip("/") + "/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": _V5_SUPABASE_ANON},
            timeout=10
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def _v5_find_profile(uid):
    if not uid:
        return None
    q = f"/agent_profiles?select=*&auth_id=eq.{uid}&limit=1"
    r = requests.get(_v5_rest(q), headers=_v5_headers(), timeout=10)
    if r.status_code == 200 and r.json():
        return r.json()[0]
    return None

def _v5_week_bounds(ref_date=None):
    if ref_date is None:
        ref_date = datetime.date.today()
    monday = ref_date - datetime.timedelta(days=ref_date.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday

def _v5_count(path):
    r = requests.get(_v5_rest(path), headers=_v5_headers(), timeout=10)
    if r.status_code == 200:
        return len(r.json())
    return 0

@app.get("/api/agent/bootstrap_v5")
def api_agent_bootstrap_v5():
    user = _v5_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    profile = _v5_find_profile(uid)

    # no linked profile yet: return empty self-only state
    if not profile:
        return jsonify({
            "ok": True,
            "profile": None,
            "week_start": None,
            "week_end": None,
            "summary": {"drivers_week": 0, "clients_week": 0, "drivers_all": 0},
            "wallet": {"balance": 0, "rows": []},
            "invoices": [],
            "activity": [],
            "drivers_monitor": []
        })

    monday, sunday = _v5_week_bounds()

    # new safe table counts
    drivers_week = _v5_count(
        f"/agent_registrations?select=id&agent_auth_id=eq.{uid}&subject_type=eq.driver"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
    )
    clients_week = _v5_count(
        f"/agent_registrations?select=id&agent_auth_id=eq.{uid}&subject_type=eq.client"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
    )
    drivers_all = _v5_count(
        f"/agent_registrations?select=id&agent_auth_id=eq.{uid}&subject_type=eq.driver"
    )

    # merge in legacy old data if exists
    aid = profile.get("id")
    if aid:
        drivers_week = max(drivers_week, _v5_count(
            f"/drivers?select=id&recruiter_agent_id=eq.{aid}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
        ))
        clients_week = max(clients_week, _v5_count(
            f"/clients?select=id&recruiter_agent_id=eq.{aid}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
        ))
        drivers_all = max(drivers_all, _v5_count(
            f"/drivers?select=id&recruiter_agent_id=eq.{aid}"
        ))

    # wallet rows
    r_wallet = requests.get(
        _v5_rest(f"/agent_wallet_ledger?select=entry_type,amount,week_start,week_end,reference,note,created_at&agent_auth_id=eq.{uid}&order=created_at.desc&limit=200"),
        headers=_v5_headers(),
        timeout=10
    )
    wallet_rows = r_wallet.json() if r_wallet.status_code == 200 else []
    balance = 0.0
    for x in wallet_rows:
        amt = float(x.get("amount") or 0)
        balance += amt if x.get("entry_type") == "credit" else -amt

    # grouped invoices
    grouped = {}
    for x in wallet_rows:
        ws = x.get("week_start") or "NO_WEEK"
        we = x.get("week_end") or "NO_WEEK"
        key = f"{ws}|{we}"
        grouped.setdefault(key, {
            "week_start": ws,
            "week_end": we,
            "credits": 0.0,
            "debits": 0.0,
            "net": 0.0
        })
        amt = float(x.get("amount") or 0)
        if x.get("entry_type") == "credit":
            grouped[key]["credits"] += amt
            grouped[key]["net"] += amt
        else:
            grouped[key]["debits"] += amt
            grouped[key]["net"] -= amt
    invoices = list(grouped.values())
    invoices.sort(key=lambda x: x["week_start"] or "", reverse=True)

    # activity (new table)
    r_act = requests.get(
        _v5_rest(f"/agent_registrations?select=subject_type,full_name,phone,town,external_code,created_at&agent_auth_id=eq.{uid}&order=created_at.desc&limit=40"),
        headers=_v5_headers(),
        timeout=10
    )
    activity = r_act.json() if r_act.status_code == 200 else []

    # if no activity in new table, add legacy rows
    if aid:
        r_d = requests.get(
            _v5_rest(f"/drivers?select=full_name,phone,town,created_at&recruiter_agent_id=eq.{aid}&order=created_at.desc&limit=20"),
            headers=_v5_headers(),
            timeout=10
        )
        if r_d.status_code == 200:
            for x in r_d.json():
                activity.append({
                    "subject_type": "driver",
                    "full_name": x.get("full_name"),
                    "phone": x.get("phone"),
                    "town": x.get("town"),
                    "external_code": "",
                    "created_at": x.get("created_at")
                })

        r_c = requests.get(
            _v5_rest(f"/clients?select=full_name,phone,created_at&recruiter_agent_id=eq.{aid}&order=created_at.desc&limit=20"),
            headers=_v5_headers(),
            timeout=10
        )
        if r_c.status_code == 200:
            for x in r_c.json():
                activity.append({
                    "subject_type": "client",
                    "full_name": x.get("full_name"),
                    "phone": x.get("phone"),
                    "town": "",
                    "external_code": "",
                    "created_at": x.get("created_at")
                })

    activity.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    activity = activity[:40]

    # driver monitor
    drivers_monitor = []
    if aid:
        r1 = requests.get(
            _v5_rest(f"/drivers?select=full_name,phone,town,created_at&recruiter_agent_id=eq.{aid}&order=created_at.desc&limit=200"),
            headers=_v5_headers(),
            timeout=10
        )
        legacy_drivers = r1.json() if r1.status_code == 200 else []

        r2 = requests.get(
            _v5_rest(f"/agent_driver_trip_updates?select=driver_phone,driver_name,trips,bonus_amount,week_start,week_end,admin_note&agent_auth_id=eq.{uid}&week_start=eq.{monday.isoformat()}&limit=300"),
            headers=_v5_headers(),
            timeout=10
        )
        trip_rows = r2.json() if r2.status_code == 200 else []
        trip_map = {(x.get("driver_phone") or ""): x for x in trip_rows}

        for d in legacy_drivers:
            phone = d.get("phone") or ""
            t = trip_map.get(phone, {})
            drivers_monitor.append({
                "full_name": d.get("full_name"),
                "phone": phone,
                "town": d.get("town"),
                "registered_at": d.get("created_at"),
                "trips_this_week": t.get("trips", 0),
                "bonus_amount": t.get("bonus_amount", 0),
                "admin_note": t.get("admin_note", "")
            })

    return jsonify({
        "ok": True,
        "profile": profile,
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "summary": {
            "drivers_week": drivers_week,
            "clients_week": clients_week,
            "drivers_all": drivers_all
        },
        "wallet": {"balance": balance, "rows": wallet_rows},
        "invoices": invoices,
        "activity": activity,
        "drivers_monitor": drivers_monitor
    })

@app.post("/api/agent/register_driver_v5")
def api_agent_register_driver_v5():
    user = _v5_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    uid = user.get("id")
    profile = _v5_find_profile(uid)
    if not profile:
        return jsonify({"ok": False, "error": "No linked agent profile"}), 400

    j = request.get_json(force=True) or {}
    full_name = (j.get("full_name") or "").strip()
    phone = (j.get("phone") or "").strip()
    town = (j.get("town") or "").strip()
    driver_code = (j.get("driver_code") or "").strip()

    if not full_name or not phone or not driver_code:
        return jsonify({"ok": False, "error": "Missing full_name / phone / driver_code"}), 400

    payload = {
        "agent_auth_id": uid,
        "subject_type": "driver",
        "full_name": full_name,
        "phone": phone,
        "town": town,
        "external_code": driver_code
    }
    r = requests.post(_v5_rest("/agent_registrations"), headers=_v5_headers(), json=payload, timeout=10)
    if r.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r.text}), 500

    return jsonify({"ok": True, "row": r.json()[0]})

@app.post("/api/agent/register_client_v5")
def api_agent_register_client_v5():
    user = _v5_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    uid = user.get("id")
    profile = _v5_find_profile(uid)
    if not profile:
        return jsonify({"ok": False, "error": "No linked agent profile"}), 400

    j = request.get_json(force=True) or {}
    full_name = (j.get("full_name") or "").strip()
    phone = (j.get("phone") or "").strip()
    town = (j.get("town") or "").strip()
    client_code = (j.get("client_code") or "").strip()

    if not full_name or not phone or not client_code:
        return jsonify({"ok": False, "error": "Missing full_name / phone / client_code"}), 400

    payload = {
        "agent_auth_id": uid,
        "subject_type": "client",
        "full_name": full_name,
        "phone": phone,
        "town": town,
        "external_code": client_code
    }
    r = requests.post(_v5_rest("/agent_registrations"), headers=_v5_headers(), json=payload, timeout=10)
    if r.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r.text}), 500

    return jsonify({"ok": True, "row": r.json()[0]})

@app.get("/api/agent/invoice_csv_v5")
def api_agent_invoice_csv_v5():
    user = _v5_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    uid = user.get("id")
    week_start = (request.args.get("week_start") or "").strip()
    if not week_start:
        return jsonify({"ok": False, "error": "Missing week_start"}), 400

    r = requests.get(
        _v5_rest(f"/agent_wallet_ledger?select=created_at,week_start,week_end,entry_type,amount,reference,note&agent_auth_id=eq.{uid}&week_start=eq.{week_start}&order=created_at.asc"),
        headers=_v5_headers(),
        timeout=10
    )
    rows = r.json() if r.status_code == 200 else []

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["created_at","week_start","week_end","entry_type","amount","reference","note"])
    for x in rows:
        w.writerow([x.get("created_at"), x.get("week_start"), x.get("week_end"), x.get("entry_type"), x.get("amount"), x.get("reference"), x.get("note")])

    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=agent_invoice_{week_start}.csv"})
'''

if "AGENT V5 HARD RESET" not in s:
    if "if __name__" in s:
        s = s.replace("if __name__", "\n" + block + "\n\nif __name__", 1)
    else:
        s += "\n" + block
    p.write_text(s, encoding="utf-8")
    print("✅ appended V5 routes to app.py")
else:
    print("✅ V5 routes already present")
PY

# 5) replace agent dashboard with simpler V5 version
cat > templates/agent_dashboard.html <<'EOF'
{% extends "base.html" %}
{% block inner %}
<div class="wrap">
  <div class="topbar">
    <div>
      <div class="muted">🌐 YENE Portal</div>
      <h2 id="welcome" style="margin:6px 0">Welcome, Agent</h2>
      <div class="muted" id="weekLabel">Loading…</div>
    </div>
    <div style="display:flex;gap:10px">
      <button id="retryBtn" type="button">Retry</button>
      <button id="signOutBtn" type="button">Sign out</button>
    </div>
  </div>

  <div id="statusCard" class="card"><div id="statusText">Checking your session…</div></div>

  <div id="dashboardArea" style="display:none">
    <div class="card">
      <div class="row">
        <div style="flex:1;min-width:180px"><div class="muted">Drivers registered (this week)</div><div id="driversWeek" style="font-size:28px;font-weight:800">0</div></div>
        <div style="flex:1;min-width:180px"><div class="muted">Clients registered (this week)</div><div id="clientsWeek" style="font-size:28px;font-weight:800">0</div></div>
        <div style="flex:1;min-width:180px"><div class="muted">Drivers (all-time)</div><div id="driversAll" style="font-size:28px;font-weight:800">0</div></div>
        <div style="flex:1;min-width:180px"><div class="muted">Wallet balance</div><div id="walletBalance" style="font-size:28px;font-weight:800">0.00</div></div>
      </div>
    </div>

    <div class="row">
      <div class="card" style="flex:1;min-width:320px">
        <h3 style="margin-top:0">Register Driver</h3>
        <div class="row">
          <div style="flex:1;min-width:140px"><input id="dName" placeholder="Full name"></div>
          <div style="flex:1;min-width:140px"><input id="dPhone" placeholder="+264..."></div>
        </div>
        <div class="row">
          <div style="flex:1;min-width:140px"><input id="dTown" placeholder="Town"></div>
          <div style="flex:1;min-width:140px"><input id="dCode" placeholder="Driver code"></div>
        </div>
        <button id="btnDriver" type="button">Submit Driver</button>
        <div id="driverMsg" class="muted" style="margin-top:8px"></div>
      </div>

      <div class="card" style="flex:1;min-width:320px">
        <h3 style="margin-top:0">Register Client</h3>
        <div class="row">
          <div style="flex:1;min-width:140px"><input id="cName" placeholder="Full name"></div>
          <div style="flex:1;min-width:140px"><input id="cPhone" placeholder="+264..."></div>
        </div>
        <div class="row">
          <div style="flex:1;min-width:140px"><input id="cTown" placeholder="Town"></div>
          <div style="flex:1;min-width:140px"><input id="cCode" placeholder="Client code"></div>
        </div>
        <button id="btnClient" type="button">Submit Client</button>
        <div id="clientMsg" class="muted" style="margin-top:8px"></div>
      </div>
    </div>

    <div class="card">
      <h3 style="margin-top:0">Previous weeks / invoices</h3>
      <table>
        <thead><tr><th>Week</th><th>Credits</th><th>Debits</th><th>Net</th><th>Download</th></tr></thead>
        <tbody id="invoiceBody"><tr><td colspan="5" class="muted">No invoices yet.</td></tr></tbody>
      </table>
    </div>

    <div class="card">
      <h3 style="margin-top:0">Recent activity</h3>
      <table>
        <thead><tr><th>Type</th><th>Name</th><th>Phone</th><th>Town</th><th>Code</th><th>Date</th></tr></thead>
        <tbody id="activityBody"><tr><td colspan="6" class="muted">No registrations yet.</td></tr></tbody>
      </table>
    </div>
  </div>
</div>

<script>
(async () => {
  const statusText = document.getElementById("statusText");
  const statusCard = document.getElementById("statusCard");
  const dashboardArea = document.getElementById("dashboardArea");

  async function waitForSession(sb, ms=8000){
    const start = Date.now();
    while(Date.now() - start < ms){
      const { data } = await sb.auth.getSession();
      if(data && data.session) return data.session;
      await new Promise(r => setTimeout(r, 200));
    }
    return null;
  }

  async function bearer(sb){
    const { data } = await sb.auth.getSession();
    return data?.session?.access_token ? ("Bearer " + data.session.access_token) : null;
  }

  async function api(sb, path, opts={}){
    const b = await bearer(sb);
    if(!b) return { ok:false, error:"No session token" };
    opts.headers = Object.assign({}, opts.headers || {}, {
      "Authorization": b,
      "Content-Type": "application/json"
    });
    const r = await fetch(path, opts);
    const txt = await r.text();
    try { return JSON.parse(txt); } catch(e){ return { ok:false, error:"bad json", raw: txt }; }
  }

  async function loadDashboard(){
    statusCard.style.display = "block";
    dashboardArea.style.display = "none";
    statusText.textContent = "Checking your session…";

    const sb = await window.sbReady;
    if(!sb){
      statusText.textContent = "Supabase client not ready.";
      return;
    }

    const session = await waitForSession(sb, 8000);
    if(!session){
      statusText.innerHTML = 'No active session found. Please <a href="/login">login again</a>.';
      return;
    }

    const boot = await api(sb, "/api/agent/bootstrap_v5");
    if(!boot || !boot.ok){
      statusText.textContent = "Could not load your dashboard.";
      return;
    }

    const profile = boot.profile || null;
    if(!profile){
      document.getElementById("welcome").textContent = "Welcome, New Agent";
      document.getElementById("weekLabel").textContent = "No data yet. Waiting for your first registration.";
      statusText.textContent = "Your account is active but no linked profile data exists yet.";
      dashboardArea.style.display = "block";
      return;
    }

    document.getElementById("welcome").textContent =
      "Welcome, " + (profile.full_name || profile.username || profile.email || "Agent");

    document.getElementById("weekLabel").textContent =
      "Week: " + (boot.week_start || "") + " → " + (boot.week_end || "");
    document.getElementById("driversWeek").textContent = boot.summary?.drivers_week || 0;
    document.getElementById("clientsWeek").textContent = boot.summary?.clients_week || 0;
    document.getElementById("driversAll").textContent = boot.summary?.drivers_all || 0;
    document.getElementById("walletBalance").textContent = Number(boot.wallet?.balance || 0).toFixed(2);

    const invoiceBody = document.getElementById("invoiceBody");
    invoiceBody.innerHTML = "";
    const invRows = boot.invoices || [];
    if(invRows.length === 0){
      invoiceBody.innerHTML = '<tr><td colspan="5" class="muted">No invoices yet.</td></tr>';
    } else {
      for(const x of invRows){
        invoiceBody.insertAdjacentHTML("beforeend", `
          <tr>
            <td>${x.week_start || ""} → ${x.week_end || ""}</td>
            <td>${Number(x.credits || 0).toFixed(2)}</td>
            <td>${Number(x.debits || 0).toFixed(2)}</td>
            <td>${Number(x.net || 0).toFixed(2)}</td>
            <td><a href="/api/agent/invoice_csv_v5?week_start=${encodeURIComponent(x.week_start || "")}" target="_blank">Download</a></td>
          </tr>
        `);
      }
    }

    const body = document.getElementById("activityBody");
    body.innerHTML = "";
    const rows = boot.activity || [];
    if(rows.length === 0){
      body.innerHTML = '<tr><td colspan="6" class="muted">No registrations yet.</td></tr>';
    } else {
      for(const r of rows){
        body.insertAdjacentHTML("beforeend", `
          <tr>
            <td>${r.subject_type || ""}</td>
            <td>${r.full_name || ""}</td>
            <td>${r.phone || ""}</td>
            <td>${r.town || ""}</td>
            <td>${r.external_code || ""}</td>
            <td>${r.created_at ? new Date(r.created_at).toLocaleString() : ""}</td>
          </tr>
        `);
      }
    }

    statusCard.style.display = "none";
    dashboardArea.style.display = "block";

    document.getElementById("btnDriver").onclick = async () => {
      document.getElementById("driverMsg").textContent = "Saving…";
      const payload = {
        full_name: document.getElementById("dName").value,
        phone: document.getElementById("dPhone").value,
        town: document.getElementById("dTown").value,
        driver_code: document.getElementById("dCode").value
      };
      const res = await api(sb, "/api/agent/register_driver_v5", { method:"POST", body: JSON.stringify(payload) });
      document.getElementById("driverMsg").textContent = res && res.ok ? "✅ Driver registered." : ("❌ " + (res?.error || "Failed"));
      if(res && res.ok) await loadDashboard();
    };

    document.getElementById("btnClient").onclick = async () => {
      document.getElementById("clientMsg").textContent = "Saving…";
      const payload = {
        full_name: document.getElementById("cName").value,
        phone: document.getElementById("cPhone").value,
        town: document.getElementById("cTown").value,
        client_code: document.getElementById("cCode").value
      };
      const res = await api(sb, "/api/agent/register_client_v5", { method:"POST", body: JSON.stringify(payload) });
      document.getElementById("clientMsg").textContent = res && res.ok ? "✅ Client registered." : ("❌ " + (res?.error || "Failed"));
      if(res && res.ok) await loadDashboard();
    };
  }

  document.getElementById("retryBtn").onclick = () => loadDashboard();
  document.getElementById("signOutBtn").onclick = async () => {
    const sb = await window.sbReady;
    if (sb) await sb.auth.signOut();
    location.href = "/login";
  };

  await loadDashboard();
})();
</script>
{% endblock %}
EOF

python3 -m py_compile app.py && echo "✅ app.py compiles"

python3 - <<'PY'
from app import app
targets = [
    "/api/agent/bootstrap_v5",
    "/api/agent/register_driver_v5",
    "/api/agent/register_client_v5",
    "/api/agent/invoice_csv_v5",
]
print("ROUTE CHECK:")
rules = {r.rule for r in app.url_map.iter_rules()}
for t in targets:
    print(t, "->", "OK" if t in rules else "MISSING")
PY

git add app.py templates/base_public.html templates/base.html templates/login.html templates/agent_dashboard.html
git commit -m "Hard reset to shared session and single bootstrap-driven agent dashboard" || true
git push origin main
