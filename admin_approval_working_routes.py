from datetime import datetime
from flask import jsonify, request


def register_admin_approval_working_routes(app, sb_admin):
    def _safe_select(table, filters=None, cols="*", limit=None):
        filters = filters or {}
        try:
            q = sb_admin.table(table).select(cols)
            for k, v in filters.items():
                q = q.eq(k, v)
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

    def _now():
        return datetime.utcnow().isoformat() + "Z"

    @app.post("/api/admin/approve_agent")
    def approve_agent_final():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400

        payload = {
            "status": "ACTIVE",
            "approved_at": _now(),
            "approval_state": "APPROVED",
            "rejection_reason": None,
        }

        res1 = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res1, Exception):
            return jsonify({"ok": False, "error": str(res1)}), 500

        _safe_update("agents", {"id": agent_id}, payload)
        return jsonify({"ok": True, "message": "Agent approved successfully"})

    @app.post("/api/admin/reject_agent")
    def reject_agent_final():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        reason = str(data.get("reason") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400
        if not reason:
            return jsonify({"ok": False, "error": "reason required"}), 400

        payload = {
            "status": "REJECTED",
            "approval_state": "REJECTED",
            "rejected_at": _now(),
            "rejection_reason": reason,
        }

        res1 = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res1, Exception):
            return jsonify({"ok": False, "error": str(res1)}), 500

        _safe_update("agents", {"id": agent_id}, payload)
        return jsonify({"ok": True, "message": "Agent rejected successfully"})

    @app.post("/api/admin/approve_driver")
    def approve_driver_final():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Driver id required"}), 400

        payload = {
            "status": "APPROVED",
            "approved_at": _now(),
            "approval_state": "APPROVED",
            "rejection_reason": None,
            "admin_approved": True,
        }

        res = _safe_update("drivers", {"id": row_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Driver approved successfully"})

    @app.post("/api/admin/reject_driver")
    def reject_driver_final():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        reason = str(data.get("reason") or "").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Driver id required"}), 400
        if not reason:
            return jsonify({"ok": False, "error": "reason required"}), 400

        payload = {
            "status": "REJECTED",
            "approval_state": "REJECTED",
            "rejected_at": _now(),
            "rejection_reason": reason,
            "admin_approved": False,
        }

        res = _safe_update("drivers", {"id": row_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Driver rejected successfully"})

    @app.post("/api/admin/approve_client")
    def approve_client_final():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Client id required"}), 400

        payload = {
            "status": "APPROVED",
            "approved_at": _now(),
            "approval_state": "APPROVED",
            "rejection_reason": None,
            "admin_approved": True,
        }

        res = _safe_update("clients", {"id": row_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Client approved successfully"})

    @app.post("/api/admin/reject_client")
    def reject_client_final():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        reason = str(data.get("reason") or "").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Client id required"}), 400
        if not reason:
            return jsonify({"ok": False, "error": "reason required"}), 400

        payload = {
            "status": "REJECTED",
            "approval_state": "REJECTED",
            "rejected_at": _now(),
            "rejection_reason": reason,
            "admin_approved": False,
        }

        res = _safe_update("clients", {"id": row_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Client rejected successfully"})

    @app.get("/api/admin/pending_agents")
    def pending_agents():
        rows = _safe_select("agent_profiles", {}, "*", 10000)
        out = []
        for r in rows:
            status = str(r.get("status") or "").upper()
            if status in ("ACTIVE", "APPROVED", "BLOCKED"):
                continue
            out.append({
                "id": r.get("id"),
                "full_name": r.get("full_name") or r.get("username") or r.get("email"),
                "email": r.get("email"),
                "phone": r.get("phone") or r.get("phone_number"),
                "town": r.get("town"),
                "region": r.get("region"),
                "status": r.get("status") or "PENDING",
                "created_at": r.get("created_at"),
            })
        return jsonify({"ok": True, "rows": out})
