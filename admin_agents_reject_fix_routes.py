from datetime import datetime
from flask import jsonify, request


def register_admin_agents_reject_fix_routes(app, sb_admin):
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

    def _agent_profiles():
        return _safe_select("agent_profiles", {}, "*", 10000)

    def _agents_table():
        return _safe_select("agents", {}, "*", 10000)

    @app.get("/api/admin/agents_list_safe")
    def admin_agents_list_safe():
        profiles = _agent_profiles()
        agents = _agents_table()

        merged = {}
        for r in profiles:
            key = str(r.get("id") or r.get("email") or "").strip()
            if not key:
                continue
            merged[key] = {
                "id": r.get("id"),
                "full_name": r.get("full_name") or r.get("username") or r.get("email"),
                "username": r.get("username"),
                "email": r.get("email"),
                "phone": r.get("phone") or r.get("phone_number"),
                "town": r.get("town"),
                "region": r.get("region"),
                "status": r.get("status") or "PENDING",
                "team_leader_name": r.get("team_leader_name") or "",
                "role": r.get("role") or "AGENT",
                "referral_code": r.get("referral_code") or "",
                "created_at": r.get("created_at"),
                "source": "agent_profiles",
            }

        for r in agents:
            key = str(r.get("id") or r.get("email") or "").strip()
            if not key:
                continue

            existing = merged.get(key, {})
            merged[key] = {
                "id": existing.get("id") or r.get("id"),
                "full_name": existing.get("full_name") or r.get("full_name") or r.get("username") or r.get("email"),
                "username": existing.get("username") or r.get("username"),
                "email": existing.get("email") or r.get("email"),
                "phone": existing.get("phone") or r.get("phone") or r.get("phone_number"),
                "town": existing.get("town") or r.get("town"),
                "region": existing.get("region") or r.get("region"),
                "status": existing.get("status") or r.get("status") or "PENDING",
                "team_leader_name": existing.get("team_leader_name") or r.get("team_leader_name") or "",
                "role": existing.get("role") or r.get("role") or "AGENT",
                "referral_code": existing.get("referral_code") or r.get("referral_code") or "",
                "created_at": existing.get("created_at") or r.get("created_at"),
                "source": existing.get("source") or "agents",
            }

        rows = list(merged.values())
        rows.sort(key=lambda x: str(x.get("full_name") or x.get("email") or "").lower())
        return jsonify({"ok": True, "rows": rows})

    @app.post("/api/admin/reject_driver")
    def admin_reject_driver():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        reason = str(data.get("reason") or "").strip()

        if not row_id:
            return jsonify({"ok": False, "error": "Driver id required"}), 400
        if not reason:
            return jsonify({"ok": False, "error": "Reject reason required"}), 400

        payload = {
            "status": "REJECTED",
            "rejection_reason": reason,
            "rejected_at": _now_iso(),
        }
        res = _safe_update("drivers", {"id": row_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Driver rejected"})

    @app.post("/api/admin/reject_client")
    def admin_reject_client():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        reason = str(data.get("reason") or "").strip()

        if not row_id:
            return jsonify({"ok": False, "error": "Client id required"}), 400
        if not reason:
            return jsonify({"ok": False, "error": "Reject reason required"}), 400

        payload = {
            "status": "REJECTED",
            "rejection_reason": reason,
            "rejected_at": _now_iso(),
        }
        res = _safe_update("clients", {"id": row_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Client rejected"})

    @app.post("/api/admin/reject_agent")
    def admin_reject_agent():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("agent_id") or "").strip()
        reason = str(data.get("reason") or "").strip()

        if not row_id:
            return jsonify({"ok": False, "error": "Agent id required"}), 400
        if not reason:
            return jsonify({"ok": False, "error": "Reject reason required"}), 400

        payload = {
            "status": "REJECTED",
            "rejection_reason": reason,
            "rejected_at": _now_iso(),
        }

        res = _safe_update("agent_profiles", {"id": row_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        _safe_update("agents", {"id": row_id}, payload)
        return jsonify({"ok": True, "message": "Agent rejected"})
