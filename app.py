from supabase import create_client, Client
from flask_cors import CORS
from flask import jsonify,  Flask, render_template, request, jsonify, session, flash, redirect, url_for, Response

# -----------------------------
# Supabase token auth (Agent API)
# -----------------------------
import os
import requests
from functools import wraps
from flask import request, jsonify

def _sb_get_user_id_from_token(access_token: str):
    """Validate Supabase access token and return user_id (uuid string) or None."""
    url = (os.getenv("SUPABASE_URL","").strip() + "/auth/v1/user")
    apikey = os.getenv("SUPABASE_ANON_KEY","").strip()
    if not url.strip() or not apikey.strip():
        return None

    try:
        r = requests.get(
            url,
            headers={
                "apikey": apikey,
                "Authorization": f"Bearer {access_token}",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get("id")
    except Exception:
        return None

def require_agent_token(fn):
    """Protect /api/agent/* using Supabase auth token instead of Flask session."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization","")
        if not auth.lower().startswith("bearer "):
            return jsonify({"ok": False, "error": "Missing Authorization Bearer token"}), 401
        token = auth.split(" ", 1)[1].strip()
        uid = _sb_get_user_id_from_token(token)
        if not uid:
            return jsonify({"ok": False, "error": "Invalid/expired token"}), 401

        # Attach for handlers to use
        request.sb_uid = uid
        return fn(*args, **kwargs)
    return wrapper

from functools import wraps
import requests
import os
SUPABASE_URL = os.getenv('SUPABASE_URL', '')
SUPABASE_ANON_KEY = os.getenv('SUPABASE_ANON_KEY', '')

from dotenv import load_dotenv
load_dotenv()

# --- 1. THE FOUNDATION & TOOLS ---
app = Flask(__name__)

# --- Inject Supabase env into all templates (agent login needs this) ---
import os
from flask import jsonify,  Flask

@app.context_processor
def inject_supabase_env():
    return {
        "SUPABASE_URL": os.getenv("SUPABASE_URL", ""),
        "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY", ""),
    }
# --- end inject ---

app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me")
CORS(app)

URL = os.getenv('SUPABASE_URL', 'https://kcxphxihykonzuagtgke.supabase.co')
ANON_KEY = os.getenv('SUPABASE_ANON_KEY', '')
SERVICE_KEY = os.getenv('SUPABASE_SERVICE_KEY', '')

supabase: Client = create_client(URL, ANON_KEY)
sb_admin: Client = create_client(URL, SERVICE_KEY) if SERVICE_KEY else supabase

# --- HELPER FUNCTIONS ---


def require_login(role_required):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):

    # PUBLIC_DASHBOARD_BYPASS (do not remove)
    if request.path in ['/agent/dashboard', '/dashboard/admin']:
        return f(*args, **kwargs)
            if "role" not in session or session.get("role") != role_required:
                flash(f"Access denied. {role_required} role required.")
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def _require_admin():
    role = session.get("role") or session.get("user_role")
    return str(role or "").upper() == "ADMIN"


def log_system_event(event_type, description, user_id=None):
    try:
        sb_admin.table("system_logs").insert({
            "event_type": event_type,
            "description": description,
            "user_id": user_id,
            "ip_address": request.remote_addr
        }).execute()
    except Exception:
        pass

# --- 2. YOUR ROUTES ---


@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------
# AUTH + DASHBOARD ROUTES
# ---------------------------

@app.route("/admin/login", methods=["GET"])
def admin_login():
    return render_template("login.html", SUPABASE_URL=SUPABASE_URL, SUPABASE_ANON_KEY=SUPABASE_ANON_KEY, login_mode="admin")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        login_mode = (request.form.get("login_mode") or "agent").strip().lower()

        try:
            if login_mode == "admin":
                admin_check = supabase.table("admin_profiles").select("email").eq("email", email).execute()
                if not admin_check.data:
                    flash("Admin not found.")
                    return redirect(url_for("admin_login"))

                supabase.auth.sign_in_with_password({"email": email, "password": password})
                session.clear()
                session["role"] = "ADMIN"
                session["email"] = email
                log_system_event("LOGIN", f"ADMIN logged in: {email}", user_id=email)
                return redirect(url_for("admin_dashboard"))

            # AGENT login
            agent_check = supabase.table("agent_profiles").select("email, status").eq("email", email).execute()
            if not agent_check.data:
                agent_check = supabase.table("agents").select("email, status").eq("email", email).execute()

            if not agent_check.data:
                flash("Agent account not found.")
                return redirect(url_for("login"))

            status = (agent_check.data[0].get("status") or "").lower()
            supabase.auth.sign_in_with_password({"email": email, "password": password})

            session.clear()
            session["role"] = "AGENT"
            session["email"] = email
            session["status"] = status
            log_system_event("LOGIN", f"AGENT logged in: {email}", user_id=email)

            return redirect(url_for("agent_dashboard"))

        except Exception as e:
            flash(f"Login error: {str(e)}")
            return redirect(url_for("login"))

    return render_template("login.html", SUPABASE_URL=SUPABASE_URL, SUPABASE_ANON_KEY=SUPABASE_ANON_KEY, login_mode="agent")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.")
    return redirect(url_for("login"))


@app.route('/dashboard/agent')
def agent_dashboard_alias():
    return redirect(url_for('agent_dashboard'))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        username = (request.form.get("username") or "").strip().lower()
        phone = (request.form.get("phone") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        gender = (request.form.get("gender") or "Not Specified").strip()

        try:
            # Create Auth user (admin)
            res = sb_admin.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name}
            })

            # Insert profile (fill NOT NULL columns)
            profile_data = {
                "id": res.user.id,
                "full_name": full_name,
                "username": username,
                "email": email,
                "phone": phone,
                "phone_number": phone,
                "gender": gender,
                "national_id": email,
                "address": "PENDING",
                "town": "PENDING",
                "region": "PENDING",
                "profile_pic_path": "none",
                "id_document_path": "none",
                "role": "AGENT",
                "status": "PENDING_APPROVAL",
                "auth_method": "password",
            }

            # Save to agent_profiles
            sb_admin.table("agent_profiles").insert(profile_data).execute()

            # Save to older agents table (best effort)
            try:
                sb_admin.table("agents").insert(profile_data).execute()
            except Exception:
                try:
                    pd2 = dict(profile_data)
                    # remove columns that may not exist in old table
                    for k in ("auth_method","phone_number","national_id","address","town","region","profile_pic_path","id_document_path","gender"):
                        pd2.pop(k, None)
                    sb_admin.table("agents").insert(pd2).execute()
                except Exception:
                    pass

            # Sign in user (normal client)
            supabase.auth.sign_in_with_password({"email": email, "password": password})

            session.clear()
            session["role"] = "AGENT"
            session["email"] = email
            session["status"] = "pending_approval"
            log_system_event("REGISTER", f"AGENT registered: {email}", user_id=email)

            return redirect(url_for("agent_dashboard"))

        except Exception as e:
            flash(str(e))
            return redirect(url_for("register"))

    return render_template("register.html")
@app.route("/api/admin/overview")
def api_admin_overview():
    if not _require_admin(): return jsonify({"success": False, "error": "Not authorized"}), 401

    agents1 = sb_admin.table("agent_profiles").select("id,status").execute().data or []
    agents2 = sb_admin.table("agents").select("id,status").execute().data or []
    agents = agents1 + agents2 # Combine them!

    active = len([a for a in agents if (a.get("status") or "").upper() == "ACTIVE"])
    pending = len([a for a in agents if (a.get("status") or "").upper() in ("PENDING", "PENDING_APPROVAL")])
    blocked = len([a for a in agents if (a.get("status") or "").upper() == "BLOCKED"])

    drivers = sb_admin.table("drivers").select("id").execute().data or []
    clients = sb_admin.table("clients").select("id").execute().data or []
    ledger = sb_admin.table("finance_ledger").select("amount").execute().data or []
    total_paid = sum(float(x.get("amount") or 0) for x in ledger)

    return jsonify({"success": True, "data": {
        "agents_total": len(agents),
        "agents_active": active,
        "agents_pending": pending,
        "agents_blocked": blocked,
        "drivers_total": len(drivers),
        "clients_total": len(clients),
        "total_paid": total_paid,
        "recent_activity": []
    }})

@app.route("/api/admin/agents_auth_stats")
def api_admin_agents_auth_stats():
    if not _require_admin(): return jsonify({"success": False, "error": "Not authorized"}), 401
    
    auth_count = 0
    try:
        url = URL.rstrip("/") + "/auth/v1/admin/users"
        headers = {"Authorization": f"Bearer {SERVICE_KEY}", "apikey": SERVICE_KEY}
        r = requests.get(url, headers=headers, params={"page": 1, "per_page": 200}, timeout=10)
        auth_count = len(r.json() or [])
    except Exception: pass

    agents1 = sb_admin.table("agent_profiles").select("id,status").execute().data or []
    agents2 = sb_admin.table("agents").select("id,status").execute().data or []
    agents = agents1 + agents2

    return jsonify({"success": True, "data": {
        "auth_users_total": auth_count,
        "agents_db_total": len(agents),
        "agents_db_pending": len([a for a in agents if (a.get("status") or "").upper() in ("PENDING", "PENDING_APPROVAL")]),
        "agents_db_active": len([a for a in agents if (a.get("status") or "").upper() == "ACTIVE"]),
        "agents_db_blocked": len([a for a in agents if (a.get("status") or "").upper() == "BLOCKED"])
    }})

@app.route("/api/admin/agents")
def api_admin_agents():
    if not _require_admin(): return jsonify({"success": False, "error": "Not authorized"}), 401

    # Pull from both tables
    agents1 = sb_admin.table("agent_profiles").select("*").limit(2000).execute().data or []
    agents2 = sb_admin.table("agents").select("*").limit(2000).execute().data or []

    # Merge them safely by email so we don't get duplicates
    merged = {}
    for a in agents2:
        if a.get("email"): merged[a["email"].lower()] = a
    for a in agents1:
        if a.get("email"): merged[a["email"].lower()] = a
    
    agents_list = list(merged.values())

    drivers = sb_admin.table("drivers").select("recruiter_agent_id").execute().data or []
    clients = sb_admin.table("clients").select("recruiter_agent_id").execute().data or []

    counts = {}
    for d in drivers:
        aid = d.get("recruiter_agent_id")
        if aid:
            counts[aid] = counts.get(aid, {"drivers":0, "clients":0})
            counts[aid]["drivers"] += 1
    for c in clients:
        aid = c.get("recruiter_agent_id")
        if aid:
            counts[aid] = counts.get(aid, {"drivers":0, "clients":0})
            counts[aid]["clients"] += 1

    return jsonify({"success": True, "data": {"agents": agents_list, "counts": counts}})

@app.route("/api/admin/agents/<agent_id>/status", methods=["POST"])
def api_admin_agent_status(agent_id):
    if not _require_admin(): return jsonify({"success": False, "error": "Not authorized"}), 401
    data = request.json or {}
    status = (data.get("status") or "").upper().strip()
    
    # Update both tables just to be safe!
    sb_admin.table("agent_profiles").update({"status": status}).eq("id", agent_id).execute()
    sb_admin.table("agents").update({"status": status}).eq("id", agent_id).execute()
    return jsonify({"success": True})

@app.route("/api/admin/approve_agent", methods=["POST"])
def approve_agent():
    data = request.json or {}
    agent_id = (data.get("agent_id") or "").strip()
    status = (data.get("status") or "ACTIVE").strip().upper()
    
    sb_admin.table("agent_profiles").update({"status": status}).eq("id", agent_id).execute()
    sb_admin.table("agents").update({"status": status}).eq("id", agent_id).execute()
    return jsonify({"success": True, "message": f"Agent status updated to {status}"})

# Leave the other basic routes (drivers, clients, finance, rules, etc.) here as they were.
@app.route("/api/admin/drivers")
def api_admin_drivers():
    return jsonify({"success": True, "data": sb_admin.table("drivers").select("*").limit(5000).execute().data or []})

@app.route("/api/admin/clients")
def api_admin_clients():
    return jsonify({"success": True, "data": sb_admin.table("clients").select("*").limit(5000).execute().data or []})

@app.route("/api/admin/finance")
def api_admin_finance():
    return jsonify({"success": True, "data": sb_admin.table("finance_ledger").select("*").limit(5000).execute().data or []})

@app.route("/api/admin/payment_rules")
def api_admin_payment_rules():
    return jsonify({"success": True, "data": sb_admin.table("payment_rules").select("*").execute().data or []})

@app.route("/api/admin/broadcasts")
def api_admin_broadcasts():
    return jsonify({"success": True, "data": sb_admin.table("broadcasts").select("*").execute().data or []})
@app.route('/admin')
def admin_entry():
    return redirect('/dashboard/admin')


@app.route("/admin")
def admin_entry():
    return redirect(url_for("admin_dashboard"))


# --- AGENT DASHBOARD API ROUTES ---

# ===== AGENT DASHBOARD V2 HELPERS =====
from datetime import datetime, timedelta, timezone, date
import re
import base64
from io import BytesIO

def _na_time_now():
    # Namibia is UTC+2 (no DST)
    return datetime.now(timezone(timedelta(hours=2)))

def _week_window(d: date):
    # Monday=0 ... Sunday=6
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return start, end

def _week_id(d: date):
    iso_year, iso_week, _ = d.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"

def _agent_email():
    return session.get("email")

@app.route("/agent/dashboard2")
@require_login("AGENT")
def agent_dashboard2():
    return render_template("agent_dashboard_v2.html")

@app.route("/api/agent/summary")
@require_agent_token
@require_login("AGENT")
def api_agent_summary():
    today = _na_time_now().date()
    ws, we = _week_window(today)
    wid = _week_id(today)

    email = _agent_email()
    # existing stats endpoint already counts totals
    try:
        # reuse your existing logic but add wallet fields (safe defaults)
        agent = sb_admin.table("agent_profiles").select("id,status").eq("email", email).execute().data
        if not agent:
            agent = sb_admin.table("agents").select("id,status").eq("email", email).execute().data
        status = (agent[0].get("status") if agent else "active") or "active"

        # counts
        aid = agent[0]["id"] if agent else None
        drivers = len(sb_admin.table("drivers").select("id").eq("recruiter_agent_id", aid).execute().data or []) if aid else 0
        clients = len(sb_admin.table("clients").select("id").eq("recruiter_agent_id", aid).execute().data or []) if aid else 0

        # wallet (if tables exist; otherwise 0)
        wa = 0
        wp = 0
        try:
            w = sb_admin.table("agent_wallets").select("available,pending").eq("agent_id", aid).limit(1).execute().data
            if w:
                wa = w[0].get("available") or 0
                wp = w[0].get("pending") or 0
        except Exception:
            pass

        return jsonify({
            "success": True,
            "status": status,
            "drivers": drivers,
            "clients": clients,
            "wallet_available": wa,
            "wallet_pending": wp,
            "week_id": wid,
            "week_start": str(ws),
            "week_end": str(we),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/agent/notifications")
@require_agent_token
@require_login("AGENT")
def api_agent_notifications():
    email = _agent_email()
    agent = sb_admin.table("agent_profiles").select("id").eq("email", email).execute().data
    if not agent:
        agent = sb_admin.table("agents").select("id").eq("email", email).execute().data
    if not agent:
        return jsonify({"success": True, "data": []})
    aid = agent[0]["id"]

    try:
        rows = sb_admin.table("agent_notifications").select("*").eq("agent_id", aid).order("created_at", desc=True).limit(20).execute().data or []
        return jsonify({"success": True, "data": rows})
    except Exception:
        return jsonify({"success": True, "data": []})

@app.route("/api/agent/upload_proof", methods=["POST"])
@require_agent_token
@require_login("AGENT")
def api_agent_upload_proof():
    # MVP: store proof record (image storage can be added once bucket is ready)
    data = request.json or {}
    img = data.get("image_base64") or ""
    if not img.startswith("data:image"):
        return jsonify({"success": False, "error": "Invalid image"}), 400

    email = _agent_email()
    agent = sb_admin.table("agent_profiles").select("id").eq("email", email).execute().data
    if not agent:
        agent = sb_admin.table("agents").select("id").eq("email", email).execute().data
    if not agent:
        return jsonify({"success": False, "error": "Agent not found"}), 404
    aid = agent[0]["id"]

    now = _na_time_now().isoformat()

    # store minimal proof row (later: upload to supabase storage + hash + watermark)
    sb_admin.table("registration_proofs").insert({
        "agent_id": aid,
        "captured_at": now,
        "note": "screen-proof-captured"
    }).execute()

    return jsonify({"success": True})

@app.route("/api/agent/register_driver_v2", methods=["POST"])
@require_agent_token
@require_login("AGENT")
def api_agent_register_driver_v2():
    payload = request.json or {}
    name = (payload.get("name") or "").strip()
    phone = (payload.get("phone") or "").strip()
    town = (payload.get("town") or "").strip()
    yene_code = (payload.get("yene_code") or "").strip()

    if not (name and phone and town and yene_code):
        return jsonify({"success": False, "error": "Missing fields"}), 400

    today = _na_time_now().date()
    ws, we = _week_window(today)
    wid = _week_id(today)

    email = _agent_email()
    agent = sb_admin.table("agent_profiles").select("id").eq("email", email).execute().data
    if not agent:
        agent = sb_admin.table("agents").select("id").eq("email", email).execute().data
    if not agent:
        return jsonify({"success": False, "error": "Agent not found"}), 404
    aid = agent[0]["id"]

    # Create referral row (your exact table names may differ; adjust later)
    sb_admin.table("agent_referrals").insert({
        "agent_id": aid,
        "type": "driver",
        "name": name,
        "phone": phone,
        "town": town,
        "yene_code": yene_code,
        "registered_at": _na_time_now().isoformat(),
        "week_id": wid,
        "week_start": str(ws),
        "week_end": str(we),
        "earning_active": True,
        "earning_end_at": (_na_time_now() + timedelta(days=30)).isoformat(),
        "weekly_cycle_start": str(ws),
    }).execute()

    return jsonify({"success": True})

@app.route("/api/agent/register_client_v2", methods=["POST"])
@require_agent_token
@require_login("AGENT")
def api_agent_register_client_v2():
    payload = request.json or {}
    name = (payload.get("name") or "").strip()
    phone = (payload.get("phone") or "").strip()
    yene_code = (payload.get("yene_code") or "").strip()
    if not (name and phone and yene_code):
        return jsonify({"success": False, "error": "Missing fields"}), 400

    today = _na_time_now().date()
    ws, we = _week_window(today)
    wid = _week_id(today)

    email = _agent_email()
    agent = sb_admin.table("agent_profiles").select("id").eq("email", email).execute().data
    if not agent:
        agent = sb_admin.table("agents").select("id").eq("email", email).execute().data
    if not agent:
        return jsonify({"success": False, "error": "Agent not found"}), 404
    aid = agent[0]["id"]

    sb_admin.table("agent_referrals").insert({
        "agent_id": aid,
        "type": "client",
        "name": name,
        "phone": phone,
        "yene_code": yene_code,
        "registered_at": _na_time_now().isoformat(),
        "week_id": wid,
        "week_start": str(ws),
        "week_end": str(we),
        "client_once_off_paid": False,
    }).execute()

    return jsonify({"success": True})

@app.route("/api/agent/team")
@require_agent_token
@require_login("AGENT")
def api_agent_team():
    mode = (request.args.get("mode") or "this_week").strip()
    today = _na_time_now().date()
    ws, we = _week_window(today)
    wid = _week_id(today)

    # last week window
    ws2 = ws - timedelta(days=7)
    we2 = we - timedelta(days=7)
    wid2 = _week_id(ws2)

    email = _agent_email()
    agent = sb_admin.table("agent_profiles").select("id").eq("email", email).execute().data
    if not agent:
        agent = sb_admin.table("agents").select("id").eq("email", email).execute().data
    if not agent:
        return jsonify({"success": False, "error": "Agent not found"}), 404
    aid = agent[0]["id"]

    q = sb_admin.table("agent_referrals").select("*").eq("agent_id", aid).order("registered_at", desc=True)
    if mode == "this_week":
        q = q.eq("week_id", wid)
    elif mode == "last_week":
        q = q.eq("week_id", wid2)
    rows = q.limit(200).execute().data or []

    # Map to UI fields; weekly rides & milestones will be filled once ride counters are connected
    data = []
    now = _na_time_now()
    for r in rows:
        days_left = None
        if r.get("type") == "driver" and r.get("earning_end_at"):
            try:
                end = datetime.fromisoformat(r["earning_end_at"])
                days_left = max(0, (end - now).days)
            except Exception:
                pass
        data.append({
            "type": r.get("type"),
            "name": r.get("name"),
            "phone": r.get("phone"),
            "week_rides": r.get("week_rides") or 0,
            "hit_5": bool(r.get("hit_5")),
            "hit_15": bool(r.get("hit_15")),
            "hit_25": bool(r.get("hit_25")),
            "days_left": days_left,
            "status": "Active" if r.get("earning_active", True) else "Cut off",
        })

    return jsonify({"success": True, "data": data})

@app.route("/api/agent/invoices")
@require_agent_token
@require_login("AGENT")
def api_agent_invoices():
    email = _agent_email()
    agent = sb_admin.table("agent_profiles").select("id").eq("email", email).execute().data
    if not agent:
        agent = sb_admin.table("agents").select("id").eq("email", email).execute().data
    if not agent:
        return jsonify({"success": False, "error": "Agent not found"}), 404
    aid = agent[0]["id"]

    try:
        rows = sb_admin.table("agent_invoices").select("*").eq("agent_id", aid).order("week_start", desc=True).limit(20).execute().data or []
        data = [{
            "week_id": r.get("week_id"),
            "pending": r.get("pending") or 0,
            "available": r.get("available") or 0,
            "pdf_url": r.get("pdf_url")
        } for r in rows]
        return jsonify({"success": True, "data": data})
    except Exception:
        return jsonify({"success": True, "data": []})
@app.route("/api/agent/stats")
@require_agent_token
@require_login("AGENT")
def api_agent_stats():
    email = session.get("email")
    agent = sb_admin.table("agent_profiles").select("id").eq("email", email).execute().data
    if not agent:
        agent = sb_admin.table("agents").select("id").eq("email", email).execute().data
    if not agent: return jsonify({"success": False, "drivers": 0, "clients": 0})
    
    aid = agent[0]["id"]
    d = sb_admin.table("drivers").select("id").eq("recruiter_agent_id", aid).execute().data or []
    c = sb_admin.table("clients").select("id").eq("recruiter_agent_id", aid).execute().data or []
    return jsonify({"success": True, "drivers": len(d), "clients": len(c)})

@app.route("/api/agent/register_driver", methods=["POST"])
@require_agent_token
@require_login("AGENT")
def api_agent_register_driver():
    data = request.json
    phone = (data.get("phone") or "").strip()
    
    # --- DUPLICATE CHECK ---
    existing = sb_admin.table("drivers").select("id").eq("phone_number", phone).execute().data
    if not existing:
        existing = sb_admin.table("drivers").select("id").eq("phone", phone).execute().data
    if existing:
        return jsonify({"success": False, "error": "A driver with this phone number is already registered in the network!"})

    email = session.get("email")
    agent = sb_admin.table("agent_profiles").select("id, full_name").eq("email", email).execute().data
    if not agent:
        agent = sb_admin.table("agents").select("id, full_name").eq("email", email).execute().data
    if not agent: return jsonify({"success": False, "error": "Agent not found"})
    
    aid = agent[0]["id"]
    aname = agent[0].get("full_name", "Unknown")
    
    try:
        sb_admin.table("drivers").insert({
            "full_name": data.get("full_name"),
            "phone": phone,
            "phone_number": phone,
            "town": data.get("town"),
            "license_number": "PENDING",
            "car_details": "PENDING",
            "recruiter_agent_id": aid,
            "recruiter_name": aname,
            "status": "pending_approval"
        }).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/agent/register_client", methods=["POST"])
@require_agent_token
@require_login("AGENT")
def api_agent_register_client():
    data = request.json
    phone = (data.get("phone") or "").strip()
    
    # --- DUPLICATE CHECK ---
    existing = sb_admin.table("clients").select("id").eq("phone_number", phone).execute().data
    if not existing:
        existing = sb_admin.table("clients").select("id").eq("phone", phone).execute().data
    if existing:
        return jsonify({"success": False, "error": "A client with this phone number is already registered in the network!"})

    email = session.get("email")
    agent = sb_admin.table("agent_profiles").select("id, full_name").eq("email", email).execute().data
    if not agent:
        agent = sb_admin.table("agents").select("id, full_name").eq("email", email).execute().data
    if not agent: return jsonify({"success": False, "error": "Agent not found"})
    
    try:
        sb_admin.table("clients").insert({
            "full_name": data.get("full_name"),
            "phone": phone,
            "phone_number": phone,
            "yene_code": "PENDING",
            "recruiter_agent_id": agent[0]["id"],
            "recruiter_name": agent[0].get("full_name", "Unknown"),
            "status": "pending_approval"
        }).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# --- ADMIN FINANCE & BROADCAST ROUTES ---
@app.route("/api/admin/payment_rules", methods=["POST"])
def api_admin_save_rule():
    data = request.json
    try:
        # Check if a rule already exists for this exact region and town
        existing = sb_admin.table("payment_rules").select("id").eq("region", data.get("region", "")).eq("town", data.get("town", "")).execute().data
        if existing:
            # Update the existing rule
            sb_admin.table("payment_rules").update(data).eq("id", existing[0]["id"]).execute()
        else:
            # Create a brand new rule
            sb_admin.table("payment_rules").insert(data).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/admin/broadcast", methods=["POST"])
def api_admin_save_broadcast():
    data = request.json
    try:
        sb_admin.table("broadcasts").insert({
            "message": data.get("message"),
            "target_region": "ALL"
        }).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/public/broadcasts", methods=["GET"])
def api_public_broadcasts():
    try:
        # Grab the 3 newest broadcasts for the landing page
        data = sb_admin.table("broadcasts").select("*").order("created_at", desc=True).limit(3).execute().data or []
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# --- ADMIN BROADCAST EDIT/DELETE ROUTES ---
@app.route("/api/admin/broadcast/<id>", methods=["PUT", "DELETE"])
def api_admin_manage_broadcast(id):
    try:
        if request.method == "DELETE":
            sb_admin.table("broadcasts").delete().eq("id", id).execute()
            return jsonify({"success": True, "message": "Broadcast deleted"})
        
        if request.method == "PUT":
            data = request.json
            sb_admin.table("broadcasts").update({"message": data.get("message")}).eq("id", id).execute()
            return jsonify({"success": True, "message": "Broadcast updated"})
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# --- ADMIN EXTENDED FEATURES ---
@app.route("/api/admin/audit_logs")
def api_admin_audit():
    logs = sb_admin.table("system_logs").select("*").order("created_at", desc=True).limit(50).execute().data
    return jsonify({"success": True, "data": logs or []})

@app.route("/api/admin/broadcast/<id>", methods=["PUT", "DELETE"])
def api_admin_manage_bc(id):
    if request.method == "DELETE":
        sb_admin.table("broadcasts").delete().eq("id", id).execute()
    else:
        sb_admin.table("broadcasts").update({"message": request.json.get("message")}).eq("id", id).execute()
    return jsonify({"success": True})

@app.route("/api/admin/drivers/<id>", methods=["PUT", "DELETE"])
def api_admin_manage_driver(id):
    if request.method == "DELETE":
        sb_admin.table("drivers").delete().eq("id", id).execute()
    else:
        sb_admin.table("drivers").update(request.json).eq("id", id).execute()
    return jsonify({"success": True})


from flask import session

@app.get("/api/debug-session")
def debug_session():
    # shows keys & value types only (no full secrets)
    out = {}
    for k,v in session.items():
        if v is None:
            out[k] = None
        else:
            val = str(v)
            out[k] = {"type": type(v).__name__, "len": len(val), "preview": val[:8]}
    return jsonify({"ok": True, "session": out})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)

@app.get("/debug/env")
def debug_env():
    # SAFE: only shows whether env vars exist, never prints secrets
    return {
        "SUPABASE_URL_set": bool(os.getenv("SUPABASE_URL")),
        "SUPABASE_ANON_KEY_set": bool(os.getenv("SUPABASE_ANON_KEY")),
        "runtime": "ok"
    }


@app.get("/api/public-config")
def public_config():
    url = (os.getenv("SUPABASE_URL") or "").strip()
    anon = (os.getenv("SUPABASE_ANON_KEY") or "").strip()

    if not url or not anon:
        return jsonify({
            "ok": False,
            "error": "Missing SUPABASE_URL or SUPABASE_ANON_KEY on server",
            "SUPABASE_URL_len": len(url),
            "SUPABASE_ANON_KEY_len": len(anon),
        }), 500

    return jsonify({
        "ok": True,
        "SUPABASE_URL": url,
        "SUPABASE_ANON_KEY": anon
    })


import os
from flask import jsonify, request

@app.get("/api/agent/me")
@require_agent_token
def api_agent_me():
    """
    Minimal endpoint used by frontend to confirm server is alive + env is present.
    IMPORTANT: This does NOT rely on Flask session.
    Your real auth is handled by Supabase in the browser.
    """
    url = (os.getenv("SUPABASE_URL") or "").strip()
    anon = (os.getenv("SUPABASE_ANON_KEY") or "").strip()

    # Return ok even if no user; frontend should use Supabase session for identity.
    return jsonify({
        "ok": True,
        "has_supabase_env": bool(url and anon),
        "note": "Use Supabase auth session in browser. This endpoint prevents 404 login loops.",
        "path": request.path
    }), 200

@app.get("/api/routes")
def api_routes():
    out = []
    for r in sorted(app.url_map.iter_rules(), key=lambda x: str(x)):
        if r.endpoint != "static":
            out.append({"methods": sorted(list(r.methods)), "rule": r.rule, "endpoint": r.endpoint})
    return jsonify({"ok": True, "count": len(out), "routes": out})

@app.get("/api/whoami")
def api_whoami():
    token = (request.headers.get("Authorization","").replace("Bearer","").strip())
    if not token:
        return jsonify({"ok": False, "error": "Missing Bearer token"}), 401

    url = os.getenv("SUPABASE_URL","").strip()
    anon = os.getenv("SUPABASE_ANON_KEY","").strip()
    if not url or not anon:
        return jsonify({"ok": False, "error": "Server missing SUPABASE_URL or SUPABASE_ANON_KEY"}), 500

    # Validate token with Supabase Auth
    r = requests.get(
        url.rstrip("/") + "/auth/v1/user",
        headers={"Authorization": f"Bearer {token}", "apikey": anon},
        timeout=15
    )
    if r.status_code != 200:
        return jsonify({"ok": False, "error": "Invalid session", "status": r.status_code, "body": r.text[:200]}), 401

    user = r.json()
    uid = user.get("id")
    email = user.get("email")

    # Lookup in agent_profiles using any of the columns you may have
    # (your table includes auth_id + user_id + email)
    try:
        q = supabase.table("agent_profiles")\
            .select("id, full_name, status, role, auth_id, user_id, email")\
            .or_(f"auth_id.eq.{uid},user_id.eq.{uid},email.eq.{email}")\
            .limit(1).execute()
        prof = (q.data or [None])[0]
    except Exception as e:
        return jsonify({"ok": False, "error": "DB lookup failed", "detail": str(e)}), 500

    role = (prof or {}).get("role") or "agent"
    return jsonify({"ok": True, "role": role, "user_id": uid, "email": email, "profile": prof})
