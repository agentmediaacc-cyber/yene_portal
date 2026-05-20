from datetime import datetime
from flask import jsonify, request


def register_admin_final_routes(app, sb_admin):
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

    def _safe_delete(table, filters):
        try:
            q = sb_admin.table(table).delete()
            for k, v in filters.items():
                q = q.eq(k, v)
            return q.execute()
        except Exception as e:
            return e

    def _safe_insert(table, payload):
        try:
            return sb_admin.table(table).insert(payload).execute()
        except Exception as e:
            return e

    def _now_iso():
        return datetime.utcnow().isoformat() + "Z"

    def _agent_by_id(agent_id):
        rows = _safe_select("agent_profiles", {"id": agent_id}, "*", 1)
        if rows:
            return rows[0]
        rows = _safe_select("agents", {"id": agent_id}, "*", 1)
        if rows:
            return rows[0]
        return None

    @app.post("/api/admin/agent/update")
    def admin_update_agent():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "Agent id required"}), 400

        payload = {
            "full_name": data.get("full_name"),
            "email": data.get("email"),
            "phone": data.get("phone"),
            "town": data.get("town"),
            "region": data.get("region"),
            "status": data.get("status"),
        }
        payload = {k: v for k, v in payload.items() if v not in (None, "")}

        res = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        _safe_update("agents", {"id": agent_id}, payload)
        return jsonify({"ok": True, "message": "Agent updated"})

    @app.post("/api/admin/driver/update")
    def admin_update_driver():
        data = request.get_json(force=True) or {}
        driver_id = str(data.get("id") or "").strip()
        if not driver_id:
            return jsonify({"ok": False, "error": "Driver id required"}), 400

        payload = {
            "full_name": data.get("full_name"),
            "phone": data.get("phone"),
            "town": data.get("town"),
            "region": data.get("region"),
            "status": data.get("status"),
            "car_details": data.get("car_type"),
            "external_code": data.get("code"),
        }
        payload = {k: v for k, v in payload.items() if v not in (None, "")}

        res = _safe_update("drivers", {"id": driver_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Driver updated"})

    @app.post("/api/admin/client/update")
    def admin_update_client():
        data = request.get_json(force=True) or {}
        client_id = str(data.get("id") or "").strip()
        if not client_id:
            return jsonify({"ok": False, "error": "Client id required"}), 400

        payload = {
            "full_name": data.get("full_name"),
            "phone": data.get("phone"),
            "town": data.get("town"),
            "region": data.get("region"),
            "status": data.get("status"),
            "external_code": data.get("code"),
            "yene_code": data.get("code"),
        }
        payload = {k: v for k, v in payload.items() if v not in (None, "")}

        res = _safe_update("clients", {"id": client_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Client updated"})

    @app.post("/api/admin/agent/delete")
    def admin_delete_agent_routes():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "Agent id required"}), 400

        _safe_delete("agent_profiles", {"id": agent_id})
        _safe_delete("agents", {"id": agent_id})
        return jsonify({"ok": True, "message": "Agent deleted"})

    @app.post("/api/admin/driver/delete")
    def admin_delete_driver():
        data = request.get_json(force=True) or {}
        driver_id = str(data.get("id") or "").strip()
        if not driver_id:
            return jsonify({"ok": False, "error": "Driver id required"}), 400

        res = _safe_delete("drivers", {"id": driver_id})
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Driver deleted"})

    @app.post("/api/admin/client/delete")
    def admin_delete_client():
        data = request.get_json(force=True) or {}
        client_id = str(data.get("id") or "").strip()
        if not client_id:
            return jsonify({"ok": False, "error": "Client id required"}), 400

        res = _safe_delete("clients", {"id": client_id})
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Client deleted"})

    @app.post("/api/admin/broadcast/town")
    def admin_broadcast_town():
        data = request.get_json(force=True) or {}
        town = str(data.get("town") or "").strip()
        title = str(data.get("title") or "Town Broadcast").strip()
        message = str(data.get("message") or "").strip()
        if not town or not message:
            return jsonify({"ok": False, "error": "town and message required"}), 400

        payload = {
            "title": title,
            "audience": f"town:{town}",
            "message": message,
            "created_at": _now_iso(),
        }
        res = _safe_insert("broadcasts", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Town broadcast saved"})

    @app.get("/api/admin/broadcasts")
    def admin_broadcasts():
        rows = _safe_select("agent_group_messages", {}, "*", 200, "created_at", True)
        if rows:
            return jsonify({"ok": True, "rows": rows})
        rows = _safe_select("broadcasts", {}, "*", 200, "created_at", True)
        return jsonify({"ok": True, "rows": rows})

    @app.post("/api/admin/broadcast")
    def admin_broadcast():
        data = request.get_json(force=True) or {}
        title = str(data.get("title") or "Broadcast").strip()
        message = str(data.get("message") or "").strip()
        if not message:
            return jsonify({"ok": False, "error": "message required"}), 400

        payload = {
            "title": title,
            "message": message,
            "audience": "agents",
            "status": "ACTIVE",
            "created_at": _now_iso(),
        }
        res = _safe_insert("agent_group_messages", payload)
        if isinstance(res, Exception):
            fallback = {
                "title": title,
                "message": message,
                "audience": "agents",
                "created_at": _now_iso(),
            }
            res = _safe_insert("broadcasts", fallback)
            if isinstance(res, Exception):
                return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Broadcast saved"})

    @app.post("/api/admin/bulk_assign_by_town")
    def admin_bulk_assign_by_town():
        data = request.get_json(force=True) or {}
        town = str(data.get("town") or "").strip()
        leader_id = str(data.get("leader_id") or "").strip()
        if not town or not leader_id:
            return jsonify({"ok": False, "error": "town and leader_id required"}), 400

        leader = _agent_by_id(leader_id)
        if not leader:
            return jsonify({"ok": False, "error": "Leader not found"}), 404

        rows = _safe_select("agent_profiles", {}, "*", 5000)
        count = 0
        for r in rows:
            if str(r.get("town") or "").strip().lower() != town.lower():
                continue
            if str(r.get("id") or "") == leader_id:
                continue
            payload = {
                "team_leader_id": leader_id,
                "team_leader_name": leader.get("full_name") or leader.get("username") or leader.get("email"),
                "referred_by": leader_id,
            }
            _safe_update("agent_profiles", {"id": r.get("id")}, payload)
            count += 1

        return jsonify({"ok": True, "message": f"{count} agents assigned to team leader", "count": count})
