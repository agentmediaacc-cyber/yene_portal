from supabase import create_client, Client
from flask_cors import CORS
from flask import Flask, render_template, request, jsonify, session, flash, redirect, url_for, Response
from functools import wraps
import requests
import os
from dotenv import load_dotenv
load_dotenv()

# --- 1. THE FOUNDATION & TOOLS ---
app = Flask(__name__)
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
    return render_template("login.html", login_mode="admin")

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

    return render_template("login.html", login_mode="agent")


@app.route("/dashboard/admin")
@require_login("ADMIN")
def admin_dashboard():
    return render_template("admin_dashboard.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.")
    return redirect(url_for("login"))


@app.route('/agent/dashboard')
@require_login('AGENT')
def agent_dashboard():
    current_status = session.get('status', 'pending_approval')
    return render_template(
    'agent_dashboard.html',
    SUPABASE_URL=URL,
    SUPABASE_ANON_KEY=ANON_KEY,
     AGENT_STATUS=current_status)


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
@require_login("ADMIN")
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


# --- AGENT DASHBOARD API ROUTES ---
@app.route("/api/agent/stats")
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
@require_login("ADMIN")
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
@require_login("ADMIN")
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
@require_login("ADMIN")
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
@require_login("ADMIN")
def api_admin_audit():
    logs = sb_admin.table("system_logs").select("*").order("created_at", desc=True).limit(50).execute().data
    return jsonify({"success": True, "data": logs or []})

@app.route("/api/admin/broadcast/<id>", methods=["PUT", "DELETE"])
@require_login("ADMIN")
def api_admin_manage_bc(id):
    if request.method == "DELETE":
        sb_admin.table("broadcasts").delete().eq("id", id).execute()
    else:
        sb_admin.table("broadcasts").update({"message": request.json.get("message")}).eq("id", id).execute()
    return jsonify({"success": True})

@app.route("/api/admin/drivers/<id>", methods=["PUT", "DELETE"])
@require_login("ADMIN")
def api_admin_manage_driver(id):
    if request.method == "DELETE":
        sb_admin.table("drivers").delete().eq("id", id).execute()
    else:
        sb_admin.table("drivers").update(request.json).eq("id", id).execute()
    return jsonify({"success": True})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)