import csv
import datetime
import io

# -----------------------------
# Supabase token auth (Agent API)
# -----------------------------
import os
import tempfile
from datetime import datetime, timedelta
from functools import wraps
from io import StringIO

import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_cors import CORS
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from supabase import create_client

from admin_agents_live_routes import register_admin_agents_live_routes
from admin_agents_master_routes import register_admin_agents_master_routes
from admin_agents_reject_fix_routes import register_admin_agents_reject_fix_routes
from admin_approval_working_routes import register_admin_approval_working_routes
from admin_broadcast_fix_routes import register_admin_broadcast_fix_routes
from admin_compat_routes import register_admin_compat_routes
from admin_error_fixes_routes import register_admin_error_fixes_routes
from admin_extended_routes import register_admin_extended_routes
from admin_final_routes import register_admin_final_routes
from admin_presence_town_routes import register_admin_presence_town_routes
from admin_workflow_routes import register_admin_workflow_routes
from agent_academy_v1 import register_agent_academy_v1_routes
from agent_dashboard_v4 import register_agent_dashboard_v4_routes
from agent_team_features import register_agent_team_routes
from agent_wallet_v1 import register_agent_wallet_v1_routes


def _sb_get_user_id_from_token(access_token: str):
    """Validate Supabase access token and return user_id (uuid string) or None."""
    url = os.getenv("SUPABASE_URL", "").strip() + "/auth/v1/user"
    apikey = os.getenv("SUPABASE_ANON_KEY", "").strip()
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




def _require_admin():
    return session.get("role") == "ADMIN" or bool(session.get("is_admin"))

