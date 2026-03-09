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
        # PUBLIC_DASHBOARD_BYPASS (do not remove)
        if request.path in ['/agent/dashboard', '/dashboard/admin']:
            return f(*args, **kwargs)


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

# ---------------------------
# PUBLIC DASHBOARD PAGES
# Supabase session is handled in browser.
# Data access must be via /api/* with Bearer token.
# ---------------------------

@app.route('/dashboard/admin')
def admin_dashboard():
    return render_template('admin_dashboard.html')


@app.route('/agent/dashboard')
def agent_dashboard():
    return render_template('agent_dashboard.html')


@app.route("/dashboard/agent")
def agent_dashboard_alias():
    return redirect(url_for("agent_dashboard"))


# ---------------------------
# PUBLIC DASHBOARD PAGES
# Supabase session is handled in browser.
# Data access must be via /api/* with Bearer token.
# ---------------------------





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


def require_login(required_role=None):
    """Session-based gate (legacy). Dashboards are PUBLIC now; API uses Supabase Bearer token.
    This decorator is kept only to protect legacy routes that still rely on Flask session.
    """
    from functools import wraps
    from flask import request, session, redirect, url_for, flash

    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            # PUBLIC PAGES (do NOT redirect)
            if request.path in ("/agent/dashboard", "/dashboard/admin"):
                return fn(*args, **kwargs)

            # Must have some session identity for legacy-protected routes
            if not session.get("user_id") and not session.get("email") and not session.get("role"):
                flash("Please log in.")
                return redirect(url_for("login"))

            # Role enforcement (legacy)
            if required_role:
                role = (session.get("role") or session.get("user_role") or "").upper()
                if role != str(required_role).upper():
                    flash(f"Access denied. {required_role} role required.")
                    return redirect(url_for("login"))

            return fn(*args, **kwargs)
        return wrapped
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
            ref_email = request.args.get("ref") or request.form.get("ref") or session.get("agent_ref")
            insert_res = sb_admin.table("agent_profiles").insert(profile_data).execute()
            new_agent_rows = insert_res.data or []
            new_agent = new_agent_rows[0] if new_agent_rows else None

            if ref_email and new_agent:
                try:
                    parent_rows = sb_admin.table("agent_profiles").select("id,email,full_name").eq("email", ref_email).limit(1).execute().data or []
                    if parent_rows:
                        parent = parent_rows[0]
                        sb_admin.table("agent_referrals").insert({
                            "parent_agent_id": parent.get("id"),
                            "parent_agent_email": parent.get("email"),
                            "child_agent_id": new_agent.get("id"),
                            "child_agent_email": new_agent.get("email"),
                            "child_agent_name": new_agent.get("full_name")
                        }).execute()
                except Exception:
                    pass

            session.pop("agent_ref", None)

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
@app.route("/admin")
def admin_entry():
    return redirect(url_for("admin_dashboard"))




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



### === AGENT V2 LIVE ROUTES ===

def _env(name, default=""):
    import os
    return (os.getenv(name, default) or "").strip()

