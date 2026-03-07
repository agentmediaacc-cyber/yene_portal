set -euo pipefail

echo "==> Patching app.py to use legacy data + working inserts..."

python3 - <<'PY'
from pathlib import Path
import re

p = Path("app.py")
s = p.read_text(encoding="utf-8")

marker = "### === CORE AGENT PORTAL API (V2 FIX) ==="
if marker in s:
    print("✅ V2 FIX already present")
    raise SystemExit(0)

block = r'''
### === CORE AGENT PORTAL API (V2 FIX) ===

def _sr_get(path):
    return requests.get(sr_postgrest(path), headers=sr_headers(), timeout=15)

def _sr_post(path, payload):
    return requests.post(sr_postgrest(path), headers=sr_headers(), json=payload, timeout=15)

def _find_agent_profile(uid=None, email=None):
    # 1) auth_id/user_id match
    if uid:
        q = f"/agent_profiles?select=*&or=(auth_id.eq.{uid},user_id.eq.{uid})&limit=1"
        r = _sr_get(q)
        if r.status_code == 200 and r.json():
            return r.json()[0]

    # 2) fallback email match
    if email:
        q = f"/agent_profiles?select=*&email=eq.{email}&limit=1"
        r = _sr_get(q)
        if r.status_code == 200 and r.json():
            return r.json()[0]

    return None

def _week_iso():
    monday, sunday = week_bounds_local()
    return monday.isoformat(), sunday.isoformat()

@app.get("/api/agent/me_v2")
@require_supabase_user
def api_agent_me_v2(user):
    uid = user["id"]
    email = user.get("email")
    prof = _find_agent_profile(uid, email)
    return jsonify({
        "ok": True,
        "auth_id": uid,
        "email": email,
        "profile": prof
    })

@app.get("/api/agent/summary_v2")
@require_supabase_user
def api_agent_summary_v2(user):
    uid = user["id"]
    email = user.get("email")
    prof = _find_agent_profile(uid, email)
    agent_profile_id = (prof or {}).get("id")
    monday, sunday = week_bounds_local()

    drivers_week = 0
    clients_week = 0
    drivers_all = 0

    # ---- OLD LEGACY TABLES FIRST (real existing data) ----
    if agent_profile_id:
        qd_week = (
            f"/drivers?select=id"
            f"&recruiter_agent_id=eq.{agent_profile_id}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
        )
        rc = _sr_get(qd_week)
        if rc.status_code == 200:
            drivers_week += len(rc.json())

        qc_week = (
            f"/clients?select=id"
            f"&recruiter_agent_id=eq.{agent_profile_id}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
        )
        rc2 = _sr_get(qc_week)
        if rc2.status_code == 200:
            clients_week += len(rc2.json())

        qd_all = f"/drivers?select=id&recruiter_agent_id=eq.{agent_profile_id}"
        rc3 = _sr_get(qd_all)
        if rc3.status_code == 200:
            drivers_all += len(rc3.json())

    # fallback by recruiter_auth_id if old rows were saved that way
    qd_auth_week = (
        f"/drivers?select=id"
        f"&recruiter_auth_id=eq.{uid}"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
    )
    rr = _sr_get(qd_auth_week)
    if rr.status_code == 200:
        drivers_week = max(drivers_week, len(rr.json()))

    qc_auth_week = (
        f"/clients?select=id"
        f"&recruiter_auth_id=eq.{uid}"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
    )
    rr2 = _sr_get(qc_auth_week)
    if rr2.status_code == 200:
        clients_week = max(clients_week, len(rr2.json()))

    qd_auth_all = f"/drivers?select=id&recruiter_auth_id=eq.{uid}"
    rr3 = _sr_get(qd_auth_all)
    if rr3.status_code == 200:
        drivers_all = max(drivers_all, len(rr3.json()))

    # ---- NEW TABLE (if exists) ----
    q_new_d = (
        f"/agent_registrations?select=id"
        f"&agent_auth_id=eq.{uid}"
        f"&subject_type=eq.driver"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
    )
    rnew = _sr_get(q_new_d)
    if rnew.status_code == 200:
        drivers_week = max(drivers_week, len(rnew.json()))

    q_new_c = (
        f"/agent_registrations?select=id"
        f"&agent_auth_id=eq.{uid}"
        f"&subject_type=eq.client"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
    )
    rnew2 = _sr_get(q_new_c)
    if rnew2.status_code == 200:
        clients_week = max(clients_week, len(rnew2.json()))

    q_new_all = f"/agent_registrations?select=id&agent_auth_id=eq.{uid}&subject_type=eq.driver"
    rnew3 = _sr_get(q_new_all)
    if rnew3.status_code == 200:
        drivers_all = max(drivers_all, len(rnew3.json()))

    return jsonify({
        "ok": True,
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "drivers_week": drivers_week,
        "clients_week": clients_week,
        "drivers_all": drivers_all,
        "profile": prof
    })

@app.get("/api/agent/activity_v2")
@require_supabase_user
def api_agent_activity_v2(user):
    uid = user["id"]
    email = user.get("email")
    prof = _find_agent_profile(uid, email)
    agent_profile_id = (prof or {}).get("id")
    monday, sunday = week_bounds_local()

    rows = []

    # old drivers
    if agent_profile_id:
        q1 = (
            f"/drivers?select=full_name,phone,town,created_at"
            f"&recruiter_agent_id=eq.{agent_profile_id}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
            f"&order=created_at.desc&limit=20"
        )
        r1 = _sr_get(q1)
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

        q2 = (
            f"/clients?select=full_name,phone,created_at"
            f"&recruiter_agent_id=eq.{agent_profile_id}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
            f"&order=created_at.desc&limit=20"
        )
        r2 = _sr_get(q2)
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

    # new rows
    q3 = (
        f"/agent_registrations?select=subject_type,full_name,phone,town,external_code,created_at"
        f"&agent_auth_id=eq.{uid}"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
        f"&order=created_at.desc&limit=20"
    )
    r3 = _sr_get(q3)
    if r3.status_code == 200:
        rows.extend(r3.json())

    rows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return jsonify({"ok": True, "rows": rows[:30], "profile": prof})

@app.post("/api/agent/register_driver_v2_working")
@require_supabase_user
def api_agent_register_driver_v2_working(user):
    uid = user["id"]
    email = user.get("email")
    prof = _find_agent_profile(uid, email)
    if not prof:
        return jsonify({"ok": False, "error": "Agent profile not linked to auth account"}), 400

    j = request.get_json(force=True) or {}
    full_name = (j.get("full_name") or "").strip()
    phone = (j.get("phone") or "").strip()
    town = (j.get("town") or "").strip()
    driver_code = (j.get("driver_code") or "").strip()

    if not full_name or not phone or not driver_code:
        return jsonify({"ok": False, "error": "Missing full_name / phone / driver_code"}), 400

    # save into OLD working drivers table
    payload_old = {
        "recruiter_agent_id": prof["id"],
        "recruiter_auth_id": uid,
        "recruiter_name": prof.get("full_name") or prof.get("username") or prof.get("email"),
        "full_name": full_name,
        "phone": phone,
        "town": town
    }
    r_old = _sr_post("/drivers", payload_old)
    if r_old.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r_old.text}), 500

    # best effort mirror into new table
    payload_new = {
        "agent_auth_id": uid,
        "subject_type": "driver",
        "full_name": full_name,
        "phone": phone,
        "town": town,
        "external_code": driver_code
    }
    _sr_post("/agent_registrations", payload_new)

    return jsonify({"ok": True, "row": r_old.json()[0], "profile": prof})

@app.post("/api/agent/register_client_v2_working")
@require_supabase_user
def api_agent_register_client_v2_working(user):
    uid = user["id"]
    email = user.get("email")
    prof = _find_agent_profile(uid, email)
    if not prof:
        return jsonify({"ok": False, "error": "Agent profile not linked to auth account"}), 400

    j = request.get_json(force=True) or {}
    full_name = (j.get("full_name") or "").strip()
    phone = (j.get("phone") or "").strip()
    town = (j.get("town") or "").strip()
    client_code = (j.get("client_code") or "").strip()

    if not full_name or not phone or not client_code:
        return jsonify({"ok": False, "error": "Missing full_name / phone / client_code"}), 400

    payload_old = {
        "recruiter_agent_id": prof["id"],
        "recruiter_auth_id": uid,
        "recruiter_name": prof.get("full_name") or prof.get("username") or prof.get("email"),
        "full_name": full_name,
        "phone": phone
    }
    r_old = _sr_post("/clients", payload_old)
    if r_old.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r_old.text}), 500

    payload_new = {
        "agent_auth_id": uid,
        "subject_type": "client",
        "full_name": full_name,
        "phone": phone,
        "town": town,
        "external_code": client_code
    }
    _sr_post("/agent_registrations", payload_new)

    return jsonify({"ok": True, "row": r_old.json()[0], "profile": prof})
'''
    s += "\n\n" + block + "\n"

