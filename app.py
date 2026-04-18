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
from yene_compat_routes import register_yene_compat_routes


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






def _agent_session_email():
    return session.get("agent_email")

def _require_admin():
    return bool(session.get("admin_email")) and session.get("role") == "ADMIN"

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


load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# --- 1. THE FOUNDATION & TOOLS ---
app = Flask(__name__)

# ---------------------------
# PUBLIC DASHBOARD PAGES
# Supabase session is handled in browser.
# Data access must be via /api/* with Bearer token.
# ---------------------------



def _agent_session_email():
    return session.get("agent_email")

def _set_agent_session(email):
    session.clear()
    session["email"] = email
    session["agent_email"] = email
    session["role"] = "AGENT"

def _set_admin_session(email):
    session.clear()
    session["email"] = email
    session["admin_email"] = email
    session["role"] = "ADMIN"
    session["is_admin"] = True

def _require_admin():
    return bool(session.get("admin_email")) and session.get("role") == "ADMIN"

def _safe_log_system_event(event_type, details, user_id=None):
    try:
        fn = globals().get("log_system_event")
        if callable(fn):
            return fn(event_type, details, user_id=user_id)
    except Exception:
        pass
    return None


@app.route("/dashboard/admin")
def admin_dashboard():
    if not _require_admin():
        return redirect("/admin/login")
    return render_template("admin_dashboard.html")


@app.route("/agent/dashboard")
def agent_dashboard():
    if not session.get("agent_email"):
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


@app.before_request
def protect_role_scoped_api_routes():
    if request.path.startswith("/api/admin/") and not _require_admin():
        return jsonify({"ok": False, "error": "Admin login required"}), 401

    if request.path.startswith("/api/agent/"):
        role = (session.get("role") or "").upper()
        if role != "AGENT" or not session.get("email"):
            return jsonify({"ok": False, "error": "Agent login required"}), 401

URL = os.getenv("SUPABASE_URL", "https://kcxphxihykonzuagtgke.supabase.co")
ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

supabase = create_client(URL, ANON_KEY)
sb_admin = create_client(URL, SERVICE_KEY) if SERVICE_KEY else supabase

# --- HELPER FUNCTIONS ---



register_agent_team_routes(app, sb_admin)
register_agent_dashboard_v4_routes(app, sb_admin, require_login, _safe_log_system_event)
register_agent_wallet_v1_routes(app, sb_admin, require_login)
register_agent_academy_v1_routes(app, sb_admin, require_login)
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
register_yene_compat_routes(app, sb_admin)


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


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("agent_email"):
            return redirect("/agent/dashboard")
        return render_template(
            "login.html",
            login_mode="agent",
            SUPABASE_URL=globals().get("SUPABASE_URL", ""),
            SUPABASE_ANON_KEY=globals().get("SUPABASE_ANON_KEY", "")
        )

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    if not email or not password:
        flash("Email and password are required")
        return redirect("/login")

    try:
        if "sb" in globals():
            sb.auth.sign_in_with_password({"email": email, "password": password})
        elif "supabase" in globals():
            supabase.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as e:
        msg = str(e)
        if "JWT expired" in msg:
            flash("Session expired. Please login again.")
        else:
            flash(f"Login error: {msg}")
        return redirect("/login")

    _set_agent_session(email)
    _safe_log_system_event("LOGIN", f"AGENT logged in: {email}", user_id=email)
    return redirect("/agent/dashboard")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        if _require_admin():
            return redirect("/dashboard/admin")
        return render_template("login.html", login_mode="admin")

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    if email == "kasera@admin.com" and password == "admin123":
        _set_admin_session(email)
        _safe_log_system_event("LOGIN", f"ADMIN logged in: {email}", user_id=email)
        return redirect("/dashboard/admin")

    flash("Invalid admin credentials")
    return redirect("/admin/login")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