SUPABASE_URL = _env("SUPABASE_URL")
SUPABASE_ANON_KEY = _env("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = _env("SUPABASE_SERVICE_ROLE_KEY")

def _rest_url(path):
    return SUPABASE_URL.rstrip("/") + "/rest/v1" + path

def _rest_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

def _verify_bearer():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    try:
        r = requests.get(
            SUPABASE_URL.rstrip("/") + "/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": SUPABASE_ANON_KEY,
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def _find_agent_profile(uid=None, email=None):
    if uid:
        q = f"/agent_profiles?select=*&or=(auth_id.eq.{uid},user_id.eq.{uid})&limit=1"
        r = requests.get(_rest_url(q), headers=_rest_headers(), timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()[0]

    if email:
        q = f"/agent_profiles?select=*&email=eq.{email}&limit=1"
        r = requests.get(_rest_url(q), headers=_rest_headers(), timeout=10)
        if r.status_code == 200 and r.json():
            return r.json()[0]

    return None

def _week_bounds():
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday

@app.get("/api/agent/me_v2")
def api_agent_me_v2():
    user = _verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _find_agent_profile(uid, email)

    return jsonify({
        "ok": True,
        "user_id": uid,
        "email": email,
        "profile": prof
    })

@app.get("/api/agent/summary_v2")
def api_agent_summary_v2():
    user = _verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _find_agent_profile(uid, email)
    if not prof:
        return jsonify({"ok": True, "drivers_week": 0, "clients_week": 0, "drivers_all": 0, "profile": None})

    agent_id = prof.get("id")
    monday, sunday = _week_bounds()

    def get_count(path):
        r = requests.get(_rest_url(path), headers=_rest_headers(), timeout=10)
        if r.status_code == 200:
            return len(r.json())
        return 0

    drivers_week = get_count(
        f"/drivers?select=id&recruiter_agent_id=eq.{agent_id}"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
    )

    clients_week = get_count(
        f"/clients?select=id&recruiter_agent_id=eq.{agent_id}"
        f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
        f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
    )

    drivers_all = get_count(
        f"/drivers?select=id&recruiter_agent_id=eq.{agent_id}"
    )

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
def api_agent_activity_v2():
    user = _verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _find_agent_profile(uid, email)
    if not prof:
        return jsonify({"ok": True, "rows": [], "profile": None})

    agent_id = prof.get("id")
    monday, sunday = _week_bounds()
    rows = []

    r1 = requests.get(
        _rest_url(
            f"/drivers?select=full_name,phone,town,created_at"
            f"&recruiter_agent_id=eq.{agent_id}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
            f"&order=created_at.desc&limit=20"
        ),
        headers=_rest_headers(),
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
                "created_at": x.get("created_at"),
            })

    r2 = requests.get(
        _rest_url(
            f"/clients?select=full_name,phone,created_at"
            f"&recruiter_agent_id=eq.{agent_id}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
            f"&order=created_at.desc&limit=20"
        ),
        headers=_rest_headers(),
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
                "created_at": x.get("created_at"),
            })

    rows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return jsonify({"ok": True, "rows": rows[:30], "profile": prof})

@app.post("/api/agent/register_driver_v2_working")
def api_agent_register_driver_v2_working():
    user = _verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _find_agent_profile(uid, email)
    if not prof:
        return jsonify({"ok": False, "error": "Agent profile not linked"}), 400

    j = request.get_json(force=True) or {}
    payload = {
        "recruiter_agent_id": prof["id"],
        "recruiter_auth_id": uid,
        "recruiter_name": prof.get("full_name") or prof.get("username") or prof.get("email"),
        "full_name": (j.get("full_name") or "").strip(),
        "phone": (j.get("phone") or "").strip(),
        "town": (j.get("town") or "").strip(),
    }

    if not payload["full_name"] or not payload["phone"]:
        return jsonify({"ok": False, "error": "Missing full_name / phone"}), 400

    r = requests.post(_rest_url("/drivers"), headers=_rest_headers(), json=payload, timeout=10)
    if r.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r.text}), 500

    return jsonify({"ok": True, "row": r.json()[0], "profile": prof})

@app.post("/api/agent/register_client_v2_working")
def api_agent_register_client_v2_working():
    user = _verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _find_agent_profile(uid, email)
    if not prof:
        return jsonify({"ok": False, "error": "Agent profile not linked"}), 400

    j = request.get_json(force=True) or {}
    payload = {
        "recruiter_agent_id": prof["id"],
        "recruiter_auth_id": uid,
        "recruiter_name": prof.get("full_name") or prof.get("username") or prof.get("email"),
        "full_name": (j.get("full_name") or "").strip(),
        "phone": (j.get("phone") or "").strip(),
    }

    if not payload["full_name"] or not payload["phone"]:
        return jsonify({"ok": False, "error": "Missing full_name / phone"}), 400

    r = requests.post(_rest_url("/clients"), headers=_rest_headers(), json=payload, timeout=10)
    if r.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r.text}), 500

    return jsonify({"ok": True, "row": r.json()[0], "profile": prof})




### === AGENT V4 UPGRADE ===
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from flask import send_file
import tempfile

def _v4_monday_from_string(s):
    return datetime.date.fromisoformat(s)

def _v4_group_week_rows(rows):
    grouped = {}
    for x in rows:
        ws = x.get("week_start") or "NO_WEEK"
        we = x.get("week_end") or "NO_WEEK"
        key = f"{ws}|{we}"
        grouped.setdefault(key, {
            "week_start": ws,
            "week_end": we,
            "credits": 0.0,
            "debits": 0.0,
            "net": 0.0,
            "items": []
        })
        amt = float(x.get("amount") or 0)
        if x.get("entry_type") == "credit":
            grouped[key]["credits"] += amt
            grouped[key]["net"] += amt
        else:
            grouped[key]["debits"] += amt
            grouped[key]["net"] -= amt
        grouped[key]["items"].append(x)
    out = list(grouped.values())
    out.sort(key=lambda z: z["week_start"] or "", reverse=True)
    return out