p.write_text(s, encoding="utf-8")
print("✅ app.py V2 working APIs added")
PY

echo "==> 2) Patching agent dashboard to use the WORKING V2 APIs..."
python3 - <<'PY'
from pathlib import Path
p = Path("templates/agent_dashboard.html")
s = p.read_text(encoding="utf-8")

replacements = {
    "/api/agent/me_v1": "/api/agent/me_v2",
    "/api/agent/summary_v1": "/api/agent/summary_v2",
    "/api/agent/activity_v1": "/api/agent/activity_v2",
    "/api/agent/register_driver_v1": "/api/agent/register_driver_v2_working",
    "/api/agent/register_client_v1": "/api/agent/register_client_v2_working",
    'document.getElementById("welcome").textContent = "Welcome, " + name;':
    'document.getElementById("welcome").textContent = "Welcome, " + name;\n    if(me.profile){ console.log("PROFILE", me.profile); }',
    'document.getElementById("driversWeek").textContent = sum.drivers_week || 0;':
    'document.getElementById("driversWeek").textContent = sum.drivers_week || 0;',
}

for old, new in replacements.items():
    s = s.replace(old, new)

p.write_text(s, encoding="utf-8")
print("✅ templates/agent_dashboard.html switched to working V2 APIs")
PY

echo "==> 3) Compile check..."
python3 -m py_compile app.py

echo "==> 4) Commit + push..."
git add app.py templates/agent_dashboard.html
git commit -m "Fix agent dashboard: use legacy data + working registration insert APIs (V2)" || true
git push origin main

echo ""
echo "✅ DONE."
echo "After Render deploys, agent dashboard should:"
echo "- show agent name from agent_profiles"
echo "- show old existing data from drivers/clients"
echo "- allow working register driver/client"
echo "- show weekly activity"