def require_login(required_role=None):
    """Early temporary session-based login guard for legacy modules."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            role = (session.get("role") or "").upper()
            if not role:
                return redirect(url_for("login"))
            if required_role and role != str(required_role).upper():
                if role == "ADMIN":
                    return redirect(url_for("admin_dashboard"))
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator

def require_agent_token(fn):
    """Protect /api/agent/* using Supabase auth token instead of Flask session."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        # PUBLIC_DASHBOARD_BYPASS (do not remove)
        if request.path in ["/agent/dashboard", "/dashboard/admin"]:
            return fn(*args, **kwargs)

        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            return (
                jsonify({"ok": False, "error": "Missing Authorization Bearer token"}),
                401,
            )
        token = auth.split(" ", 1)[1].strip()
        uid = _sb_get_user_id_from_token(token)
        if not uid:
            return jsonify({"ok": False, "error": "Invalid/expired token"}), 401

        # Attach for handlers to use
        request.sb_uid = uid
        return fn(*args, **kwargs)

    return wrapper


SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

load_dotenv()

# --- 1. THE FOUNDATION & TOOLS ---
app = Flask(__name__)

# ---------------------------
# PUBLIC DASHBOARD PAGES
# Supabase session is handled in browser.
# Data access must be via /api/* with Bearer token.
# ---------------------------


@app.route("/dashboard/admin")
def admin_dashboard():
    if session.get("role") != "ADMIN":
        return redirect("/admin/login")
    return render_template("admin_dashboard.html")


@app.route("/agent/dashboard")
def agent_dashboard():
    if session.get("role") != "AGENT":
        return redirect("/login")
    return render_template("agent_dashboard.html")


@app.route("/dashboard/agent")
def agent_dashboard_alias():
    return redirect(url_for("agent_dashboard"))


# ---------------------------
# PUBLIC DASHBOARD PAGES
# Supabase session is handled in browser.
# Data access must be via /api/* with Bearer token.
# ---------------------------


# --- Inject Supabase env into all templates (agent login needs this) ---


@app.context_processor
def inject_supabase_env():
    return {
        "SUPABASE_URL": os.getenv("SUPABASE_URL", ""),
        "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY", ""),
    }


# --- end inject ---


app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
CORS(app)

URL = os.getenv("SUPABASE_URL", "https://kcxphxihykonzuagtgke.supabase.co")
ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

supabase = create_client(URL, ANON_KEY)
sb_admin = create_client(URL, SERVICE_KEY) if SERVICE_KEY else supabase

# --- HELPER FUNCTIONS ---



register_agent_team_routes(app, sb_admin)
register_admin_compat_routes(app, sb_admin)
register_admin_presence_town_routes(app, sb_admin)
register_admin_extended_routes(app, sb_admin)
register_admin_final_routes(app, sb_admin)
register_admin_workflow_routes(app, sb_admin)
register_admin_agents_reject_fix_routes(app, sb_admin)
register_admin_agents_master_routes(app, sb_admin)
register_admin_agents_live_routes(app, sb_admin)
register_admin_approval_working_routes(app, sb_admin)
register_admin_error_fixes_routes(app, sb_admin)
register_admin_broadcast_fix_routes(app, sb_admin)


def homepage_stats(sb_admin):
    def _safe_select(
        table, filters=None, cols="*", limit=None, order_col=None, desc=False
    ):
        filters = filters or {}
        try:
            q = sb_admin.table(table).select(cols)
            for k, v in filters.items():
                q = q.eq(k, v)
            if order_col:
                q = q.order(order_col, desc=desc)
            if limit:
                q = q.limit(limit)
            res = q.execute()
            return res.data or []
        except Exception:
            return []

    def _clean(v):
        return str(v or "").strip()

    def _approved(v):
        return _clean(v).upper() in ("ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED")

    def _official_updates():
        rows = _safe_select("broadcasts", {}, "*", 10, "created_at", True)
        out = []
        for r in rows:
            msg = _clean(r.get("message"))
            if not msg:
                continue
            out.append(
                {
                    "date": _clean(r.get("created_at"))[:10],
                    "title": _clean(r.get("title")) or "Official Update",
                    "message": msg,
                }
            )
        if out:
            return out[:5]

        rows = _safe_select("system_logs", {}, "*", 10, "created_at", True)
        for r in rows:
            details = _clean(r.get("details"))
            if not details:
                continue
            out.append(
                {
                    "date": _clean(r.get("created_at"))[:10],
                    "title": _clean(r.get("event_type")) or "Official Update",
                    "message": details,
                }
            )
        if out:
            return out[:5]

        return [
            {
                "date": "2026-04-01",
                "title": "Promotion Time",
                "message": "Windhoek, YENE is coming. Get ready for network growth and driver recruitment.",
            },
            {
                "date": "2026-03-08",
                "title": "Remote Work",
                "message": "We are building great remote work opportunities around Namibia. Join YENE and earn with us.",
            },
            {
                "date": "2026-03-05",
                "title": "Network Growth",
                "message": "Regional expansion continues as we strengthen recruitment, approvals, and team leadership.",
            },
        ]

    def _network_rules():
        return [
            {"icon": "✅", "text": "Only approved agents can earn commissions."},
            {
                "icon": "📵",
                "text": "Duplicate phone registrations are blocked for network integrity.",
            },
            {
                "icon": "🧾",
                "text": "Verified trip tracking supports clear reward calculations.",
            },
            {
                "icon": "💳",
                "text": "Transparent ledger and admin review support fair payouts.",
            },
        ]

    def _regional_rates():
        rows = _safe_select("payment_rules", {}, "*", 200, "updated_at", True)
        out = []
        for r in rows:
            region = _clean(r.get("region"))
            town = _clean(r.get("town"))
            driver = r.get("driver_reg")
            client = r.get("client_reg")
            status = _clean(r.get("status")) or "Active"
            if region or town:
                out.append(
                    {
                        "region": region or "Namibia",
                        "town": town or "General",
                        "driver": driver if driver is not None else 0,
                        "client": client if client is not None else 0,
                        "status": status,
                    }
                )

        if out:
            seen = set()
            unique = []
            for r in out:
                key = (
                    r["region"].lower(),
                    r["town"].lower(),
                    str(r["driver"]),
                    str(r["client"]),
                    r["status"].lower(),
                )
                if key in seen:
                    continue
                seen.add(key)
                unique.append(r)
            return unique[:12]

        return [
            {
                "region": "Erongo",
                "town": "Walvis Bay",
                "driver": 10,
                "client": 10,
                "status": "Active",
            },
            {
                "region": "Erongo",
                "town": "Swakopmund",
                "driver": 10,
                "client": 10,
                "status": "Active",
            },
            {
                "region": "Khomas",
                "town": "Windhoek",
                "driver": 10,
                "client": 10,
                "status": "Active",
            },
            {
                "region": "Kavango East",
                "town": "Rundu",
                "driver": 10,
                "client": 15,
                "status": "Active",
            },
        ]

    agents = _safe_select("agent_profiles", {}, "*", 10000)
    if not agents:
        agents = _safe_select("agents", {}, "*", 10000)

    return {
        "stats": {
            "regions": 14,
            "agents": len(
                [
                    r
                    for r in agents
                    if _approved(r.get("status")) or not _clean(r.get("status"))
                ]
            ),
            "drivers": len(_safe_select("drivers", {}, "*", 10000)),
            "clients": len(_safe_select("clients", {}, "*", 10000)),
        },
        "updates": _official_updates(),
        "rules": _network_rules(),
        "rates": _regional_rates(),
    }


@app.route("/")
def index():
    home = homepage_stats(sb_admin)
    return render_template(
        "index.html",
        stats=home["stats"],
        updates=home["updates"],
        rules=home["rules"],
        rates=home["rates"],
    )


# ---------------------------
# AUTH + DASHBOARD ROUTES
# ---------------------------


@app.route("/admin/login", methods=["GET"])
def admin_login():
    if session.get("role") == "ADMIN":
        return redirect("/dashboard/admin")
    return render_template(
        "login.html",
        SUPABASE_URL=SUPABASE_URL,
        SUPABASE_ANON_KEY=SUPABASE_ANON_KEY,
        login_mode="admin",
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("role") == "AGENT":
            return redirect("/agent/dashboard")
        return render_template(
            "login.html",
            SUPABASE_URL=SUPABASE_URL,
            SUPABASE_ANON_KEY=SUPABASE_ANON_KEY,
            login_mode="agent",
        )

    if request.method == "POST":
        email = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        login_mode = (request.form.get("login_mode") or "agent").strip().lower()

        try:
            if login_mode == "admin":
                admin_check = (
                    sb_admin.table("admin_profiles")
                    .select("email")
                    .eq("email", email)
                    .execute()
                )
                if not admin_check.data:
                    flash("Admin not found.")
                    return redirect(url_for("admin_login"))

                supabase.auth.sign_in_with_password(
                    {"email": email, "password": password}
                )
                session.clear()
                session.permanent = bool(request.form.get("remember_me"))
                session["role"] = "ADMIN"
                session["is_admin"] = True
                session["email"] = email
                log_system_event("LOGIN", f"ADMIN logged in: {email}", user_id=email)
                return redirect("/dashboard/admin")

            # AGENT login
            agent_check = (
                supabase.table("agent_profiles")
                .select("email, status")
                .eq("email", email)
                .execute()
            )
            if not agent_check.data:
                agent_check = (
                    supabase.table("agents")
                    .select("email, status")
                    .eq("email", email)
                    .execute()
                )

            if not agent_check.data:
                flash("Agent account not found.")
                return redirect(url_for("login"))

            status = (agent_check.data[0].get("status") or "").lower()
            supabase.auth.sign_in_with_password({"email": email, "password": password})

            session.clear()
            session.permanent = bool(request.form.get("remember_me"))
            session["role"] = "AGENT"
            session["is_admin"] = False
            session["email"] = email
            session["status"] = status
            log_system_event("LOGIN", f"AGENT logged in: {email}", user_id=email)

            return redirect(url_for("agent_dashboard"))

        except Exception as e:
            flash(f"Login error: {str(e)}")
            return redirect(url_for("login"))

    return render_template(
        "login.html",
        SUPABASE_URL=SUPABASE_URL,
        SUPABASE_ANON_KEY=SUPABASE_ANON_KEY,
        login_mode="agent",
    )


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
            res = sb_admin.auth.admin.create_user(
                {
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "user_metadata": {"full_name": full_name},
                }
            )

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
            ref_email = (
                request.args.get("ref")
                or request.form.get("ref")
                or session.get("agent_ref")
            )
            insert_res = sb_admin.table("agent_profiles").insert(profile_data).execute()
            new_agent_rows = insert_res.data or []
            new_agent = new_agent_rows[0] if new_agent_rows else None

            if ref_email and new_agent:
                try:
                    parent_rows = (
                        sb_admin.table("agent_profiles")
                        .select("id,email,full_name")
                        .eq("email", ref_email)
                        .limit(1)
                        .execute()
                        .data
                        or []
                    )
                    if parent_rows:
                        parent = parent_rows[0]
                        sb_admin.table("agent_referrals").insert(
                            {
                                "parent_agent_id": parent.get("id"),
                                "parent_agent_email": parent.get("email"),
                                "child_agent_id": new_agent.get("id"),
                                "child_agent_email": new_agent.get("email"),
                                "child_agent_name": new_agent.get("full_name"),
                            }
                        ).execute()
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
                    for k in (
                        "auth_method",
                        "phone_number",
                        "national_id",
                        "address",
                        "town",
                        "region",
                        "profile_pic_path",
                        "id_document_path",
                        "gender",
                    ):
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
    if not _require_admin():
        return jsonify({"success": False, "error": "Not authorized"}), 401

    agents1 = sb_admin.table("agent_profiles").select("id,status").execute().data or []
    agents2 = sb_admin.table("agents").select("id,status").execute().data or []
    agents = agents1 + agents2  # Combine them!

    active = len([a for a in agents if (a.get("status") or "").upper() == "ACTIVE"])
    pending = len(
        [
            a
            for a in agents
            if (a.get("status") or "").upper() in ("PENDING", "PENDING_APPROVAL")
        ]
    )
    blocked = len([a for a in agents if (a.get("status") or "").upper() == "BLOCKED"])

    drivers = sb_admin.table("drivers").select("id").execute().data or []
    clients = sb_admin.table("clients").select("id").execute().data or []
    ledger = sb_admin.table("finance_ledger").select("amount").execute().data or []
    total_paid = sum(float(x.get("amount") or 0) for x in ledger)

    return jsonify(
        {
            "success": True,
            "data": {
                "agents_total": len(agents),
                "agents_active": active,
                "agents_pending": pending,
                "agents_blocked": blocked,
                "drivers_total": len(drivers),
                "clients_total": len(clients),
                "total_paid": total_paid,
                "recent_activity": [],
            },
        }
    )


@app.route("/api/admin/agents_auth_stats")
def api_admin_agents_auth_stats():
    if not _require_admin():
        return jsonify({"success": False, "error": "Not authorized"}), 401

    auth_count = 0
    try:
        url = URL.rstrip("/") + "/auth/v1/admin/users"
        headers = {"Authorization": f"Bearer {SERVICE_KEY}", "apikey": SERVICE_KEY}
        r = requests.get(
            url, headers=headers, params={"page": 1, "per_page": 200}, timeout=10
        )
        auth_count = len(r.json() or [])
    except Exception:
        pass

    agents1 = sb_admin.table("agent_profiles").select("id,status").execute().data or []
    agents2 = sb_admin.table("agents").select("id,status").execute().data or []
    agents = agents1 + agents2

    return jsonify(
        {
            "success": True,
            "data": {
                "auth_users_total": auth_count,
                "agents_db_total": len(agents),
                "agents_db_pending": len(
                    [
                        a
                        for a in agents
                        if (a.get("status") or "").upper()
                        in ("PENDING", "PENDING_APPROVAL")
                    ]
                ),
                "agents_db_active": len(
                    [a for a in agents if (a.get("status") or "").upper() == "ACTIVE"]
                ),
                "agents_db_blocked": len(
                    [a for a in agents if (a.get("status") or "").upper() == "BLOCKED"]
                ),
            },
        }
    )


@app.route("/api/admin/agents")
def api_admin_agents():
    if not _require_admin():
        return jsonify({"success": False, "error": "Not authorized"}), 401

    # Pull from both tables
    agents1 = (
        sb_admin.table("agent_profiles").select("*").limit(2000).execute().data or []
    )
    agents2 = sb_admin.table("agents").select("*").limit(2000).execute().data or []

    # Merge them safely by email so we don't get duplicates
    merged = {}
    for a in agents2:
        if a.get("email"):
            merged[a["email"].lower()] = a
    for a in agents1:
        if a.get("email"):
            merged[a["email"].lower()] = a

    agents_list = list(merged.values())

    drivers = (
        sb_admin.table("drivers").select("recruiter_agent_id").execute().data or []
    )
    clients = (
        sb_admin.table("clients").select("recruiter_agent_id").execute().data or []
    )

    counts = {}
    for d in drivers:
        aid = d.get("recruiter_agent_id")
        if aid:
            counts[aid] = counts.get(aid, {"drivers": 0, "clients": 0})
            counts[aid]["drivers"] += 1
    for c in clients:
        aid = c.get("recruiter_agent_id")
        if aid:
            counts[aid] = counts.get(aid, {"drivers": 0, "clients": 0})
            counts[aid]["clients"] += 1

    return jsonify({"success": True, "data": {"agents": agents_list, "counts": counts}})


@app.route("/api/admin/agents/<agent_id>/status", methods=["POST"])
def api_admin_agent_status(agent_id):
    if not _require_admin():
        return jsonify({"success": False, "error": "Not authorized"}), 401
    data = request.json or {}
    status = (data.get("status") or "").upper().strip()

    # Update both tables just to be safe!
    sb_admin.table("agent_profiles").update({"status": status}).eq(
        "id", agent_id
    ).execute()
    sb_admin.table("agents").update({"status": status}).eq("id", agent_id).execute()
    return jsonify({"success": True})


@app.route("/api/admin/approve_agent", methods=["POST"])
def approve_agent():
    data = request.json or {}
    agent_id = (data.get("agent_id") or "").strip()
    status = (data.get("status") or "ACTIVE").strip().upper()

    sb_admin.table("agent_profiles").update({"status": status}).eq(
        "id", agent_id
    ).execute()
    sb_admin.table("agents").update({"status": status}).eq("id", agent_id).execute()
    return jsonify({"success": True, "message": f"Agent status updated to {status}"})


# Leave the other basic routes (drivers, clients, finance, rules, etc.)
# here as they were.


@app.route("/api/admin/drivers")
def api_admin_drivers():
    return jsonify(
        {
            "success": True,
            "data": sb_admin.table("drivers").select("*").limit(5000).execute().data
            or [],
        }
    )


@app.route("/api/admin/clients")
def api_admin_clients():
    return jsonify(
        {
            "success": True,
            "data": sb_admin.table("clients").select("*").limit(5000).execute().data
            or [],
        }
    )


@app.route("/api/admin/finance")
def api_admin_finance():
    return jsonify(
        {
            "success": True,
            "data": sb_admin.table("finance_ledger")
            .select("*")
            .limit(5000)
            .execute()
            .data
            or [],
        }
    )


@app.route("/api/admin/payment_rules")
def api_admin_payment_rules():
    return jsonify(
        {
            "success": True,
            "data": sb_admin.table("payment_rules").select("*").execute().data or [],
        }
    )


@app.route("/api/admin/broadcasts")
def api_admin_broadcasts():
    return jsonify(
        {
            "success": True,
            "data": sb_admin.table("broadcasts").select("*").execute().data or [],
        }
    )


@app.route("/admin")
def admin_entry():
    return redirect(url_for("admin_dashboard"))

    # --- DUPLICATE CHECK ---
    existing = (
        sb_admin.table("drivers").select("id").eq("phone_number", phone).execute().data
    )
    if not existing:
        existing = (
            sb_admin.table("drivers").select("id").eq("phone", phone).execute().data
        )
    if existing:
        return jsonify(
            {
                "success": False,
                "error": "A driver with this phone number is already registered in the network!",
            }
        )

    email = session.get("email")
    agent = (
        sb_admin.table("agent_profiles")
        .select("id, full_name")
        .eq("email", email)
        .execute()
        .data
    )
    if not agent:
        agent = (
            sb_admin.table("agents")
            .select("id, full_name")
            .eq("email", email)
            .execute()
            .data
        )
    if not agent:
        return jsonify({"success": False, "error": "Agent not found"})

    aid = agent[0]["id"]
    aname = agent[0].get("full_name", "Unknown")

    try:
        sb_admin.table("drivers").insert(
            {
                "full_name": data.get("full_name"),
                "phone": phone,
                "phone_number": phone,
                "town": data.get("town"),
                "license_number": "PENDING",
                "car_details": "PENDING",
                "recruiter_agent_id": aid,
                "recruiter_name": aname,
                "status": "pending_approval",
            }
        ).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/agent/register_client_legacy", methods=["POST"])
def api_agent_register_client_legacy():
    data = request.json
    phone = (data.get("phone") or "").strip()

    # --- DUPLICATE CHECK ---
    existing = (
        sb_admin.table("clients").select("id").eq("phone_number", phone).execute().data
    )
    if not existing:
        existing = (
            sb_admin.table("clients").select("id").eq("phone", phone).execute().data
        )
    if existing:
        return jsonify(
            {
                "success": False,
                "error": "A client with this phone number is already registered in the network!",
            }
        )

    email = session.get("email")
    agent = (
        sb_admin.table("agent_profiles")
        .select("id, full_name")
        .eq("email", email)
        .execute()
        .data
    )
    if not agent:
        agent = (
            sb_admin.table("agents")
            .select("id, full_name")
            .eq("email", email)
            .execute()
            .data
        )
    if not agent:
        return jsonify({"success": False, "error": "Agent not found"})

    try:
        sb_admin.table("clients").insert(
            {
                "full_name": data.get("full_name"),
                "phone": phone,
                "phone_number": phone,
                "yene_code": "PENDING",
                "recruiter_agent_id": agent[0]["id"],
                "recruiter_name": agent[0].get("full_name", "Unknown"),
                "status": "pending_approval",
            }
        ).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# --- ADMIN FINANCE & BROADCAST ROUTES ---
@app.route("/api/admin/payment_rules", methods=["POST"])
def api_admin_save_rule():
    data = request.json
    try:
        # Check if a rule already exists for this exact region and town
        existing = (
            sb_admin.table("payment_rules")
            .select("id")
            .eq("region", data.get("region", ""))
            .eq("town", data.get("town", ""))
            .execute()
            .data
        )
        if existing:
            # Update the existing rule
            sb_admin.table("payment_rules").update(data).eq(
                "id", existing[0]["id"]
            ).execute()
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
        sb_admin.table("broadcasts").insert(
            {"message": data.get("message"), "target_region": "ALL"}
        ).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/public/broadcasts", methods=["GET"])
def api_public_broadcasts():
    try:
        # Grab the 3 newest broadcasts for the landing page
        data = (
            sb_admin.table("broadcasts")
            .select("*")
            .order("created_at", desc=True)
            .limit(3)
            .execute()
            .data
            or []
        )
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
            sb_admin.table("broadcasts").update({"message": data.get("message")}).eq(
                "id", id
            ).execute()
            return jsonify({"success": True, "message": "Broadcast updated"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# --- ADMIN EXTENDED FEATURES ---
@app.route("/api/admin/audit_logs")
def api_admin_audit():
    logs = (
        sb_admin.table("system_logs")
        .select("*")
        .order("created_at", desc=True)
        .limit(50)
        .execute()
        .data
    )
    return jsonify({"success": True, "data": logs or []})


@app.route("/api/admin/broadcast/<id>", methods=["PUT", "DELETE"])
def api_admin_manage_bc(id):
    if request.method == "DELETE":
        sb_admin.table("broadcasts").delete().eq("id", id).execute()
    else:
        sb_admin.table("broadcasts").update(
            {"message": request.json.get("message")}
        ).eq("id", id).execute()
    return jsonify({"success": True})


@app.route("/api/admin/drivers/<id>", methods=["PUT", "DELETE"])
def api_admin_manage_driver(id):
    if request.method == "DELETE":
        sb_admin.table("drivers").delete().eq("id", id).execute()
    else:
        sb_admin.table("drivers").update(request.json).eq("id", id).execute()
    return jsonify({"success": True})


@app.get("/api/debug-session")
def debug_session():
    # shows keys & value types only (no full secrets)
    out = {}
    for k, v in session.items():
        if v is None:
            out[k] = None
        else:
            val = str(v)
            out[k] = {"type": type(v).__name__, "len": len(val), "preview": val[:8]}
    return jsonify({"ok": True, "session": out})


# === AGENT V2 LIVE ROUTES ===


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

    return jsonify({"ok": True, "user_id": uid, "email": email, "profile": prof})


@app.get("/api/agent/summary_v2")
def api_agent_summary_v2():
    user = _verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _find_agent_profile(uid, email)
    if not prof:
        return jsonify(
            {
                "ok": True,
                "drivers_week": 0,
                "clients_week": 0,
                "drivers_all": 0,
                "profile": None,
            }
        )

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

    drivers_all = get_count(f"/drivers?select=id&recruiter_agent_id=eq.{agent_id}")

    return jsonify(
        {
            "ok": True,
            "week_start": monday.isoformat(),
            "week_end": sunday.isoformat(),
            "drivers_week": drivers_week,
            "clients_week": clients_week,
            "drivers_all": drivers_all,
            "profile": prof,
        }
    )


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
        timeout=10,
    )
    if r1.status_code == 200:
        for x in r1.json():
            rows.append(
                {
                    "subject_type": "driver",
                    "full_name": x.get("full_name"),
                    "phone": x.get("phone"),
                    "town": x.get("town"),
                    "external_code": "",
                    "created_at": x.get("created_at"),
                }
            )

    r2 = requests.get(
        _rest_url(
            f"/clients?select=full_name,phone,created_at"
            f"&recruiter_agent_id=eq.{agent_id}"
            f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
            f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
            f"&order=created_at.desc&limit=20"
        ),
        headers=_rest_headers(),
        timeout=10,
    )
    if r2.status_code == 200:
        for x in r2.json():
            rows.append(
                {
                    "subject_type": "client",
                    "full_name": x.get("full_name"),
                    "phone": x.get("phone"),
                    "town": "",
                    "external_code": "",
                    "created_at": x.get("created_at"),
                }
            )

    rows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return jsonify({"ok": True, "rows": rows[:30], "profile": prof})


@app.post("/api/agent/register_driver_v2_working_legacy")
def api_agent_register_driver_v2_working_legacy():
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
        "recruiter_name": prof.get("full_name")
        or prof.get("username")
        or prof.get("email"),
        "full_name": (j.get("full_name") or "").strip(),
        "phone": (j.get("phone") or "").strip(),
        "town": (j.get("town") or "").strip(),
    }

    if not payload["full_name"] or not payload["phone"]:
        return jsonify({"ok": False, "error": "Missing full_name / phone"}), 400

    r = requests.post(
        _rest_url("/drivers"), headers=_rest_headers(), json=payload, timeout=10
    )
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
        "recruiter_name": prof.get("full_name")
        or prof.get("username")
        or prof.get("email"),
        "full_name": (j.get("full_name") or "").strip(),
        "phone": (j.get("phone") or "").strip(),
    }

    if not payload["full_name"] or not payload["phone"]:
        return jsonify({"ok": False, "error": "Missing full_name / phone"}), 400

    r = requests.post(
        _rest_url("/clients"), headers=_rest_headers(), json=payload, timeout=10
    )
    if r.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r.text}), 500

    return jsonify({"ok": True, "row": r.json()[0], "profile": prof})


# === AGENT V4 UPGRADE ===


def _v4_monday_from_string(s):
    return datetime.date.fromisoformat(s)


def _v4_group_week_rows(rows):
    grouped = {}
    for x in rows:
        ws = x.get("week_start") or "NO_WEEK"
        we = x.get("week_end") or "NO_WEEK"
        key = f"{ws}|{we}"
        grouped.setdefault(
            key,
            {
                "week_start": ws,
                "week_end": we,
                "credits": 0.0,
                "debits": 0.0,
                "net": 0.0,
                "items": [],
            },
        )
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
        _fa_rest(
            f"/agent_registrations?select=created_at,subject_type&agent_auth_id=eq.{uid}&order=created_at.desc&limit=1000"
        ),
        headers=_fa_headers(),
        timeout=15,
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
        weeks.setdefault(
            key,
            {
                "week_start": monday.isoformat(),
                "week_end": sunday.isoformat(),
                "drivers": 0,
                "clients": 0,
            },
        )
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
        _fa_rest(
            f"/agent_wallet_ledger?select=week_start,week_end,entry_type,amount,reference,note,created_at&agent_auth_id=eq.{uid}&order=week_start.desc,created_at.desc&limit=500"
        ),
        headers=_fa_headers(),
        timeout=15,
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
        _fa_rest(
            f"/agent_wallet_ledger?select=created_at,week_start,week_end,entry_type,amount,reference,note&agent_auth_id=eq.{uid}&week_start=eq.{week_start}&order=created_at.asc"
        ),
        headers=_fa_headers(),
        timeout=15,
    )
    rows = r.json() if r.status_code == 200 else []

    if rows:
        week_end = rows[0].get("week_end") or ""
    else:
        monday = _v4_monday_from_string(week_start)
        week_end = (monday + datetime.timedelta(days=6)).isoformat()

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    agent_name = (
        (prof or {}).get("full_name")
        or (prof or {}).get("username")
        or email
        or "Agent"
    )
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
    return send_file(
        tmp.name, as_attachment=True, download_name=filename, mimetype="application/pdf"
    )


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
        _fa_rest(
            f"/agent_profiles?select=full_name,email,phone,role,created_at,referred_by_code&referred_by_code=eq.{referral_code}&order=created_at.desc&limit=200"
        ),
        headers=_fa_headers(),
        timeout=15,
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
    return jsonify(
        {
            "ok": True,
            "auth_user_id": uid,
            "profile_found": True if prof else False,
            "profile": prof,
        }
    )



@app.get("/debug/env")
def debug_env():
    # SAFE: only shows whether env vars exist, never prints secrets
    return {
        "SUPABASE_URL_set": bool(os.getenv("SUPABASE_URL")),
        "SUPABASE_ANON_KEY_set": bool(os.getenv("SUPABASE_ANON_KEY")),
        "runtime": "ok",
    }


@app.get("/api/public-config")
def public_config():
    url = (os.getenv("SUPABASE_URL") or "").strip()
    anon = (os.getenv("SUPABASE_ANON_KEY") or "").strip()

    if not url or not anon:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Missing SUPABASE_URL or SUPABASE_ANON_KEY on server",
                    "SUPABASE_URL_len": len(url),
                    "SUPABASE_ANON_KEY_len": len(anon),
                }
            ),
            500,
        )

    return jsonify({"ok": True, "SUPABASE_URL": url, "SUPABASE_ANON_KEY": anon})



@app.get("/debug/admin-check")
def debug_admin_check():
    email = request.args.get("email", "").strip().lower()
    try:
        data = sb_admin.table("admin_profiles").select("email").eq("email", email).execute()
        return jsonify({
            "ok": True,
            "email": email,
            "rows": len(data.data or []),
            "service_key_set": bool(SERVICE_KEY),
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "email": email,
            "service_key_set": bool(SERVICE_KEY),
            "error": str(e),
        }), 500


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

    # Return ok even if no user; frontend should use Supabase session for
    # identity.
    return (
        jsonify(
            {
                "ok": True,
                "has_supabase_env": bool(url and anon),
                "note": "Use Supabase auth session in browser. This endpoint prevents 404 login loops.",
                "path": request.path,
            }
        ),
        200,
    )


@app.get("/api/routes")
def api_routes():
    out = []
    for r in sorted(app.url_map.iter_rules(), key=lambda x: str(x)):
        if r.endpoint != "static":
            out.append(
                {
                    "methods": sorted(list(r.methods)),
                    "rule": r.rule,
                    "endpoint": r.endpoint,
                }
            )
    return jsonify({"ok": True, "count": len(out), "routes": out})


@app.get("/api/whoami")
def api_whoami():
    token = request.headers.get("Authorization", "").replace("Bearer", "").strip()
    if not token:
        return jsonify({"ok": False, "error": "Missing Bearer token"}), 401

    url = os.getenv("SUPABASE_URL", "").strip()
    anon = os.getenv("SUPABASE_ANON_KEY", "").strip()
    if not url or not anon:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Server missing SUPABASE_URL or SUPABASE_ANON_KEY",
                }
            ),
            500,
        )

    # Validate token with Supabase Auth
    r = requests.get(
        url.rstrip("/") + "/auth/v1/user",
        headers={"Authorization": f"Bearer {token}", "apikey": anon},
        timeout=15,
    )
    if r.status_code != 200:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "Invalid session",
                    "status": r.status_code,
                    "body": r.text[:200],
                }
            ),
            401,
        )

    user = r.json()
    uid = user.get("id")
    email = user.get("email")

    # Lookup in agent_profiles using any of the columns you may have
    # (your table includes auth_id + user_id + email)
    try:
        q = (
            supabase.table("agent_profiles")
            .select("id, full_name, status, role, auth_id, user_id, email")
            .or_(f"auth_id.eq.{uid},user_id.eq.{uid},email.eq.{email}")
            .limit(1)
            .execute()
        )
        prof = (q.data or [None])[0]
    except Exception as e:
        return (
            jsonify({"ok": False, "error": "DB lookup failed", "detail": str(e)}),
            500,
        )

    role = (prof or {}).get("role") or "agent"
    return jsonify(
        {"ok": True, "role": role, "user_id": uid, "email": email, "profile": prof}
    )


# === CORE AGENT PORTAL API (V1) ===


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
        "Prefer": "return=representation",
    }


def week_bounds_local(today=None):
    # Mon-Sun
    if today is None:
        today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday


@app.get("/api/agent/me_v1")
@require_supabase_user
def api_agent_me_v1(user):
    uid = user["id"]

    # load profile (service role)
    q = f"/agent_profiles?select=auth_id,full_name,phone,email,role,town,region,status,referral_code&auth_id=eq.{uid}&limit=1"
    r = requests.get(sr_postgrest(q), headers=sr_headers(), timeout=10)
    prof = r.json()[0] if r.status_code == 200 and r.json() else None

    # If no profile exists yet, return minimal data (still logged in)
    return jsonify(
        {"ok": True, "auth_id": uid, "email": user.get("email"), "profile": prof}
    )


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

    return jsonify(
        {
            "ok": True,
            "week_start": monday.isoformat(),
            "week_end": sunday.isoformat(),
            "drivers_week": drivers_week,
            "clients_week": clients_week,
            "drivers_all": drivers_all,
        }
    )


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
        return (
            jsonify({"ok": False, "error": "Missing full_name / phone / driver_code"}),
            400,
        )

    r = requests.post(
        sr_postgrest("/agent_registrations"),
        headers=sr_headers(),
        json=payload,
        timeout=10,
    )
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
        return (
            jsonify({"ok": False, "error": "Missing full_name / phone / client_code"}),
            400,
        )

    r = requests.post(
        sr_postgrest("/agent_registrations"),
        headers=sr_headers(),
        json=payload,
        timeout=10,
    )
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
    return jsonify(
        {
            "ok": True,
            "week_start": monday.isoformat(),
            "week_end": sunday.isoformat(),
            "rows": rows,
        }
    )


# =========================
# FORCE-ADDED AGENT V3 ROUTES
# =========================


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
        "Prefer": "return=representation",
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
            headers={"Authorization": f"Bearer {token}", "apikey": _FA_SUPABASE_ANON},
            timeout=10,
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
        drivers_week = max(
            drivers_week,
            _count(
                f"/drivers?select=id&recruiter_agent_id=eq.{aid}"
                f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
                f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
            ),
        )
        clients_week = max(
            clients_week,
            _count(
                f"/clients?select=id&recruiter_agent_id=eq.{aid}"
                f"&created_at=gte.{monday.isoformat()}T00:00:00Z"
                f"&created_at=lte.{sunday.isoformat()}T23:59:59Z"
            ),
        )
        drivers_all = max(
            drivers_all, _count(f"/drivers?select=id&recruiter_agent_id=eq.{aid}")
        )

    return jsonify(
        {
            "ok": True,
            "week_start": monday.isoformat(),
            "week_end": sunday.isoformat(),
            "drivers_week": drivers_week,
            "clients_week": clients_week,
            "drivers_all": drivers_all,
            "profile": prof,
        }
    )


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
        _fa_rest(
            f"/agent_registrations?select=subject_type,full_name,phone,town,external_code,created_at&agent_auth_id=eq.{uid}&order=created_at.desc&limit=30"
        ),
        headers=_fa_headers(),
        timeout=10,
    )
    if r.status_code == 200:
        rows.extend(r.json())

    if prof and prof.get("id"):
        aid = prof["id"]
        r1 = requests.get(
            _fa_rest(
                f"/drivers?select=full_name,phone,town,created_at&recruiter_agent_id=eq.{aid}&order=created_at.desc&limit=20"
            ),
            headers=_fa_headers(),
            timeout=10,
        )
        if r1.status_code == 200:
            for x in r1.json():
                rows.append(
                    {
                        "subject_type": "driver",
                        "full_name": x.get("full_name"),
                        "phone": x.get("phone"),
                        "town": x.get("town"),
                        "external_code": "",
                        "created_at": x.get("created_at"),
                    }
                )

        r2 = requests.get(
            _fa_rest(
                f"/clients?select=full_name,phone,created_at&recruiter_agent_id=eq.{aid}&order=created_at.desc&limit=20"
            ),
            headers=_fa_headers(),
            timeout=10,
        )
        if r2.status_code == 200:
            for x in r2.json():
                rows.append(
                    {
                        "subject_type": "client",
                        "full_name": x.get("full_name"),
                        "phone": x.get("phone"),
                        "town": "",
                        "external_code": "",
                        "created_at": x.get("created_at"),
                    }
                )

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
        return (
            jsonify({"ok": False, "error": "Agent profile not linked to auth account"}),
            400,
        )

    j = request.get_json(force=True) or {}
    full_name = (j.get("full_name") or "").strip()
    phone = (j.get("phone") or "").strip()
    town = (j.get("town") or "").strip()
    code = (j.get("driver_code") or "").strip()

    if not full_name or not phone or not code:
        return (
            jsonify({"ok": False, "error": "Missing full_name / phone / driver_code"}),
            400,
        )

    payload = {
        "agent_auth_id": uid,
        "subject_type": "driver",
        "full_name": full_name,
        "phone": phone,
        "town": town,
        "external_code": code,
    }
    r = requests.post(
        _fa_rest("/agent_registrations"),
        headers=_fa_headers(),
        json=payload,
        timeout=10,
    )
    if r.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r.text}), 500

    # optional legacy mirror
    try:
        old_payload = {
            "recruiter_agent_id": prof["id"],
            "recruiter_auth_id": uid,
            "recruiter_name": prof.get("full_name")
            or prof.get("username")
            or prof.get("email"),
            "full_name": full_name,
            "phone": phone,
            "town": town,
        }
        requests.post(
            _fa_rest("/drivers"), headers=_fa_headers(), json=old_payload, timeout=10
        )
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
        return (
            jsonify({"ok": False, "error": "Agent profile not linked to auth account"}),
            400,
        )

    j = request.get_json(force=True) or {}
    full_name = (j.get("full_name") or "").strip()
    phone = (j.get("phone") or "").strip()
    town = (j.get("town") or "").strip()
    code = (j.get("client_code") or "").strip()

    if not full_name or not phone or not code:
        return (
            jsonify({"ok": False, "error": "Missing full_name / phone / client_code"}),
            400,
        )

    payload = {
        "agent_auth_id": uid,
        "subject_type": "client",
        "full_name": full_name,
        "phone": phone,
        "town": town,
        "external_code": code,
    }
    r = requests.post(
        _fa_rest("/agent_registrations"),
        headers=_fa_headers(),
        json=payload,
        timeout=10,
    )
    if r.status_code not in (200, 201):
        return jsonify({"ok": False, "error": "Insert failed", "detail": r.text}), 500

    try:
        old_payload = {
            "recruiter_agent_id": prof["id"],
            "recruiter_auth_id": uid,
            "recruiter_name": prof.get("full_name")
            or prof.get("username")
            or prof.get("email"),
            "full_name": full_name,
            "phone": phone,
        }
        requests.post(
            _fa_rest("/clients"), headers=_fa_headers(), json=old_payload, timeout=10
        )
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
        _fa_rest(
            f"/agent_wallet_ledger?select=entry_type,amount,week_start,week_end,reference,note,created_at&agent_auth_id=eq.{uid}&order=created_at.desc&limit=200"
        ),
        headers=_fa_headers(),
        timeout=10,
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
        _fa_rest(
            f"/agent_wallet_ledger?select=week_start,week_end,entry_type,amount,reference,note,created_at&agent_auth_id=eq.{uid}&order=week_start.desc,created_at.desc&limit=300"
        ),
        headers=_fa_headers(),
        timeout=10,
    )
    rows = r.json() if r.status_code == 200 else []
    grouped = {}
    for x in rows:
        ws = x.get("week_start") or "NO_WEEK"
        we = x.get("week_end") or "NO_WEEK"
        key = f"{ws}|{we}"
        grouped.setdefault(
            key,
            {
                "week_start": ws,
                "week_end": we,
                "credits": 0,
                "debits": 0,
                "net": 0,
                "items": [],
            },
        )
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
        _fa_rest(
            f"/agent_wallet_ledger?select=created_at,week_start,week_end,entry_type,amount,reference,note&agent_auth_id=eq.{uid}&week_start=eq.{week_start}&order=created_at.asc"
        ),
        headers=_fa_headers(),
        timeout=10,
    )
    rows = r.json() if r.status_code == 200 else []

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(
        [
            "created_at",
            "week_start",
            "week_end",
            "entry_type",
            "amount",
            "reference",
            "note",
        ]
    )
    for x in rows:
        w.writerow(
            [
                x.get("created_at"),
                x.get("week_start"),
                x.get("week_end"),
                x.get("entry_type"),
                x.get("amount"),
                x.get("reference"),
                x.get("note"),
            ]
        )

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=agent_invoice_{week_start}.csv"
        },
    )


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
        _fa_rest(
            f"/drivers?select=full_name,phone,town,created_at&recruiter_agent_id=eq.{aid}&order=created_at.desc&limit=200"
        ),
        headers=_fa_headers(),
        timeout=10,
    )
    drivers = r1.json() if r1.status_code == 200 else []

    r2 = requests.get(
        _fa_rest(
            f"/agent_driver_trip_updates?select=driver_phone,driver_name,trips,bonus_amount,week_start,week_end,admin_note&agent_auth_id=eq.{uid}&week_start=eq.{monday.isoformat()}&limit=300"
        ),
        headers=_fa_headers(),
        timeout=10,
    )
    trip_rows = r2.json() if r2.status_code == 200 else []
    trip_map = {(x.get("driver_phone") or ""): x for x in trip_rows}

    rows = []
    for d in drivers:
        phone = d.get("phone") or ""
        t = trip_map.get(phone, {})
        rows.append(
            {
                "full_name": d.get("full_name"),
                "phone": phone,
                "town": d.get("town"),
                "registered_at": d.get("created_at"),
                "trips_this_week": t.get("trips", 0),
                "bonus_amount": t.get("bonus_amount", 0),
                "admin_note": t.get("admin_note", ""),
            }
        )

    return jsonify(
        {
            "ok": True,
            "week_start": monday.isoformat(),
            "week_end": sunday.isoformat(),
            "rows": rows,
        }
    )


# === NEW AGENT AUTOLINK FIX ===


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
                timeout=10,
            )

            # re-read linked row
            r3 = requests.get(_fa_rest(q1), headers=_fa_headers(), timeout=10)
            if r3.status_code == 200 and r3.json():
                return r3.json()[0]
        return prof

    return None


# --- AGENT DASHBOARD API ROUTES ---


@app.route("/api/agent/weekly_stats")
# TEMP DISABLED: @require_login("AGENT")
def api_agent_weekly():
    email = session.get("email")
    agent = (
        sb_admin.table("agent_profiles").select("*").eq("email", email).execute().data
    )
    if not agent:
        return jsonify({"success": False})

    agent_data = agent[0]
    aid = agent_data["id"]
    region = agent_data.get("region", "Khomas")

    # Calculate Monday to Sunday of the current week
    today = datetime.today()
    monday = today - timedelta(days=today.weekday())
    monday_str = monday.strftime("%Y-%m-%d")

    # Fetch ALL-TIME data for the Gamification Badges
    all_d = (
        sb_admin.table("drivers")
        .select("id", count="exact")
        .eq("recruiter_agent_id", aid)
        .execute()
        .count
        or 0
    )
    all_c = (
        sb_admin.table("clients")
        .select("id", count="exact")
        .eq("recruiter_agent_id", aid)
        .execute()
        .count
        or 0
    )

    # Fetch WEEKLY data for the Earnings Ledger
    week_d = (
        sb_admin.table("drivers")
        .select("*")
        .eq("recruiter_agent_id", aid)
        .gte("created_at", monday_str)
        .execute()
        .data
        or []
    )
    week_c = (
        sb_admin.table("clients")
        .select("*")
        .eq("recruiter_agent_id", aid)
        .gte("created_at", monday_str)
        .execute()
        .data
        or []
    )

    # Dynamically calculate earnings based on Admin Pricing Rules
    rules = (
        sb_admin.table("payment_rules").select("*").eq("region", region).execute().data
    )
    # Default 50 if admin hasn't set it
    d_rate = rules[0]["driver_reg"] if rules else 50
    # Default 10 if admin hasn't set it
    c_rate = rules[0]["client_reg"] if rules else 10

    earnings = (len(week_d) * d_rate) + (len(week_c) * c_rate)

    # Build the recent weekly activity table
    recent = []
    for d in week_d:
        recent.append(
            {
                "date": d["created_at"],
                "type": "Driver",
                "name": d.get("full_name"),
                "town": d.get("town"),
            }
        )
    for c in week_c:
        recent.append(
            {
                "date": c["created_at"],
                "type": "Client",
                "name": c.get("full_name"),
                "town": "Network",
            }
        )

    recent = sorted(recent, key=lambda x: x["date"], reverse=True)

    return jsonify(
        {
            "success": True,
            "weekly_earnings": earnings,
            "weekly_drivers": len(week_d),
            "weekly_clients": len(week_c),
            "total_drivers": all_d,
            "total_clients": all_c,
            "recent": recent,
        }
    )


@app.route("/api/agent/register_driver_legacy", methods=["POST"])
def api_agent_register_driver_legacy():
    data = request.json
    email = session.get("email")
    agent = (
        sb_admin.table("agent_profiles")
        .select("id, full_name")
        .eq("email", email)
        .execute()
        .data
    )
    if not agent:
        return jsonify({"success": False, "error": "Agent not found"})

    phone = data.get("phone")
    dup = sb_admin.table("drivers").select("id").eq("phone", phone).execute()
    if dup.data:
        return jsonify({"success": False, "error": "Phone number already in system!"})

    try:
        sb_admin.table("drivers").insert(
            {
                "full_name": data.get("full_name"),
                "phone": phone,
                "phone_number": phone,
                "town": data.get("town"),
                "recruiter_agent_id": agent[0]["id"],
                "recruiter_name": agent[0]["full_name"],
                "status": "pending_approval",
                "license_number": "PENDING",
                "car_details": "PENDING",
            }
        ).execute()
        # Log to Admin Command Center
        log_system_event(
            "REGISTER",
            f"Agent {agent[0]['full_name']} registered driver {data.get('full_name')}",
        )
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


register_agent_dashboard_v4_routes(
    app, sb_admin, require_login, globals().get("log_system_event")
)


register_agent_academy_v1_routes(app, sb_admin, require_login)


register_agent_wallet_v1_routes(app, sb_admin, require_login)


def _admin_credit_activation_bonus(agent_id, bonus_key, description, reference_no):
    try:
        if not agent_id:
            return

        bonus_rows = (
            sb_admin.table("agent_bonus_settings")
            .select("*")
            .eq("bonus_key", bonus_key)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not bonus_rows:
            return

        row = bonus_rows[0]
        if not row.get("is_enabled", True):
            return

        amount = float(row.get("amount") or 0)
        if amount <= 0:
            return

        dup = (
            sb_admin.table("agent_wallet_ledger")
            .select("id")
            .eq("reference_no", reference_no)
            .limit(1)
            .execute()
            .data
            or []
        )
        if dup:
            return

        prof = (
            sb_admin.table("agent_profiles")
            .select("email")
            .eq("id", agent_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        agent_email = prof[0].get("email") if prof else None

        sb_admin.table("agent_wallet_ledger").insert(
            {
                "agent_id": str(agent_id),
                "agent_email": agent_email,
                "txn_type": "credit",
                "amount": amount,
                "description": description,
                "reference_no": reference_no,
                "status": "approved",
            }
        ).execute()
    except Exception as e:
        print("Activation bonus credit failed:", e)


@app.route("/api/admin/approve_driver/<driver_id>", methods=["POST"])
def api_admin_approve_driver(driver_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        rows = (
            sb_admin.table("drivers")
            .select("*")
            .eq("id", driver_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            return jsonify({"success": False, "error": "Driver not found"}), 404

        driver = rows[0]
        sb_admin.table("drivers").update({"status": "approved"}).eq(
            "id", driver_id
        ).execute()

        recruiter_agent_id = driver.get("recruiter_agent_id")
        _admin_credit_activation_bonus(
            recruiter_agent_id,
            "driver_activation",
            f"Driver activation bonus for {driver.get('full_name') or 'driver'}",
            f"driver-activation-{driver_id}",
        )

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/reject_driver/<driver_id>", methods=["POST"])
def api_admin_reject_driver(driver_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        sb_admin.table("drivers").update({"status": "rejected"}).eq(
            "id", driver_id
        ).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/approve_client/<client_id>", methods=["POST"])
def api_admin_approve_client(client_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        rows = (
            sb_admin.table("clients")
            .select("*")
            .eq("id", client_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            return jsonify({"success": False, "error": "Client not found"}), 404

        client = rows[0]
        sb_admin.table("clients").update({"status": "approved"}).eq(
            "id", client_id
        ).execute()

        recruiter_agent_id = client.get("recruiter_agent_id")
        _admin_credit_activation_bonus(
            recruiter_agent_id,
            "client_activation",
            f"Client activation bonus for {client.get('full_name') or client.get('phone_number') or 'client'}",
            f"client-activation-{client_id}",
        )

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/reject_client/<client_id>", methods=["POST"])
def api_admin_reject_client(client_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        sb_admin.table("clients").update({"status": "rejected"}).eq(
            "id", client_id
        ).execute()
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
            .data
            or []
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
            .data
            or []
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
            .data
            or []
        )
        if q:
            rows = [
                r
                for r in rows
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
            .data
            or []
        )
        if q:
            rows = [
                r
                for r in rows
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

        agent_rows = (
            sb_admin.table("agent_profiles")
            .select("*")
            .eq("id", agent_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not agent_rows:
            return jsonify({"success": False, "error": "Agent not found"}), 404

        sb_admin.table("agent_profiles").update({"pin": new_pin}).eq(
            "id", agent_id
        ).execute()

        return jsonify(
            {
                "success": True,
                "message": "PIN reset successfully",
                "agent_id": agent_id,
                "agent_name": agent_rows[0].get("full_name") or "",
                "agent_email": agent_rows[0].get("email") or "",
                "temporary_pin": new_pin,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/agent/presence_ping", methods=["POST"])
# TEMP DISABLED: @require_login("AGENT")
def api_agent_presence_ping():
    try:
        email = session.get("email")
        if not email:
            return jsonify({"ok": False, "error": "Missing session email"}), 401

        agent_rows = (
            sb_admin.table("agent_profiles")
            .select("*")
            .eq("email", email)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not agent_rows:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        agent = agent_rows[0]
        data = request.get_json(silent=True) or {}

        payload = {
            "agent_id": str(agent.get("id") or ""),
            "agent_email": agent.get("email"),
            "agent_name": agent.get("full_name") or agent.get("email"),
            "page_name": (data.get("page_name") or "agent_dashboard").strip(),
            "operation_region": agent.get("operation_region") or "",
            "town": agent.get("town") or "",
            "is_online": True,
            "last_seen": datetime.utcnow().isoformat() + "Z",
        }

        sb_admin.table("agent_presence").upsert(
            payload, on_conflict="agent_id"
        ).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/admin/online_agents", methods=["GET"])
def api_admin_online_agents():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        rows = (
            sb_admin.table("agent_presence")
            .select("*")
            .order("last_seen", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )

        online_rows = []
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)

        for r in rows:
            last_seen_raw = r.get("last_seen")
            is_online = False
            if last_seen_raw:
                try:
                    dt = datetime.fromisoformat(
                        str(last_seen_raw).replace("Z", "+00:00")
                    )
                    if now - dt <= timedelta(minutes=3):
                        is_online = True
                except Exception:
                    pass
            if is_online:
                online_rows.append(r)

        return jsonify({"success": True, "rows": online_rows})
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
        agents = (
            sb_admin.table("agent_profiles").select("*").limit(5000).execute().data
            or []
        )
        drivers = sb_admin.table("drivers").select("*").limit(5000).execute().data or []
        clients = sb_admin.table("clients").select("*").limit(5000).execute().data or []
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    def match_town(v):
        return town in ((v or "").strip().lower())

    if town:
        agents = [
            a
            for a in agents
            if match_town(a.get("town")) or match_town(a.get("operation_region"))
        ]
        drivers = [d for d in drivers if match_town(d.get("town"))]
        clients = [c for c in clients if match_town(c.get("town"))]
    else:
        town = "all"

    return jsonify(
        {
            "success": True,
            "town": town,
            "agents": agents,
            "drivers": drivers,
            "clients": clients,
        }
    )


@app.route("/api/admin/finance_summary_by_region", methods=["GET"])
def api_admin_finance_summary_by_region():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        agents = (
            sb_admin.table("agent_profiles").select("*").limit(5000).execute().data
            or []
        )
        ledger = (
            sb_admin.table("agent_wallet_ledger")
            .select("*")
            .limit(10000)
            .execute()
            .data
            or []
        )
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

    total_due_all = round(sum(r["total_due"] for r in rows), 2)

    return jsonify({"success": True, "total_due_all": total_due_all, "rows": rows})


@app.route("/api/admin/agent_payment_due", methods=["GET"])
def api_admin_agent_payment_due():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    town = (request.args.get("town") or "").strip().lower()

    try:
        agents = (
            sb_admin.table("agent_profiles").select("*").limit(5000).execute().data
            or []
        )
        ledger = (
            sb_admin.table("agent_wallet_ledger")
            .select("*")
            .limit(10000)
            .execute()
            .data
            or []
        )
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
        out.append(
            {
                "agent_id": aid,
                "full_name": a.get("full_name") or "",
                "email": a.get("email") or "",
                "phone": a.get("phone") or "",
                "region": region or "Unknown",
                "amount_due": round(balances.get(aid, 0.0), 2),
            }
        )

    out.sort(key=lambda x: x["amount_due"], reverse=True)

    return jsonify({"success": True, "rows": out})


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
    "Zambezi",
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
        agents = (
            sb_admin.table("agent_profiles").select("*").limit(5000).execute().data
            or []
        )
        ledger = (
            sb_admin.table("agent_wallet_ledger")
            .select("*")
            .limit(10000)
            .execute()
            .data
            or []
        )
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
        headers={
            "Content-Disposition": "attachment; filename=finance_summary_by_region.csv"
        },
    )


@app.route("/api/admin/export_agent_due_csv", methods=["GET"])
def api_admin_export_agent_due_csv():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    town = (request.args.get("town") or "").strip().lower()

    try:
        agents = (
            sb_admin.table("agent_profiles").select("*").limit(5000).execute().data
            or []
        )
        ledger = (
            sb_admin.table("agent_wallet_ledger")
            .select("*")
            .limit(10000)
            .execute()
            .data
            or []
        )
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
        out.append(
            [
                aid,
                a.get("full_name") or "",
                a.get("email") or "",
                a.get("phone") or "",
                region or "Unknown",
                round(balances.get(aid, 0.0), 2),
            ]
        )

    out.sort(key=lambda x: x[5], reverse=True)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["Agent ID", "Full Name", "Email", "Phone", "Region", "Amount Due (N$)"]
    )
    for row in out:
        writer.writerow(row)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=agent_payment_due.csv"},
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

        agents = (
            sb_admin.table("agent_profiles").select("*").limit(5000).execute().data
            or []
        )
        targets = [
            a
            for a in agents
            if region.lower()
            in ((a.get("operation_region") or a.get("town") or "").lower())
        ]

        if "broadcast_logs" not in t if False else False:
            pass

        try:
            sb_admin.table("broadcast_logs").insert(
                {
                    "target_scope": "region",
                    "target_value": region,
                    "message": message,
                    "sent_count": len(targets),
                    "created_by": session.get("email") or "admin",
                }
            ).execute()
        except Exception:
            pass

        return jsonify(
            {
                "success": True,
                "region": region,
                "sent_count": len(targets),
                "rows": [
                    {
                        "full_name": a.get("full_name"),
                        "email": a.get("email"),
                        "phone": a.get("phone"),
                        "region": a.get("operation_region") or a.get("town") or "",
                    }
                    for a in targets
                ],
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/admin/agent/<agent_id>")
def admin_agent_profile_page(agent_id):
    if session.get("role") != "ADMIN":
        return redirect(url_for("admin_login"))
    return render_template("admin_agent_profile.html", agent_id=agent_id)


@app.route("/api/admin/agent_profile/<agent_id>", methods=["GET"])
def api_admin_agent_profile(agent_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        agents = (
            sb_admin.table("agent_profiles")
            .select("*")
            .eq("id", agent_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not agents:
            return jsonify({"success": False, "error": "Agent not found"}), 404

        agent = agents[0]

        drivers = (
            sb_admin.table("drivers")
            .select("*")
            .eq("recruiter_agent_id", str(agent_id))
            .order("created_at", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )
        clients = (
            sb_admin.table("clients")
            .select("*")
            .eq("recruiter_agent_id", str(agent_id))
            .order("created_at", desc=True)
            .limit(500)
            .execute()
            .data
            or []
        )

        try:
            ledger = (
                sb_admin.table("agent_wallet_ledger")
                .select("*")
                .eq("agent_id", str(agent_id))
                .order("created_at", desc=True)
                .limit(200)
                .execute()
                .data
                or []
            )
        except Exception:
            ledger = []

        balance = 0.0
        for row in ledger:
            amt = float(row.get("amount") or 0)
            typ = (row.get("txn_type") or "").lower()
            status = (row.get("status") or "approved").lower()
            if status != "approved":
                continue
            if typ == "debit":
                balance -= amt
            else:
                balance += amt

        return jsonify(
            {
                "success": True,
                "agent": agent,
                "drivers": drivers,
                "clients": clients,
                "wallet_balance": round(balance, 2),
                "wallet_rows": ledger,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/agent_reset_pin/<agent_id>", methods=["POST"])
def api_admin_agent_reset_pin_stable(agent_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        data = request.get_json(silent=True) or {}
        new_pin = (data.get("new_pin") or "").strip()

        if not new_pin:
            return jsonify({"success": False, "error": "New PIN is required"}), 400

        rows = (
            sb_admin.table("agent_profiles")
            .select("*")
            .eq("id", agent_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            return jsonify({"success": False, "error": "Agent not found"}), 404

        agent = rows[0]

        sb_admin.table("agent_profiles").update({"pin": new_pin}).eq(
            "id", agent_id
        ).execute()

        return jsonify(
            {
                "success": True,
                "agent_id": agent_id,
                "agent_name": agent.get("full_name") or "",
                "agent_email": agent.get("email") or "",
                "temporary_pin": new_pin,
                "message": "PIN reset successfully",
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/agent_set_status/<agent_id>", methods=["POST"])
def api_admin_agent_set_status(agent_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        data = request.get_json(silent=True) or {}
        status = (data.get("status") or "").strip()
        if not status:
            return jsonify({"success": False, "error": "Status is required"}), 400

        sb_admin.table("agent_profiles").update({"status": status}).eq(
            "id", agent_id
        ).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/agents_manager_data", methods=["GET"])
def api_admin_agents_manager_data():
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    q = (request.args.get("q") or "").strip().lower()

    try:
        agents = (
            sb_admin.table("agent_profiles").select("*").limit(5000).execute().data
            or []
        )
        drivers = (
            sb_admin.table("drivers")
            .select("recruiter_agent_id")
            .limit(10000)
            .execute()
            .data
            or []
        )
        clients = (
            sb_admin.table("clients")
            .select("recruiter_agent_id")
            .limit(10000)
            .execute()
            .data
            or []
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    dcount = {}
    ccount = {}

    for d in drivers:
        aid = str(d.get("recruiter_agent_id") or "")
        if aid:
            dcount[aid] = dcount.get(aid, 0) + 1

    for c in clients:
        aid = str(c.get("recruiter_agent_id") or "")
        if aid:
            ccount[aid] = ccount.get(aid, 0) + 1

    rows = []
    for a in agents:
        aid = str(a.get("id") or "")
        row = {
            "id": aid,
            "status": a.get("status") or "ACTIVE",
            "full_name": a.get("full_name") or "",
            "email": a.get("email") or "",
            "phone": a.get("phone") or "",
            "town": a.get("operation_region") or a.get("town") or "—",
            "created_at": a.get("created_at"),
            "drivers": dcount.get(aid, 0),
            "clients": ccount.get(aid, 0),
        }

        hay = " ".join(
            [
                str(row["full_name"]),
                str(row["email"]),
                str(row["phone"]),
                str(row["town"]),
                str(row["status"]),
            ]
        ).lower()

        if q and q not in hay:
            continue

        rows.append(row)

    rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return jsonify({"success": True, "rows": rows})


@app.route("/api/admin/agent_reset_pin/<agent_id>", methods=["POST"])
def api_admin_agent_reset_pin():
    agent_id = request.view_args["agent_id"]
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        data = request.get_json(silent=True) or {}
        new_pin = (data.get("new_pin") or "").strip()

        if not new_pin:
            return jsonify({"success": False, "error": "New PIN is required"}), 400

        rows = (
            sb_admin.table("agent_profiles")
            .select("*")
            .eq("id", agent_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            return jsonify({"success": False, "error": "Agent not found"}), 404

        agent = rows[0]
        sb_admin.table("agent_profiles").update({"pin": new_pin}).eq(
            "id", agent_id
        ).execute()

        return jsonify(
            {
                "success": True,
                "agent_id": agent_id,
                "agent_name": agent.get("full_name") or "",
                "agent_email": agent.get("email") or "",
                "temporary_pin": new_pin,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/admin/agent_set_status/<agent_id>", methods=["POST"])
def api_admin_agent_set_status_clean(agent_id):
    if session.get("role") != "ADMIN":
        return jsonify({"success": False, "error": "Unauthorized"}), 403


@app.route("/api/admin/agent_wallet_bonus/<agent_id>", methods=["POST"])
def api_admin_agent_wallet_bonus(agent_id):
    data = request.get_json(silent=True) or {}

    try:
        return jsonify({"message": "Bonus processed", "agent_id": agent_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/delete_agent", methods=["DELETE"])
def admin_delete_agent():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403

    email = request.json.get("email")

    try:
        supabase.table("profiles").delete().eq("email", email).execute()
        return jsonify({"message": f"Agent {email} deleted successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def require_login(required_role=None):
    from functools import wraps
    from flask import jsonify, session

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user_role = session.get("role")

            if not user_role:
                return jsonify({"error": "Unauthorized"}), 401

            if required_role and user_role != required_role:
                return jsonify({"error": "Forbidden"}), 403

            return fn(*args, **kwargs)

        return wrapper

    return decorator








@app.after_request
def inject_admin_report_center_button(response):
    try:
        if request.path != "/dashboard/admin":
            return response
        ctype = (response.content_type or "").lower()
        if "text/html" not in ctype:
            return response

        body = response.get_data(as_text=True)
        if "/admin/report-center" in body and "Create Weekly Report" in body:
            return response

        card = """
<div id="admin-report-center-card" style="margin:18px 0;">
  <div style="background:linear-gradient(135deg,#0F4C81,#1D7CC1);color:#fff;border-radius:18px;padding:20px 22px;box-shadow:0 12px 30px rgba(0,0,0,.12);">
    <div style="font:700 22px Arial,sans-serif;margin-bottom:8px;">Create Weekly Report</div>
    <div style="font:400 14px Arial,sans-serif;opacity:.95;margin-bottom:14px;">
      Generate a professional PDF report for all agents or one agent, with weekly registrations, bonus, and payout totals.
    </div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;">
      <a href="/admin/report-center"
         style="display:inline-block;background:#fff;color:#0F4C81;text-decoration:none;padding:11px 16px;border-radius:12px;font:700 14px Arial,sans-serif;">
         Open Report Center
      </a>
      <a href="/api/admin/weekly_agent_report_pdf"
         style="display:inline-block;background:rgba(255,255,255,.18);color:#fff;text-decoration:none;padding:11px 16px;border-radius:12px;font:700 14px Arial,sans-serif;border:1px solid rgba(255,255,255,.25);">
         Quick PDF
      </a>
    </div>
  </div>
</div>
"""

        floating = """
<div id="admin-report-center-shortcut" style="position:fixed;right:18px;bottom:18px;z-index:99999;">
  <a href="/admin/report-center"
     style="display:inline-block;background:#0b57d0;color:#fff;text-decoration:none;
            padding:12px 16px;border-radius:12px;font:600 14px Arial,sans-serif;
            box-shadow:0 8px 24px rgba(0,0,0,.18);">
    Weekly Reports
  </a>
</div>
"""

        inserted = False
        for marker in ["</main>", "</section>", "</div></div>", "</body>"]:
            if marker in body and not inserted:
                body = body.replace(marker, card + "\n" + marker, 1)
                inserted = True

        if "</body>" in body and "admin-report-center-shortcut" not in body:
            body = body.replace("</body>", floating + "\n</body>", 1)

        response.set_data(body)
    except Exception:
        pass
    return response


@app.route("/admin/report-center")
def admin_report_center():
    from flask import session, redirect

    def _is_admin_ok():
        try:
            if "_require_admin" in globals():
                v = _require_admin()
                if v:
                    return True
        except Exception:
            pass
        return bool(session.get("is_admin") or session.get("role") == "ADMIN")

    if not _is_admin_ok():
        return redirect("/admin/login")

    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Admin Report Center</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: Arial, sans-serif; background:#f5f7fb; margin:0; padding:24px; color:#1f2937; }
    .wrap { max-width: 980px; margin: 0 auto; }
    .card { background:#fff; border-radius:18px; padding:24px; box-shadow:0 10px 30px rgba(0,0,0,.08); }
    h1 { margin:0 0 8px; font-size:28px; }
    p { margin:0 0 18px; color:#4b5563; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }
    .full { grid-column:1 / -1; }
    label { display:block; font-size:14px; font-weight:700; margin-bottom:8px; }
    input, select, button { width:100%; padding:12px 14px; border-radius:12px; border:1px solid #d1d5db; font-size:15px; }
    button { background:#0b57d0; color:#fff; border:none; cursor:pointer; font-weight:700; }
    button.secondary { background:#111827; }
    .row { display:flex; gap:12px; flex-wrap:wrap; margin-top:18px; }
    .row a, .row button { flex:1; min-width:220px; }
    .hint { margin-top:16px; font-size:13px; color:#6b7280; }
    .topnav { margin-bottom:18px; }
    .topnav a { text-decoration:none; color:#0b57d0; font-weight:700; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topnav"><a href="/dashboard/admin">← Back to Admin Dashboard</a></div>
    <div class="card">
      <h1>Weekly Report Center</h1>
      <p>Select a week and choose whether to generate a report for all agents or a single agent.</p>

      <div class="grid">
        <div>
          <label for="week_start">Week start (Monday)</label>
          <input type="date" id="week_start">
        </div>

        <div>
          <label for="report_scope">Report scope</label>
          <select id="report_scope">
            <option value="all">All agents</option>
            <option value="single">Single agent</option>
          </select>
        </div>

        <div class="full">
          <label for="agent_code">Agent</label>
          <select id="agent_code" disabled>
            <option value="">Loading agents...</option>
          </select>
        </div>

        <div>
          <label for="client_payment">Client registration payment</label>
          <input type="number" id="client_payment" step="0.01" min="0" value="10">
        </div>

        <div>
          <label for="driver_payment">Driver registration payment</label>
          <input type="number" id="driver_payment" step="0.01" min="0" value="10">
        </div>

        <div>
          <label for="client_bonus">Client bonus amount</label>
          <input type="number" id="client_bonus" step="0.01" min="0" value="15">
        </div>

        <div>
          <label for="driver_bonus">Driver bonus amount</label>
          <input type="number" id="driver_bonus" step="0.01" min="0" value="0">
        </div>

        <div>
          <label for="client_bonus_min">Minimum clients per day for client bonus</label>
          <input type="number" id="client_bonus_min" step="1" min="0" value="1">
        </div>

        <div>
          <label for="driver_bonus_min">Minimum drivers per day for driver bonus</label>
          <input type="number" id="driver_bonus_min" step="1" min="0" value="1">
        </div>
      </div>

      <div class="row">
        <button id="open_pdf_btn">Open PDF Report</button>
        <button id="download_pdf_btn" class="secondary">Download PDF Report</button>
      </div>

      <div class="hint">
        Report includes each agent name, agent code, daily registration totals from Monday to Sunday,
        driver list, and client list for the selected week.
      </div>
    </div>
  </div>

<script>
(function () {
  function getMonday(d) {
    const date = new Date(d);
    const day = date.getDay();
    const diff = date.getDate() - ((day + 6) % 7);
    const monday = new Date(date.setDate(diff));
    return monday.toISOString().slice(0, 10);
  }

  const weekInput = document.getElementById("week_start");
  const scopeSel = document.getElementById("report_scope");
  const agentSel = document.getElementById("agent_code");
  const clientPaymentInput = document.getElementById("client_payment");
  const driverPaymentInput = document.getElementById("driver_payment");
  const clientBonusInput = document.getElementById("client_bonus");
  const driverBonusInput = document.getElementById("driver_bonus");
  const clientBonusMinInput = document.getElementById("client_bonus_min");
  const driverBonusMinInput = document.getElementById("driver_bonus_min");
  const openBtn = document.getElementById("open_pdf_btn");
  const dlBtn = document.getElementById("download_pdf_btn");

  weekInput.value = getMonday(new Date());

  function toggleAgent() {
    const single = scopeSel.value === "single";
    agentSel.disabled = !single;
  }

  scopeSel.addEventListener("change", toggleAgent);
  toggleAgent();

  function optionText(a) {
    const name = a.full_name || a.name || a.agent_name || "Unknown Agent";
    const code = a.referral_code || a.agent_code || a.code || a.my_referral_code || "";
    const phone = a.phone || a.mobile || "";
    return code ? `${name} (${code})${phone ? " - " + phone : ""}` : `${name}${phone ? " - " + phone : ""}`;
  }

  function optionValue(a) {
    return a.referral_code || a.agent_code || a.code || a.my_referral_code || "";
  }

  fetch("/api/admin/agents")
    .then(r => r.json())
    .then(data => {
      const rows = Array.isArray(data) ? data : (data.rows || data.agents || data.data || []);
      agentSel.innerHTML = '<option value="">Select an agent</option>';
      rows.forEach(a => {
        const val = optionValue(a);
        if (!val) return;
        const opt = document.createElement("option");
        opt.value = val;
        opt.textContent = optionText(a);
        agentSel.appendChild(opt);
      });
    })
    .catch(() => {
      agentSel.innerHTML = '<option value="">Could not load agents</option>';
    });

  function buildUrl() {
    const week = weekInput.value;
    const scope = scopeSel.value;
    const agent = agentSel.value;
    const clientPayment = clientPaymentInput.value || "0";
    const driverPayment = driverPaymentInput.value || "0";
    const clientBonus = clientBonusInput.value || "0";
    const driverBonus = driverBonusInput.value || "0";
    const clientBonusMin = clientBonusMinInput.value || "0";
    const driverBonusMin = driverBonusMinInput.value || "0";

    if (!week) {
      alert("Please select a week.");
      return null;
    }
    if (scope === "single" && !agent) {
      alert("Please select an agent.");
      return null;
    }

    const u = new URL("/api/admin/weekly_agent_report_pdf", window.location.origin);
    u.searchParams.set("week_start", week);
    u.searchParams.set("client_payment", clientPayment);
    u.searchParams.set("driver_payment", driverPayment);
    u.searchParams.set("client_bonus", clientBonus);
    u.searchParams.set("driver_bonus", driverBonus);
    u.searchParams.set("client_bonus_min", clientBonusMin);
    u.searchParams.set("driver_bonus_min", driverBonusMin);
    if (scope === "single") u.searchParams.set("agent_code", agent);
    return u.toString();
  }

  openBtn.addEventListener("click", function () {
    const url = buildUrl();
    if (url) window.open(url, "_blank");
  });

  dlBtn.addEventListener("click", function () {
    const url = buildUrl();
    if (url) window.location.href = url;
  });
})();
</script>
</body>
</html>
"""




@app.route("/api/admin/weekly_agent_report_pdf")
def api_admin_weekly_agent_report_pdf():
    import tempfile
    import datetime as dt
    from flask import request, send_file, jsonify, session
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors

    def _is_admin_ok():
        try:
            if "_require_admin" in globals():
                v = _require_admin()
                if v:
                    return True
        except Exception:
            pass
        return bool(session.get("is_admin") or session.get("role") == "ADMIN")

    if not _is_admin_ok():
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    week_start = (request.args.get("week_start") or "").strip()
    selected_agent_code = (request.args.get("agent_code") or "").strip()

    def _arg_money(name, default=0.0):
        try:
            return float((request.args.get(name) or default))
        except Exception:
            return float(default)

    admin_client_payment = _arg_money("client_payment", 0.0)
    admin_driver_payment = _arg_money("driver_payment", 0.0)
    admin_client_bonus = _arg_money("client_bonus", 0.0)
    admin_driver_bonus = _arg_money("driver_bonus", 0.0)

    def _arg_int(name, default=0):
        try:
            return int(float((request.args.get(name) or default)))
        except Exception:
            return int(default)

    admin_client_bonus_min = _arg_int("client_bonus_min", 0)
    admin_driver_bonus_min = _arg_int("driver_bonus_min", 0)

    try:
        monday = dt.date.fromisoformat(week_start) if week_start else dt.date.today()
    except Exception:
        return jsonify({"ok": False, "error": "Invalid week_start. Use YYYY-MM-DD"}), 400

    sunday = monday + dt.timedelta(days=6)
    start_iso = f"{monday.isoformat()}T00:00:00"
    end_iso = f"{(sunday + dt.timedelta(days=1)).isoformat()}T00:00:00"

    DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def _pick(d, *keys):
        for k in keys:
            v = d.get(k)
            if v not in (None, ""):
                return v
        return ""

    def _norm(v):
        return str(v or "").strip().lower()

    def _num(v, default=0.0):
        try:
            if v in (None, ""):
                return float(default)
            return float(v)
        except Exception:
            return float(default)

    def _parse_dt(v):
        s = str(v or "").strip()
        if not s:
            return None
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return dt.datetime.fromisoformat(s)
        except Exception:
            try:
                return dt.datetime.fromisoformat(s[:19])
            except Exception:
                return None

    def _day_idx(v):
        x = _parse_dt(v)
        if not x:
            return None
        return x.weekday()

    def _safe_rows(table_name):
        try:
            return sb_admin.table(table_name).select("*").limit(5000).execute().data or []
        except Exception:
            return []

    def _safe_week_rows(table_name):
        for date_col in ["created_at", "registered_at", "signup_date"]:
            try:
                rows = (
                    sb_admin.table(table_name)
                    .select("*")
                    .gte(date_col, start_iso)
                    .lt(date_col, end_iso)
                    .limit(5000)
                    .execute()
                    .data
                    or []
                )
                if rows:
                    return rows
            except Exception:
                pass
        try:
            return sb_admin.table(table_name).select("*").limit(5000).execute().data or []
        except Exception:
            return []

    def _agent_code(a):
        return str(_pick(a, "referral_code", "agent_code", "code", "my_referral_code", "agent_referral_code")).strip()

    def _agent_name(a):
        return str(_pick(a, "full_name", "name", "agent_name")).strip() or "Unknown Agent"

    def _agent_phone(a):
        return str(_pick(a, "phone", "mobile", "phone_number")).strip()

    def _row_code(r):
        return str(_pick(r, "referred_by_code", "referral_code", "agent_code", "code")).strip()

    def _row_name(r):
        return str(_pick(r, "full_name", "name", "driver_name", "client_name")).strip() or "Unknown"

    def _row_phone(r):
        return str(_pick(r, "phone", "mobile", "phone_number")).strip()

    def _row_town(r):
        return str(_pick(r, "town", "region", "city", "location")).strip()

    def _row_created(r):
        return _pick(r, "created_at", "registered_at", "signup_date")

    payment_rules = _safe_rows("payment_rules")

    def _best_rule_for_agent(agent_row):
        active_rules = [r for r in payment_rules if bool(r.get("is_active", True))]
        if not active_rules:
            return {}

        town_matches = []
        region_matches = []

        agent_region = _norm(_pick(agent_row, "region", "agent_region"))
        agent_town = _norm(_pick(agent_row, "town", "agent_town", "city", "location"))

        for r in active_rules:
            rr = _norm(r.get("region"))
            rt = _norm(r.get("town"))
            if agent_town and rt and agent_town == rt:
                town_matches.append(r)
            elif agent_region and rr and agent_region == rr:
                region_matches.append(r)

        def newest(rows):
            rows = sorted(rows, key=lambda x: str(x.get("created_at", "")), reverse=True)
            return rows[0] if rows else {}

        return newest(town_matches) or newest(region_matches) or newest(active_rules)

    def _driver_rate_for_agent(agent_row):
        if admin_driver_payment > 0:
            return admin_driver_payment
        return _num(_best_rule_for_agent(agent_row).get("driver_reg"), 0.0)

    def _client_rate_for_agent(agent_row):
        if admin_client_payment > 0:
            return admin_client_payment
        return _num(_best_rule_for_agent(agent_row).get("client_reg"), 0.0)

    def _client_bonus_for_agent(agent_row):
        if admin_client_bonus > 0:
            return admin_client_bonus
        return _num(_best_rule_for_agent(agent_row).get("first_trip_bonus"), 0.0)

    def _driver_bonus_for_agent(agent_row):
        if admin_driver_bonus > 0:
            return admin_driver_bonus
        return 0.0

    def _client_bonus_min_for_agent(agent_row):
        return max(0, int(admin_client_bonus_min))

    def _driver_bonus_min_for_agent(agent_row):
        return max(0, int(admin_driver_bonus_min))

    def _daily_bonus_breakdown_for_agent(agent_row, client_count, driver_count):
        client_bonus_rate = _client_bonus_for_agent(agent_row)
        driver_bonus_rate = _driver_bonus_for_agent(agent_row)
        client_min = _client_bonus_min_for_agent(agent_row)
        driver_min = _driver_bonus_min_for_agent(agent_row)

        client_bonus = client_bonus_rate if client_count >= client_min and client_count > 0 else 0.0
        driver_bonus = driver_bonus_rate if driver_count >= driver_min and driver_count > 0 else 0.0

        return {
            "client_bonus": client_bonus,
            "driver_bonus": driver_bonus,
            "total_bonus": client_bonus + driver_bonus,
            "client_min": client_min,
            "driver_min": driver_min,
            "client_bonus_rate": client_bonus_rate,
            "driver_bonus_rate": driver_bonus_rate,
        }

    def _daily_bonus_for_agent(agent_row, client_count, driver_count):
        return _daily_bonus_breakdown_for_agent(agent_row, client_count, driver_count)["total_bonus"]

    agents_map = {}
    for a in _safe_rows("agent_profiles"):
        code = _agent_code(a)
        if code:
            agents_map[_norm(code)] = a
    for a in _safe_rows("agents"):
        code = _agent_code(a)
        if code and _norm(code) not in agents_map:
            agents_map[_norm(code)] = a

    weekly_drivers = _safe_week_rows("drivers")
    weekly_clients = _safe_week_rows("clients")

    report = {}
    for code_norm, agent in agents_map.items():
        report[code_norm] = {
            "agent": agent,
            "agent_name": _agent_name(agent),
            "agent_code": _agent_code(agent),
            "phone": _agent_phone(agent),
            "drivers": [],
            "clients": [],
            "days": [
                {"drivers": [], "clients": []} for _ in range(7)
            ],
        }

    unknown_bucket = {
        "agent": {},
        "agent_name": "Unassigned / Unknown Agent",
        "agent_code": "UNASSIGNED",
        "phone": "",
        "drivers": [],
        "clients": [],
        "days": [
            {"drivers": [], "clients": []} for _ in range(7)
        ],
    }

    for r in weekly_drivers:
        code_norm = _norm(_row_code(r))
        bucket = report.get(code_norm, unknown_bucket)
        bucket["drivers"].append(r)
        di = _day_idx(_row_created(r))
        if di is not None:
            bucket["days"][di]["drivers"].append(r)

    for r in weekly_clients:
        code_norm = _norm(_row_code(r))
        bucket = report.get(code_norm, unknown_bucket)
        bucket["clients"].append(r)
        di = _day_idx(_row_created(r))
        if di is not None:
            bucket["days"][di]["clients"].append(r)

    rows = [v for v in report.values() if v["drivers"] or v["clients"]]
    if unknown_bucket["drivers"] or unknown_bucket["clients"]:
        rows.append(unknown_bucket)

    if selected_agent_code:
        rows = [r for r in rows if _norm(r["agent_code"]) == _norm(selected_agent_code)]

    rows.sort(key=lambda x: ((_norm(x["agent_name"])), (_norm(x["agent_code"]))))

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    c = canvas.Canvas(tmp.name, pagesize=A4)
    page_w, page_h = A4

    LEFT = 28
    RIGHT = page_w - 28
    TOP = page_h - 28
    BOTTOM = 22

    BRAND = colors.HexColor("#0F4C81")
    BRAND2 = colors.HexColor("#1D7CC1")
    LIGHT = colors.HexColor("#EAF3FB")
    SOFT = colors.HexColor("#F4F6F8")
    TEXT = colors.HexColor("#1F2937")
    MUTED = colors.HexColor("#6B7280")
    GREEN = colors.HexColor("#0F9D58")

    def money(v):
        return f"N${_num(v):,.2f}"

    def new_page():
        c.showPage()
        return header()

    def need_space(y, need=90):
        if y < BOTTOM + need:
            return new_page()
        return y

    def box(x, y, w, h, fill=None, stroke=colors.white):
        if fill is not None:
            c.setFillColor(fill)
        c.setStrokeColor(stroke)
        c.rect(x, y, w, h, fill=1 if fill is not None else 0, stroke=1)

    def header():
        c.setFillColor(BRAND)
        c.rect(0, page_h - 72, page_w, 72, fill=1, stroke=0)

        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(LEFT, page_h - 34, "YENE WEEKLY AGENT PERFORMANCE REPORT")

        c.setFont("Helvetica-Bold", 10)
        c.drawString(LEFT, page_h - 52, f"Client Pay: {money(admin_client_payment)}   Driver Pay: {money(admin_driver_payment)}   Client Bonus: {money(admin_client_bonus)}   Driver Bonus: {money(admin_driver_bonus)}")

        c.setFont("Helvetica", 9)
        c.drawString(LEFT, page_h - 64, f"Bonus Conditions  |  Client Bonus Min/Day: {admin_client_bonus_min}   Driver Bonus Min/Day: {admin_driver_bonus_min}")

        c.setFont("Helvetica", 10)
        c.drawRightString(RIGHT, page_h - 28, f"Week: {monday.isoformat()} to {sunday.isoformat()}")
        if selected_agent_code:
            c.drawRightString(RIGHT, page_h - 44, f"Agent: {selected_agent_code}")
        else:
            c.drawRightString(RIGHT, page_h - 44, "Agent: ALL")

        y = page_h - 92

        c.setFillColor(MUTED)
        c.setFont("Helvetica", 9)
        c.drawString(LEFT, y, "Shows daily client and driver registrations, weekly totals, pay, and bonuses.")
        return y - 18

    def draw_summary_cards(row, y):
        y = need_space(y, 92)

        total_drivers = len(row["drivers"])
        total_clients = len(row["clients"])
        driver_rate = _driver_rate_for_agent(row["agent"])
        client_rate = _client_rate_for_agent(row["agent"])
        client_bonus_rate = _client_bonus_for_agent(row["agent"])
        driver_bonus_rate = _driver_bonus_for_agent(row["agent"])
        weekly_base = (total_drivers * driver_rate) + (total_clients * client_rate)
        daily_bonus_total = sum(
            _daily_bonus_for_agent(
                row["agent"],
                len(row["days"][i]["clients"]),
                len(row["days"][i]["drivers"])
            ) for i in range(7)
        )
        grand_total = weekly_base + daily_bonus_total

        cards = [
            ("Drivers", str(total_drivers), LIGHT),
            ("Clients", str(total_clients), LIGHT),
            ("Base Pay", money(weekly_base), SOFT),
            ("Bonus", money(daily_bonus_total), SOFT),
            ("Total Payout", money(grand_total), GREEN),
        ]

        x = LEFT
        card_w = 102
        gap = 8
        for title, value, fill in cards:
            box(x, y - 54, card_w, 48, fill=fill, stroke=colors.white)
            c.setFillColor(TEXT if fill != GREEN else colors.white)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(x + 8, y - 20, title)
            c.setFont("Helvetica-Bold", 12)
            c.drawString(x + 8, y - 38, value)
            x += card_w + gap

        return y - 66, weekly_base, daily_bonus_total, grand_total

    def draw_pay_table(row, y):
        y = need_space(y, 120)

        c.setFillColor(BRAND2)
        c.rect(LEFT, y - 16, RIGHT - LEFT, 18, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(LEFT + 8, y - 4, "PAY CALCULATION")
        y -= 26

        cols = [
            ("Type", LEFT, 130),
            ("Qty", LEFT + 130, 60),
            ("Rate", LEFT + 190, 80),
            ("Amount", LEFT + 270, 90),
        ]
        c.setFillColor(TEXT)
        c.setFont("Helvetica-Bold", 9)
        for label, x, w in cols:
            c.setStrokeColor(colors.HexColor("#D1D5DB"))
            c.rect(x, y - 14, w, 16, fill=0, stroke=1)
            c.drawString(x + 4, y - 2, label)
        y -= 16

        driver_rate = _driver_rate_for_agent(row["agent"])
        client_rate = _client_rate_for_agent(row["agent"])
        client_bonus_rate = _client_bonus_for_agent(row["agent"])
        driver_bonus_rate = _driver_bonus_for_agent(row["agent"])
        total_bonus = sum(
            _daily_bonus_for_agent(
                row["agent"],
                len(row["days"][i]["clients"]),
                len(row["days"][i]["drivers"])
            ) for i in range(7)
        )

        client_bonus_min = _client_bonus_min_for_agent(row["agent"])
        driver_bonus_min = _driver_bonus_min_for_agent(row["agent"])

        rows_pay = [
            ("Drivers Registered", len(row["drivers"]), driver_rate, len(row["drivers"]) * driver_rate),
            ("Clients Registered", len(row["clients"]), client_rate, len(row["clients"]) * client_rate),
            (f"Client Bonus (min/day {client_bonus_min})", "-", client_bonus_rate, total_bonus),
            (f"Driver Bonus (min/day {driver_bonus_min})", "-", driver_bonus_rate, "-"),
            ("Total Bonus", "-", "-", total_bonus),
        ]

        c.setFont("Helvetica", 9)
        for label, qty, rate, amount in rows_pay:
            vals = [
                (label, LEFT, 130),
                (str(qty), LEFT + 130, 60),
                (money(rate) if isinstance(rate, (int, float)) else str(rate), LEFT + 190, 80),
                (money(amount), LEFT + 270, 90),
            ]
            for val, x, w in vals:
                c.rect(x, y - 14, w, 16, fill=0, stroke=1)
                c.drawString(x + 4, y - 2, str(val))
            y -= 16

        total = (len(row["drivers"]) * driver_rate) + (len(row["clients"]) * client_rate) + total_bonus
        c.setFillColor(BRAND)
        c.rect(LEFT + 190, y - 14, 170, 18, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(LEFT + 198, y - 2, f"TOTAL PAYOUT: {money(total)}")
        return y - 24

    def draw_day_section(day_name, day_date, drivers, clients, y):
        y = need_space(y, 170)

        c.setFillColor(BRAND2)
        c.rect(LEFT, y - 16, RIGHT - LEFT, 18, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 10)
        agent_for_bonus = row_agent_context
        bonus_info = _daily_bonus_breakdown_for_agent(agent_for_bonus, len(clients), len(drivers))
        c.drawString(LEFT + 8, y - 4, f"{day_name}  |  {day_date.isoformat()}  |  Clients: {len(clients)}  |  Drivers: {len(drivers)}  |  Bonus: {money(bonus_info['total_bonus'])}")
        y -= 24

        c.setFillColor(MUTED)
        c.setFont("Helvetica", 8)
        c.drawString(LEFT, y, f"Client bonus rule: need {bonus_info['client_min']} clients/day to earn {money(bonus_info['client_bonus_rate'])}")
        c.drawString(300, y, f"Driver bonus rule: need {bonus_info['driver_min']} drivers/day to earn {money(bonus_info['driver_bonus_rate'])}")
        y -= 12

        c.setFillColor(TEXT)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(LEFT, y, "Clients Registered")
        c.drawString(300, y, "Drivers Registered")
        y -= 12

        max_len = max(len(clients), len(drivers), 1)
        c.setFont("Helvetica", 8)

        for i in range(max_len):
            y = need_space(y, 26)

            c.setStrokeColor(colors.HexColor("#E5E7EB"))
            c.rect(LEFT, y - 12, 240, 15, fill=0, stroke=1)
            c.rect(300, y - 12, 240, 15, fill=0, stroke=1)

            if i < len(clients):
                cl = clients[i]
                client_text = f"{_row_name(cl)} | {_row_code(cl)} | {_row_phone(cl)}"
                c.drawString(LEFT + 4, y - 2, client_text[:44])

            if i < len(drivers):
                dr = drivers[i]
                driver_text = f"{_row_name(dr)} | {_row_code(dr)} | {_row_phone(dr)}"
                c.drawString(304, y - 2, driver_text[:44])

            y -= 16

        return y - 8

    y = header()

    if not rows:
        c.setFillColor(TEXT)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(LEFT, y, "No report data found for the selected week / agent.")
        c.save()
        filename = f"yene_weekly_agent_report_{monday.isoformat()}_to_{sunday.isoformat()}.pdf"
        return send_file(tmp.name, as_attachment=True, download_name=filename, mimetype="application/pdf")

    for idx, row in enumerate(rows, start=1):
        y = need_space(y, 180)

        c.setFillColor(BRAND)
        c.roundRect(LEFT, y - 34, RIGHT - LEFT, 28, 8, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(LEFT + 10, y - 16, f"{idx}. Agent: {row['agent_name']}")
        c.setFont("Helvetica", 10)
        c.drawRightString(RIGHT - 10, y - 16, f"Code: {row['agent_code'] or '-'}")
        y -= 44

        if row["phone"]:
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 9)
            c.drawString(LEFT, y, f"Phone: {row['phone']}")
            y -= 14

        y, _, _, _ = draw_summary_cards(row, y)
        y = draw_pay_table(row, y)

        row_agent_context = row["agent"]
        for di in range(7):
            y = draw_day_section(
                DAYS[di],
                monday + dt.timedelta(days=di),
                row["days"][di]["drivers"],
                row["days"][di]["clients"],
                y
            )

        if idx != len(rows):
            y = need_space(y, 40)
            c.setStrokeColor(colors.HexColor("#CBD5E1"))
            c.line(LEFT, y, RIGHT, y)
            y -= 20

    c.save()

    if selected_agent_code:
        filename = f"yene_weekly_agent_report_{selected_agent_code}_{monday.isoformat()}_to_{sunday.isoformat()}.pdf"
    else:
        filename = f"yene_weekly_agent_report_{monday.isoformat()}_to_{sunday.isoformat()}.pdf"

    return send_file(tmp.name, as_attachment=True, download_name=filename, mimetype="application/pdf")



if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