@app.get("/api/agent/weekly_breakdown_v4")
def api_agent_weekly_breakdown_v4():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")

    r = requests.get(
        _fa_rest(f"/agent_registrations?select=created_at,subject_type&agent_auth_id=eq.{uid}&order=created_at.desc&limit=1000"),
        headers=_fa_headers(),
        timeout=15
    )
    rows = r.json() if r.status_code == 200 else []

    weeks = {}
    for x in rows:
        created = x.get("created_at")
        if not created:
            continue
        d = datetime.date.fromisoformat(created[:10])
        monday = d - datetime.timedelta(days=d.weekday())
        sunday = monday + datetime.timedelta(days=6)
        key = monday.isoformat()
        weeks.setdefault(key, {
            "week_start": monday.isoformat(),
            "week_end": sunday.isoformat(),
            "drivers": 0,
            "clients": 0
        })
        if x.get("subject_type") == "driver":
            weeks[key]["drivers"] += 1
        elif x.get("subject_type") == "client":
            weeks[key]["clients"] += 1

    out = list(weeks.values())
    out.sort(key=lambda x: x["week_start"], reverse=True)
    return jsonify({"ok": True, "rows": out})

@app.get("/api/agent/invoices_v4")
def api_agent_invoices_v4():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    uid = user.get("id")

    r = requests.get(
        _fa_rest(f"/agent_wallet_ledger?select=week_start,week_end,entry_type,amount,reference,note,created_at&agent_auth_id=eq.{uid}&order=week_start.desc,created_at.desc&limit=500"),
        headers=_fa_headers(),
        timeout=15
    )
    rows = r.json() if r.status_code == 200 else []
    return jsonify({"ok": True, "rows": _v4_group_week_rows(rows)})

@app.get("/api/agent/invoice_pdf_v4")
def api_agent_invoice_pdf_v4():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _strict_find_agent_profile(uid)
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    week_start = (request.args.get("week_start") or "").strip()

    if not week_start:
        return jsonify({"ok": False, "error": "Missing week_start"}), 400

    r = requests.get(
        _fa_rest(f"/agent_wallet_ledger?select=created_at,week_start,week_end,entry_type,amount,reference,note&agent_auth_id=eq.{uid}&week_start=eq.{week_start}&order=created_at.asc"),
        headers=_fa_headers(),
        timeout=15
    )
    rows = r.json() if r.status_code == 200 else []

    if rows:
        week_end = rows[0].get("week_end") or ""
    else:
        monday = _v4_monday_from_string(week_start)
        week_end = (monday + datetime.timedelta(days=6)).isoformat()

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    agent_name = (prof or {}).get("full_name") or (prof or {}).get("username") or email or "Agent"
    group_name = (prof or {}).get("town") or "Single Agent"
    referral_code = (prof or {}).get("referral_code") or "-"

    credits = 0.0
    debits = 0.0
    net = 0.0
    for x in rows:
        amt = float(x.get("amount") or 0)
        if x.get("entry_type") == "credit":
            credits += amt
            net += amt
        else:
            debits += amt
            net -= amt

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    c = canvas.Canvas(tmp.name, pagesize=A4)
    width, height = A4

    y = height - 50
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, y, "YENE INVOICE")
    y -= 22

    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Generated: {generated_at}")
    y -= 18
    c.drawString(40, y, f"Agent Name: {agent_name}")
    y -= 18
    c.drawString(40, y, f"Group: {group_name}")
    y -= 18
    c.drawString(40, y, f"Referral Code: {referral_code}")
    y -= 18
    c.drawString(40, y, f"Week: {week_start} -> {week_end}")
    y -= 30

    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Created")
    c.drawString(150, y, "Type")
    c.drawString(240, y, "Amount")
    c.drawString(330, y, "Reference")
    c.drawString(470, y, "Note")
    y -= 16

    c.setFont("Helvetica", 10)
    for x in rows[:40]:
        if y < 70:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 10)
        c.drawString(40, y, str(x.get("created_at") or "")[:16])
        c.drawString(150, y, str(x.get("entry_type") or ""))
        c.drawString(240, y, f"{float(x.get('amount') or 0):.2f}")
        c.drawString(330, y, str(x.get("reference") or "")[:20])
        c.drawString(470, y, str(x.get("note") or "")[:18])
        y -= 14

    y -= 18
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, f"Credits: {credits:.2f}")
    y -= 16
    c.drawString(40, y, f"Debits: {debits:.2f}")
    y -= 16
    c.drawString(40, y, f"Net: {net:.2f}")

    c.save()

    filename = f"yene_invoice_{agent_name.replace(' ','_')}_{week_start}.pdf"
    return send_file(tmp.name, as_attachment=True, download_name=filename, mimetype="application/pdf")

