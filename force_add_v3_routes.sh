set -euo pipefail

python3 - <<'PY'
from pathlib import Path

p = Path("app.py")
s = p.read_text(encoding="utf-8")

block = r'''

# =========================
# FORCE-ADDED AGENT V3 ROUTES
# =========================
import os
import io
import csv
import datetime
import requests
from flask import request, jsonify, Response

def _fa_env(name, default=""):
    return (os.getenv(name, default) or "").strip()

_FA_SUPABASE_URL = _fa_env("SUPABASE_URL")
_FA_SUPABASE_ANON = _fa_env("SUPABASE_ANON_KEY")
_FA_SUPABASE_SERVICE = _fa_env("SUPABASE_SERVICE_ROLE_KEY")

def _fa_rest(path):
    return _FA_SUPABASE_URL.rstrip("/") + "/rest/v1" + path

def _fa_headers():
    return {
        "apikey": _FA_SUPABASE_SERVICE,
        "Authorization": f"Bearer {_FA_SUPABASE_SERVICE}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def _fa_verify_bearer():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        r = requests.get(
            _FA_SUPABASE_URL.rstrip("/") + "/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": _FA_SUPABASE_ANON
            },
            timeout=10
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def _fa_find_profile(uid=None, email=None):
    if uid:
        q = f"/agent_profiles?select=*&or=(auth_id.eq.{uid},user_id.eq.{uid})&limit=1"
        r = requests.get(_fa_rest(q), headers=_fa_headers(), timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()[0]
    if email:
        q = f"/agent_profiles?select=*&email=eq.{email}&limit=1"
        r = requests.get(_fa_rest(q), headers=_fa_headers(), timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()[0]
    return None

def _fa_week_bounds():
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday

@app.get("/api/agent/me_v3")
def api_agent_me_v3():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    uid = user.get("id")
    email = user.get("email")
    prof = _fa_find_profile(uid, email)
    return jsonify({"ok": True, "user_id": uid, "email": email, "profile": prof})

@app.get("/api/agent/summary_v3")
def api_agent_summary_v3():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _fa_find_profile(uid, email)
    monday, sunday = _fa_week_bounds()

    def _count(path):
        r = requests.get(_fa_rest(path), headers=_fa_headers(), timeout=10)
        if r.status_code == 200:
            return len(r.json())
        return 0

    drivers_week = _count(
        f"/agent_registrations?select=id&agent_auth_id=eq.{uid}&subject_type=eq.driver"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
    )
    clients_week = _count(
        f"/agent_registrations?select=id&agent_auth_id=eq.{uid}&subject_type=eq.client"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
    )
    drivers_all = _count(
        f"/agent_registrations?select=id&agent_auth_id=eq.{uid}&subject_type=eq.driver"
    )

    if prof and prof.get("id"):
        aid = prof["id"]
        drivers_week = max(drivers_week, _count(
            f"/drivers?select=id&recruiter_agent_id=eq.{aid}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
        ))
        clients_week = max(clients_week, _count(
            f"/clients?select=id&recruiter_agent_id=eq.{aid}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
        ))
        drivers_all = max(drivers_all, _count(
            f"/drivers?select=id&recruiter_agent_id=eq.{aid}"
        ))

    return jsonify({
        "ok": True,
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "drivers_week": drivers_week,
        "clients_week": clients_week,
        "drivers_all": drivers_all,
        "profile": prof
    })

@app.get("/api/agent/activity_v3")
def api_agent_activity_v3():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _fa_find_profile(uid, email)
    rows = []

    r = requests.get(
        _fa_rest(f"/agent_registrations?select=subject_type,full_name,phone,town,external_code,created_at&agent_auth_id=eq.{uid}&order=created_at.desc&limit=30"),
        headers=_fa_headers(),
        timeout=10
    )
    if r.status_code == 200:
        rows.extend(r.json())

    if prof and prof.get("id"):
        aid = prof["id"]
        r1 = requests.get(
            _fa_rest(f"/drivers?select=full_name,phone,town,created_at&recruiter_agent_id=eq.{aid}&order=created_at.desc&limit=20"),
            headers=_fa_headers(),
            timeout=10
        )
        if r1.status_code == 200:
            for x in r1.json():
                rows.append({
                    "subject_type": "driver",
                    "full_name": x.get("full_name"),
                    "phone": x.get("phone"),
                    "town": x.get("town"),
                    "external_code": "",
                    "created_at": x.get("created_at")
                })

        r2 = requests.get(
            _fa_rest(f"/clients?select=full_name,phone,created_at&recruiter_agent_id=eq.{aid}&order=created_at.desc&limit=20"),
            headers=_fa_headers(),
            timeout=10
        )
        if r2.status_code == 200:
            for x in r2.json():
                rows.append({
                    "subject_type": "client",
                    "full_name": x.get("full_name"),
                    "phone": x.get("phone"),
                    "town": "",
                    "external_code": "",
                    "created_at": x.get("created_at")
                })

    rows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return jsonify({"ok": True, "rows": rows[:40], "profile": prof})

@app.post("/api/agent/register_driver_v3")
def api_agent_register_driver_v3():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _fa_find_profile(uid, email)
    if not prof:
        return jsonify({"ok": False, "error": "Agent profile not linked to auth account"}), 400

    j = request.get_json(force=True) or {}
    full_name = (j.get("full_name") or "").strip()
    phone = (j.get("phone") or "").strip()
    town = (j.get("town") or "").strip()
    code = (j.get("driver_code") or "").strip()

    if not full_name or not phone or not code:
        return jsonify({"ok": False, "error": "Missing full_name / phone / driver_code"}), 400

    payload = {
        "agent_auth_id": uid,
        "subject_type": "driver",
        "full_name": full_name,
        "phone": phone,
        "town": town,
        "external_code": code
    }
    r = requests.post(_fa_rest("/agent_registrations"), headers=_fa_headers(), json=payload, timeout=10)
    if r.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r.text}), 500

    # optional legacy mirror
    try:
        old_payload = {
            "recruiter_agent_id": prof["id"],
            "recruiter_auth_id": uid,
            "recruiter_name": prof.get("full_name") or prof.get("username") or prof.get("email"),
            "full_name": full_name,
            "phone": phone,
            "town": town
        }
        requests.post(_fa_rest("/drivers"), headers=_fa_headers(), json=old_payload, timeout=10)
    except Exception:
        pass

    return jsonify({"ok": True, "row": r.json()[0]})

@app.post("/api/agent/register_client_v3")
def api_agent_register_client_v3():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _fa_find_profile(uid, email)
    if not prof:
        return jsonify({"ok": False, "error": "Agent profile not linked to auth account"}), 400

    j = request.get_json(force=True) or {}
    full_name = (j.get("full_name") or "").strip()
    phone = (j.get("phone") or "").strip()
    town = (j.get("town") or "").strip()
    code = (j.get("client_code") or "").strip()

    if not full_name or not phone or not code:
        return jsonify({"ok": False, "error": "Missing full_name / phone / client_code"}), 400

    payload = {
        "agent_auth_id": uid,
        "subject_type": "client",
        "full_name": full_name,
        "phone": phone,
        "town": town,
        "external_code": code
    }
    r = requests.post(_fa_rest("/agent_registrations"), headers=_fa_headers(), json=payload, timeout=10)
    if r.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r.text}), 500

    try:
        old_payload = {
            "recruiter_agent_id": prof["id"],
            "recruiter_auth_id": uid,
            "recruiter_name": prof.get("full_name") or prof.get("username") or prof.get("email"),
            "full_name": full_name,
            "phone": phone
        }
        requests.post(_fa_rest("/clients"), headers=_fa_headers(), json=old_payload, timeout=10)
    except Exception:
        pass

    return jsonify({"ok": True, "row": r.json()[0]})

@app.get("/api/agent/wallet_v3")
def api_agent_wallet_v3():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    uid = user.get("id")
    r = requests.get(
        _fa_rest(f"/agent_wallet_ledger?select=entry_type,amount,week_start,week_end,reference,note,created_at&agent_auth_id=eq.{uid}&order=created_at.desc&limit=200"),
        headers=_fa_headers(),
        timeout=10
    )
    rows = r.json() if r.status_code == 200 else []
    balance = 0
    for x in rows:
        amt = float(x.get("amount") or 0)
        balance += amt if x.get("entry_type") == "credit" else -amt
    return jsonify({"ok": True, "balance": balance, "rows": rows})

@app.get("/api/agent/invoices_v3")
def api_agent_invoices_v3():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    uid = user.get("id")
    r = requests.get(
        _fa_rest(f"/agent_wallet_ledger?select=week_start,week_end,entry_type,amount,reference,note,created_at&agent_auth_id=eq.{uid}&order=week_start.desc,created_at.desc&limit=300"),
        headers=_fa_headers(),
        timeout=10
    )
    rows = r.json() if r.status_code == 200 else []
    grouped = {}
    for x in rows:
        ws = x.get("week_start") or "NO_WEEK"
        we = x.get("week_end") or "NO_WEEK"
        key = f"{ws}|{we}"
        grouped.setdefault(key, {"week_start": ws, "week_end": we, "credits": 0, "debits": 0, "net": 0, "items": []})
        amt = float(x.get("amount") or 0)
        if x.get("entry_type") == "credit":
            grouped[key]["credits"] += amt
            grouped[key]["net"] += amt
        else:
            grouped[key]["debits"] += amt
            grouped[key]["net"] -= amt
        grouped[key]["items"].append(x)
    invoices = list(grouped.values())
    invoices.sort(key=lambda x: x["week_start"] or "", reverse=True)
    return jsonify({"ok": True, "rows": invoices})

@app.get("/api/agent/invoice_csv_v3")
def api_agent_invoice_csv_v3():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    uid = user.get("id")
    week_start = (request.args.get("week_start") or "").strip()
    if not week_start:
        return jsonify({"ok": False, "error": "Missing week_start"}), 400

    r = requests.get(
        _fa_rest(f"/agent_wallet_ledger?select=created_at,week_start,week_end,entry_type,amount,reference,note&agent_auth_id=eq.{uid}&week_start=eq.{week_start}&order=created_at.asc"),
        headers=_fa_headers(),
        timeout=10
    )
    rows = r.json() if r.status_code == 200 else []

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["created_at","week_start","week_end","entry_type","amount","reference","note"])
    for x in rows:
        w.writerow([x.get("created_at"), x.get("week_start"), x.get("week_end"), x.get("entry_type"), x.get("amount"), x.get("reference"), x.get("note")])

    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=agent_invoice_{week_start}.csv"})

@app.get("/api/agent/drivers_monitor_v3")
def api_agent_drivers_monitor_v3():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _fa_find_profile(uid, email)
    if not prof:
        return jsonify({"ok": True, "rows": []})

    aid = prof.get("id")
    monday, sunday = _fa_week_bounds()

    r1 = requests.get(
        _fa_rest(f"/drivers?select=full_name,phone,town,created_at&recruiter_agent_id=eq.{aid}&order=created_at.desc&limit=200"),
        headers=_fa_headers(),
        timeout=10
    )
    drivers = r1.json() if r1.status_code == 200 else []

    r2 = requests.get(
        _fa_rest(f"/agent_driver_trip_updates?select=driver_phone,driver_name,trips,bonus_amount,week_start,week_end,admin_note&agent_auth_id=eq.{uid}&week_start=eq.{monday.isoformat()}&limit=300"),
        headers=_fa_headers(),
        timeout=10
    )
    trip_rows = r2.json() if r2.status_code == 200 else []
    trip_map = {(x.get("driver_phone") or ""): x for x in trip_rows}

    rows = []
    for d in drivers:
        phone = d.get("phone") or ""
        t = trip_map.get(phone, {})
        rows.append({
            "full_name": d.get("full_name"),
            "phone": phone,
            "town": d.get("town"),
            "registered_at": d.get("created_at"),
            "trips_this_week": t.get("trips", 0),
            "bonus_amount": t.get("bonus_amount", 0),
            "admin_note": t.get("admin_note", "")
        })

    return jsonify({"ok": True, "week_start": monday.isoformat(), "week_end": sunday.isoformat(), "rows": rows})
'''

if "FORCE-ADDED AGENT V3 ROUTES" not in s:
    s += "\n" + block + "\n"
    p.write_text(s, encoding="utf-8")
    print("✅ appended V3 routes to app.py")
else:
    print("✅ V3 routes already present in app.py")
PY

python3 - <<'PY'
from app import app
targets = [
    "/api/agent/me_v3",
    "/api/agent/summary_v3",
    "/api/agent/activity_v3",
    "/api/agent/register_driver_v3",
    "/api/agent/register_client_v3",
    "/api/agent/wallet_v3",
    "/api/agent/invoices_v3",
    "/api/agent/invoice_csv_v3",
    "/api/agent/drivers_monitor_v3",
]
print("ROUTE CHECK:")
rules = {r.rule for r in app.url_map.iter_rules()}
for t in targets:
    print(t, "->", "OK" if t in rules else "MISSING")
PY

git add app.py
git commit -m "Force-add V3 agent API routes" || true
git push origin main
