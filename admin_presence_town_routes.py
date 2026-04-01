from datetime import datetime, timedelta
import uuid
from flask import jsonify, request, session, render_template


def register_admin_presence_town_routes(app, sb_admin):
    def _safe_select(table, filters=None, cols="*", limit=None, order_col=None, desc=False):
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

    def _safe_insert(table, payload):
        try:
            return sb_admin.table(table).insert(payload).execute()
        except Exception as e:
            return e

    def _safe_update(table, filters, payload):
        try:
            q = sb_admin.table(table).update(payload)
            for k, v in filters.items():
                q = q.eq(k, v)
            return q.execute()
        except Exception as e:
            return e

    def _now_iso():
        return datetime.utcnow().isoformat() + "Z"

    def _current_email():
        return (session.get("email") or "").strip().lower()

    def _current_role():
        return (session.get("role") or "").strip().upper()

    def _agent_by_email(email):
        rows = _safe_select("agent_profiles", {"email": email}, "*", 1)
        if rows:
            return rows[0]
        rows = _safe_select("agents", {"email": email}, "*", 1)
        if rows:
            return rows[0]
        return None

    def _agent_by_id(agent_id):
        rows = _safe_select("agent_profiles", {"id": agent_id}, "*", 1)
        if rows:
            return rows[0]
        rows = _safe_select("agents", {"id": agent_id}, "*", 1)
        if rows:
            return rows[0]
        return None

    def _approved(status):
        s = str(status or "").upper()
        return s in ("ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED")

    @app.post("/api/agent/presence/heartbeat")
    def agent_presence_heartbeat():
        email = _current_email()
        if not email:
            return jsonify({"ok": False, "error": "Not logged in"}), 401

        agent = _agent_by_email(email)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        data = request.get_json(silent=True) or {}
        current_page = str(data.get("current_page") or request.referrer or "/agent/dashboard").strip()
        payload = {
            "agent_id": agent.get("id"),
            "agent_email": agent.get("email"),
            "agent_name": agent.get("full_name") or agent.get("username") or agent.get("email"),
            "region": agent.get("region") or "",
            "town": agent.get("town") or "",
            "current_page": current_page,
            "last_seen": _now_iso(),
            "status": "ONLINE",
        }

        existing = _safe_select("agent_presence", {"agent_id": agent.get("id")}, "*", 1)
        if existing:
            _safe_update("agent_presence", {"agent_id": agent.get("id")}, payload)
        else:
            payload["id"] = str(uuid.uuid4())
            _safe_insert("agent_presence", payload)

        return jsonify({"ok": True, "message": "Presence updated"})

    @app.get("/api/admin/online_agents")
    @app.get("/api/admin/online")
    def admin_online_agents_live():
        rows = _safe_select("agent_presence", {}, "*", 5000, "last_seen", True)
        cutoff = datetime.utcnow() - timedelta(minutes=5)

        out = []
        for r in rows:
            last_seen_raw = str(r.get("last_seen") or "")
            try:
                dt = datetime.fromisoformat(last_seen_raw.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                continue
            if dt < cutoff:
                continue
            out.append({
                "name": r.get("agent_name"),
                "email": r.get("agent_email"),
                "region": r.get("region"),
                "town": r.get("town"),
                "current_page": r.get("current_page"),
                "last_seen": r.get("last_seen"),
            })

        return jsonify({"ok": True, "rows": out})

    @app.get("/api/admin/agent_related_records")
    def admin_agent_related_records():
        agent_id = str(request.args.get("id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "Agent id required"}), 400

        drivers = [r for r in _safe_select("drivers", {}, "*", 10000) if str(r.get("recruiter_agent_id") or "") == agent_id]
        clients = [r for r in _safe_select("clients", {}, "*", 10000) if str(r.get("recruiter_agent_id") or "") == agent_id]

        return jsonify({"ok": True, "drivers": drivers[:100], "clients": clients[:100]})

    @app.get("/town-leader/dashboard")
    def town_leader_dashboard():
        email = _current_email()
        if not email:
            return "Unauthorized", 401

        agent = _agent_by_email(email)
        if not agent:
            return "Town leader not found", 404

        role = str(agent.get("role") or "").upper()
        if role not in ("TOWN_LEADER", "TOWN_PROMOTER"):
            return "Unauthorized", 403

        return render_template("town_leader_dashboard.html")

    @app.get("/api/town_leader/me")
    def town_leader_me():
        email = _current_email()
        if not email:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

        leader = _agent_by_email(email)
        if not leader:
            return jsonify({"ok": False, "error": "Town leader not found"}), 404

        town = leader.get("town") or ""
        region = leader.get("region") or ""

        agents = [a for a in (_safe_select("agent_profiles", {}, "*", 5000) or _safe_select("agents", {}, "*", 5000))
                  if (a.get("town") or "") == town or (town and (a.get("region") or "") == region)]

        drivers = [d for d in _safe_select("drivers", {}, "*", 10000)
                   if (d.get("town") or "") == town or (town and (d.get("region") or "") == region)]

        clients = [c for c in _safe_select("clients", {}, "*", 10000)
                   if (c.get("town") or "") == town or (town and (c.get("region") or "") == region)]

        return jsonify({
            "ok": True,
            "leader": {
                "name": leader.get("full_name") or leader.get("username") or leader.get("email"),
                "email": leader.get("email"),
                "town": town,
                "region": region,
                "role": leader.get("role") or "TOWN_LEADER",
            },
            "summary": {
                "agents": len(agents),
                "drivers": len(drivers),
                "clients": len(clients),
            },
            "agents": agents[:100],
            "drivers": drivers[:100],
            "clients": clients[:100],
        })

    @app.post("/api/town_leader/create_agent")
    def town_leader_create_agent():
        email = _current_email()
        if not email:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

        leader = _agent_by_email(email)
        if not leader:
            return jsonify({"ok": False, "error": "Town leader not found"}), 404

        if str(leader.get("role") or "").upper() not in ("TOWN_LEADER", "TOWN_PROMOTER"):
            return jsonify({"ok": False, "error": "Forbidden"}), 403

        data = request.get_json(force=True) or {}
        full_name = str(data.get("full_name") or "").strip()
        agent_email = str(data.get("email") or "").strip().lower()
        phone = str(data.get("phone") or "").strip()

        if not full_name or not agent_email or not phone:
            return jsonify({"ok": False, "error": "full_name, email, phone required"}), 400

        payload = {
            "id": str(uuid.uuid4()),
            "full_name": full_name,
            "email": agent_email,
            "phone": phone,
            "town": leader.get("town") or "",
            "region": leader.get("region") or "",
            "status": "PENDING_APPROVAL",
            "role": "AGENT",
            "team_leader_id": leader.get("id"),
            "team_leader_name": leader.get("full_name") or leader.get("username") or leader.get("email"),
            "created_at": _now_iso(),
        }
        res = _safe_insert("agent_profiles", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Agent created under town leader"})