@app.get("/api/agent/team_v4")
def api_agent_team_v4():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _strict_find_agent_profile(uid)
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    if not prof:
        return jsonify({"ok": True, "rows": [], "referral_code": None})

    referral_code = (prof.get("referral_code") or "").strip()
    if not referral_code:
        return jsonify({"ok": True, "rows": [], "referral_code": ""})

    r = requests.get(
        _fa_rest(f"/agent_profiles?select=full_name,email,phone,role,created_at,referred_by_code&referred_by_code=eq.{referral_code}&order=created_at.desc&limit=200"),
        headers=_fa_headers(),
        timeout=15
    )
    rows = r.json() if r.status_code == 200 else []
    return jsonify({"ok": True, "referral_code": referral_code, "rows": rows})



@app.get("/api/agent/whoami_strict")
def api_agent_whoami_strict():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    uid = user.get("id")
    prof = _strict_find_agent_profile(uid)
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    return jsonify({
        "ok": True,
        "auth_user_id": uid,
        "profile_found": True if prof else False,
        "profile": prof
    })


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


def _strict_find_agent_profile(uid):
    if not uid:
        return None
    q = f"/agent_profiles?select=*&auth_id=eq.{uid}&limit=1"
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
    prof = _strict_find_agent_profile(uid)
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    return jsonify({"ok": True, "user_id": uid, "email": email, "profile": prof})

@app.get("/api/agent/summary_v3")
def api_agent_summary_v3():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _strict_find_agent_profile(uid)
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
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
    prof = _strict_find_agent_profile(uid)
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
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
    prof = _strict_find_agent_profile(uid)
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
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
    prof = _strict_find_agent_profile(uid)
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
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
    prof = _strict_find_agent_profile(uid)
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))
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




### === NEW AGENT AUTOLINK FIX ===

def _autolink_profile_by_email(uid, email):
    """
    If a newly signed-up agent has an agent_profiles row with matching email
    but missing auth_id, link it automatically.
    """
    if not uid or not email:
        return None

    # 1) already linked?
    q1 = f"/agent_profiles?select=*&auth_id=eq.{uid}&limit=1"
    r1 = requests.get(_fa_rest(q1), headers=_fa_headers(), timeout=10)
    if r1.status_code == 200 and r1.json():
        return r1.json()[0]

    # 2) find by email
    q2 = f"/agent_profiles?select=*&email=eq.{email}&limit=1"
    r2 = requests.get(_fa_rest(q2), headers=_fa_headers(), timeout=10)
    if r2.status_code == 200 and r2.json():
        prof = r2.json()[0]
        pid = prof.get("id")
        if pid:
            patch_payload = {"auth_id": uid, "user_id": uid}
            requests.patch(
                _fa_rest(f"/agent_profiles?id=eq.{pid}"),
                headers=_fa_headers(),
                json=patch_payload,
                timeout=10
            )

            # re-read linked row
            r3 = requests.get(_fa_rest(q1), headers=_fa_headers(), timeout=10)
            if r3.status_code == 200 and r3.json():
                return r3.json()[0]
        return prof

    return None


# --- AGENT DASHBOARD API ROUTES ---
from datetime import datetime, timedelta

@app.route("/api/agent/weekly_stats")
@require_login("AGENT")
def api_agent_weekly():
    email = session.get("email")
    agent = sb_admin.table("agent_profiles").select("*").eq("email", email).execute().data
    if not agent: return jsonify({"success": False})
    
    agent_data = agent[0]
    aid = agent_data["id"]
    region = agent_data.get("region", "Khomas")
    
    # Calculate Monday to Sunday of the current week
    today = datetime.today()
    monday = today - timedelta(days=today.weekday())
    monday_str = monday.strftime('%Y-%m-%d')
    
    # Fetch ALL-TIME data for the Gamification Badges
    all_d = sb_admin.table("drivers").select("id", count="exact").eq("recruiter_agent_id", aid).execute().count or 0
    all_c = sb_admin.table("clients").select("id", count="exact").eq("recruiter_agent_id", aid).execute().count or 0
    
    # Fetch WEEKLY data for the Earnings Ledger
    week_d = sb_admin.table("drivers").select("*").eq("recruiter_agent_id", aid).gte("created_at", monday_str).execute().data or []
    week_c = sb_admin.table("clients").select("*").eq("recruiter_agent_id", aid).gte("created_at", monday_str).execute().data or []
    
    # Dynamically calculate earnings based on Admin Pricing Rules
    rules = sb_admin.table("payment_rules").select("*").eq("region", region).execute().data
    d_rate = rules[0]["driver_reg"] if rules else 50 # Default 50 if admin hasn't set it
    c_rate = rules[0]["client_reg"] if rules else 10 # Default 10 if admin hasn't set it
    
    earnings = (len(week_d) * d_rate) + (len(week_c) * c_rate)
    
    # Build the recent weekly activity table
    recent = []
    for d in week_d: recent.append({"date": d["created_at"], "type": "Driver", "name": d.get("full_name"), "town": d.get("town")})
    for c in week_c: recent.append({"date": c["created_at"], "type": "Client", "name": c.get("full_name"), "town": "Network"})
    
    recent = sorted(recent, key=lambda x: x["date"], reverse=True)
    
    return jsonify({
        "success": True,
        "weekly_earnings": earnings,
        "weekly_drivers": len(week_d),
        "weekly_clients": len(week_c),
        "total_drivers": all_d,
        "total_clients": all_c,
        "recent": recent
    })

@app.route("/api/agent/register_driver", methods=["POST"])
@require_login("AGENT")
def api_agent_register_driver():
    data = request.json
    email = session.get("email")
    agent = sb_admin.table("agent_profiles").select("id, full_name").eq("email", email).execute().data
    if not agent: return jsonify({"success": False, "error": "Agent not found"})
    
    phone = data.get("phone")
    dup = sb_admin.table("drivers").select("id").eq("phone", phone).execute()
    if dup.data: return jsonify({"success": False, "error": "Phone number already in system!"})
    
    try:
        sb_admin.table("drivers").insert({
            "full_name": data.get("full_name"), "phone": phone, "phone_number": phone,
            "town": data.get("town"), "recruiter_agent_id": agent[0]["id"],
            "recruiter_name": agent[0]["full_name"], "status": "pending_approval",
            "license_number": "PENDING", "car_details": "PENDING"
        }).execute()
        # Log to Admin Command Center
        log_system_event("REGISTER", f"Agent {agent[0]['full_name']} registered driver {data.get('full_name')}")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})



@app.route("/debug-agent-session")
def debug_agent_session():
    return {
        "session_email": session.get("email"),
        "session_role": session.get("role"),
        "session_user_id": session.get("user_id"),
        "session_auth_id": session.get("auth_id"),
    }


from agent_dashboard_v4 import register_agent_dashboard_v4_routes
register_agent_dashboard_v4_routes(app, sb_admin, require_login, globals().get("log_system_event"))


from agent_academy_v1 import register_agent_academy_v1_routes
register_agent_academy_v1_routes(app, sb_admin, require_login)


from agent_wallet_v1 import register_agent_wallet_v1_routes
register_agent_wallet_v1_routes(app, sb_admin, require_login)



def _admin_credit_activation_bonus(agent_id, bonus_key, description, reference_no):
    try:
        if not agent_id:
            return

        bonus_rows = sb_admin.table("agent_bonus_settings").select("*").eq("bonus_key", bonus_key).limit(1).execute().data or []
        if not bonus_rows:
            return

        row = bonus_rows[0]
        if not row.get("is_enabled", True):
            return

        amount = float(row.get("amount") or 0)
        if amount <= 0:
            return

        dup = sb_admin.table("agent_wallet_ledger").select("id").eq("reference_no", reference_no).limit(1).execute().data or []
        if dup:
            return

        prof = sb_admin.table("agent_profiles").select("email").eq("id", agent_id).limit(1).execute().data or []
        agent_email = prof[0].get("email") if prof else None

        sb_admin.table("agent_wallet_ledger").insert({
            "agent_id": str(agent_id),
            "agent_email": agent_email,
            "txn_type": "credit",
            "amount": amount,
            "description": description,
            "reference_no": reference_no,
            "status": "approved"
        }).execute()
    except Exception as e:
        print("Activation bonus credit failed:", e)


@app.route("/api/admin/approve_driver/<driver_id>", methods=["POST"])
def api_admin_approve_driver(driver_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        rows = sb_admin.table("drivers").select("*").eq("id", driver_id).limit(1).execute().data or []
        if not rows:
            return jsonify({"success": False, "error": "Driver not found"}), 404

        driver = rows[0]
        sb_admin.table("drivers").update({"status": "approved"}).eq("id", driver_id).execute()

        recruiter_agent_id = driver.get("recruiter_agent_id")
        _admin_credit_activation_bonus(
            recruiter_agent_id,
            "driver_activation",
            f"Driver activation bonus for {driver.get('full_name') or 'driver'}",
            f"driver-activation-{driver_id}"
        )

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/reject_driver/<driver_id>", methods=["POST"])
def api_admin_reject_driver(driver_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        sb_admin.table("drivers").update({"status": "rejected"}).eq("id", driver_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/approve_client/<client_id>", methods=["POST"])
def api_admin_approve_client(client_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        rows = sb_admin.table("clients").select("*").eq("id", client_id).limit(1).execute().data or []
        if not rows:
            return jsonify({"success": False, "error": "Client not found"}), 404

        client = rows[0]
        sb_admin.table("clients").update({"status": "approved"}).eq("id", client_id).execute()

        recruiter_agent_id = client.get("recruiter_agent_id")
        _admin_credit_activation_bonus(
            recruiter_agent_id,
            "client_activation",
            f"Client activation bonus for {client.get('full_name') or client.get('phone_number') or 'client'}",
            f"client-activation-{client_id}"
        )

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/reject_client/<client_id>", methods=["POST"])
def api_admin_reject_client(client_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        sb_admin.table("clients").update({"status": "rejected"}).eq("id", client_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500



@app.route("/api/admin/pending_drivers", methods=["GET"])
def api_admin_pending_drivers():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        rows = (
            sb_admin.table("drivers")
            .select("*")
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
            .data or []
        )
        approvable = []
        for r in rows:
            status = (r.get("status") or "").strip().lower()
            if status in {"approved", "rejected", "blocked"}:
                continue
            approvable.append(r)
        return jsonify({"success": True, "rows": approvable})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/pending_clients", methods=["GET"])
def api_admin_pending_clients():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        rows = (
            sb_admin.table("clients")
            .select("*")
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
            .data or []
        )
        approvable = []
        for r in rows:
            status = (r.get("status") or "").strip().lower()
            if status in {"approved", "rejected", "blocked"}:
                continue
            approvable.append(r)
        return jsonify({"success": True, "rows": approvable})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500




@app.route("/api/admin/all_drivers_for_approval", methods=["GET"])
def api_admin_all_drivers_for_approval():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        q = (request.args.get("q") or "").strip().lower()
        rows = (
            sb_admin.table("drivers")
            .select("*")
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
            .data or []
        )
        if q:
            rows = [
                r for r in rows
                if q in ((r.get("full_name") or "").lower())
                or q in ((r.get("phone") or "").lower())
                or q in ((r.get("phone_number") or "").lower())
                or q in ((r.get("town") or "").lower())
                or q in ((r.get("recruiter_name") or "").lower())
                or q in ((r.get("status") or "").lower())
            ]
        return jsonify({"success": True, "rows": rows})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/all_clients_for_approval", methods=["GET"])
def api_admin_all_clients_for_approval():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        q = (request.args.get("q") or "").strip().lower()
        rows = (
            sb_admin.table("clients")
            .select("*")
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
            .data or []
        )
        if q:
            rows = [
                r for r in rows
                if q in ((r.get("full_name") or "").lower())
                or q in ((r.get("phone") or "").lower())
                or q in ((r.get("phone_number") or "").lower())
                or q in ((r.get("recruiter_name") or "").lower())
                or q in ((r.get("status") or "").lower())
            ]
        return jsonify({"success": True, "rows": rows})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500



@app.route("/api/admin/reset_agent_pin/<agent_id>", methods=["POST"])
def api_admin_reset_agent_pin(agent_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        data = request.get_json(silent=True) or {}
        new_pin = (data.get("new_pin") or "").strip()

        if not new_pin:
            return jsonify({"success": False, "error": "New PIN is required"}), 400

        sb_admin.table("agent_profiles").update({
            "pin": new_pin
        }).eq("id", agent_id).execute()

        return jsonify({"success": True, "message": "PIN reset successfully"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500



@app.route("/api/admin/delete_agent/<agent_id>", methods=["POST"])
def api_admin_delete_agent(agent_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        sb_admin.table("agent_profiles").delete().eq("id", agent_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/delete_driver/<driver_id>", methods=["POST"])
def api_admin_delete_driver(driver_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        sb_admin.table("drivers").delete().eq("id", driver_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/delete_client/<client_id>", methods=["POST"])
def api_admin_delete_client(client_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    try:
        sb_admin.table("clients").delete().eq("id", client_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/town_filter_data", methods=["GET"])
def api_admin_town_filter_data():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    town = (request.args.get("town") or "").strip().lower()

    try:
        agents = sb_admin.table("agent_profiles").select("*").limit(5000).execute().data or []
        drivers = sb_admin.table("drivers").select("*").limit(5000).execute().data or []
        clients = sb_admin.table("clients").select("*").limit(5000).execute().data or []
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    def match_town(v):
        return town in ((v or "").strip().lower())

    if town:
        agents = [a for a in agents if match_town(a.get("town")) or match_town(a.get("operation_region"))]
        drivers = [d for d in drivers if match_town(d.get("town"))]
        clients = [c for c in clients if match_town(c.get("town"))]
    else:
        town = "all"

    return jsonify({
        "success": True,
        "town": town,
        "agents": agents,
        "drivers": drivers,
        "clients": clients
    })


@app.route("/api/admin/finance_summary_by_region", methods=["GET"])
def api_admin_finance_summary_by_region():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        agents = sb_admin.table("agent_profiles").select("*").limit(5000).execute().data or []
        ledger = sb_admin.table("agent_wallet_ledger").select("*").limit(10000).execute().data or []
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    by_agent = {}
    for row in ledger:
        aid = str(row.get("agent_id") or "")
        if not aid:
            continue
        amt = float(row.get("amount") or 0)
        typ = (row.get("txn_type") or "").lower()
        status = (row.get("status") or "approved").lower()
        if status != "approved":
            continue
        by_agent.setdefault(aid, 0.0)
        if typ == "debit":
            by_agent[aid] -= amt
        else:
            by_agent[aid] += amt

    regions = {}
    for a in agents:
        region = a.get("operation_region") or a.get("town") or "Unknown"
        aid = str(a.get("id") or "")
        bal = by_agent.get(aid, 0.0)
        if region not in regions:
            regions[region] = {
                "region": region,
                "agents": 0,
                "total_due": 0.0
            }
        regions[region]["agents"] += 1
        regions[region]["total_due"] += bal

    rows = list(regions.values())
    rows.sort(key=lambda x: x["total_due"], reverse=True)

    total_due_all = round(sum(r["total_due"] for r in rows), 2)

    return jsonify({
        "success": True,
        "total_due_all": total_due_all,
        "rows": rows
    })


@app.route("/api/admin/agent_payment_due", methods=["GET"])
def api_admin_agent_payment_due():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    town = (request.args.get("town") or "").strip().lower()

    try:
        agents = sb_admin.table("agent_profiles").select("*").limit(5000).execute().data or []
        ledger = sb_admin.table("agent_wallet_ledger").select("*").limit(10000).execute().data or []
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    balances = {}
    for row in ledger:
        aid = str(row.get("agent_id") or "")
        if not aid:
            continue
        amt = float(row.get("amount") or 0)
        typ = (row.get("txn_type") or "").lower()
        status = (row.get("status") or "approved").lower()
        if status != "approved":
            continue
        balances.setdefault(aid, 0.0)
        if typ == "debit":
            balances[aid] -= amt
        else:
            balances[aid] += amt

    out = []
    for a in agents:
        region = (a.get("operation_region") or a.get("town") or "").strip()
        if town and town not in region.lower():
            continue
        aid = str(a.get("id") or "")
        out.append({
            "agent_id": aid,
            "full_name": a.get("full_name") or "",
            "email": a.get("email") or "",
            "phone": a.get("phone") or "",
            "region": region or "Unknown",
            "amount_due": round(balances.get(aid, 0.0), 2)
        })

    out.sort(key=lambda x: x["amount_due"], reverse=True)

    return jsonify({
        "success": True,
        "rows": out
    })



import csv
from io import StringIO, BytesIO
from flask import Response

NAMIBIA_REGIONS = [
    "Erongo",
    "Hardap",
    "Karas",
    "Kavango East",
    "Kavango West",
    "Khomas",
    "Kunene",
    "Ohangwena",
    "Omaheke",
    "Omusati",
    "Oshana",
    "Oshikoto",
    "Otjozondjupa",
    "Zambezi"
]

@app.route("/api/admin/namibia_regions", methods=["GET"])
def api_admin_namibia_regions():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    return jsonify({"success": True, "rows": NAMIBIA_REGIONS})


@app.route("/api/admin/export_finance_summary_csv", methods=["GET"])
def api_admin_export_finance_summary_csv():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        agents = sb_admin.table("agent_profiles").select("*").limit(5000).execute().data or []
        ledger = sb_admin.table("agent_wallet_ledger").select("*").limit(10000).execute().data or []
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    by_agent = {}
    for row in ledger:
        aid = str(row.get("agent_id") or "")
        if not aid:
            continue
        amt = float(row.get("amount") or 0)
        typ = (row.get("txn_type") or "").lower()
        status = (row.get("status") or "approved").lower()
        if status != "approved":
            continue
        by_agent.setdefault(aid, 0.0)
        if typ == "debit":
            by_agent[aid] -= amt
        else:
            by_agent[aid] += amt

    regions = {}
    for a in agents:
        region = a.get("operation_region") or a.get("town") or "Unknown"
        aid = str(a.get("id") or "")
        bal = by_agent.get(aid, 0.0)
        if region not in regions:
            regions[region] = {"region": region, "agents": 0, "total_due": 0.0}
        regions[region]["agents"] += 1
        regions[region]["total_due"] += bal

    rows = list(regions.values())
    rows.sort(key=lambda x: x["total_due"], reverse=True)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Region", "Agents", "Total Due (N$)"])
    for r in rows:
        writer.writerow([r["region"], r["agents"], round(r["total_due"], 2)])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=finance_summary_by_region.csv"}
    )


@app.route("/api/admin/export_agent_due_csv", methods=["GET"])
def api_admin_export_agent_due_csv():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    town = (request.args.get("town") or "").strip().lower()

    try:
        agents = sb_admin.table("agent_profiles").select("*").limit(5000).execute().data or []
        ledger = sb_admin.table("agent_wallet_ledger").select("*").limit(10000).execute().data or []
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    balances = {}
    for row in ledger:
        aid = str(row.get("agent_id") or "")
        if not aid:
            continue
        amt = float(row.get("amount") or 0)
        typ = (row.get("txn_type") or "").lower()
        status = (row.get("status") or "approved").lower()
        if status != "approved":
            continue
        balances.setdefault(aid, 0.0)
        if typ == "debit":
            balances[aid] -= amt
        else:
            balances[aid] += amt

    out = []
    for a in agents:
        region = (a.get("operation_region") or a.get("town") or "").strip()
        if town and town not in region.lower():
            continue
        aid = str(a.get("id") or "")
        out.append([
            aid,
            a.get("full_name") or "",
            a.get("email") or "",
            a.get("phone") or "",
            region or "Unknown",
            round(balances.get(aid, 0.0), 2)
        ])

    out.sort(key=lambda x: x[5], reverse=True)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Agent ID", "Full Name", "Email", "Phone", "Region", "Amount Due (N$)"])
    for row in out:
        writer.writerow(row)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=agent_payment_due.csv"}
    )


@app.route("/api/admin/broadcast_by_region", methods=["POST"])
def api_admin_broadcast_by_region():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        data = request.get_json(silent=True) or {}
        region = (data.get("region") or "").strip()
        message = (data.get("message") or "").strip()

        if not region:
            return jsonify({"success": False, "error": "Region is required"}), 400
        if not message:
            return jsonify({"success": False, "error": "Message is required"}), 400

        agents = sb_admin.table("agent_profiles").select("*").limit(5000).execute().data or []
        targets = [
            a for a in agents
            if region.lower() in ((a.get("operation_region") or a.get("town") or "").lower())
        ]

        if "broadcast_logs" not in t if False else False:
            pass

        try:
            sb_admin.table("broadcast_logs").insert({
                "target_scope": "region",
                "target_value": region,
                "message": message,
                "sent_count": len(targets),
                "created_by": session.get("email") or "admin"
            }).execute()
        except Exception:
            pass

        return jsonify({
            "success": True,
            "region": region,
            "sent_count": len(targets),
            "rows": [{
                "full_name": a.get("full_name"),
                "email": a.get("email"),
                "phone": a.get("phone"),
                "region": a.get("operation_region") or a.get("town") or ""
            } for a in targets]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)

# --- Agent dashboard full routes ---
from agent_dashboard_full import register_agent_dashboard_routes, register_agent_dashboard_debug_routes
# # # register_agent_dashboard_routes(app, sb_admin, require_login, globals().get("log_system_event"))
# # # register_agent_dashboard_debug_routes(app, sb_admin, require_login)

