from datetime import datetime
import uuid
from flask import jsonify, request


def register_admin_extended_routes(app, sb_admin):
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

    def _safe_float(v, default=0.0):
        try:
            return float(v or 0)
        except Exception:
            return default

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

    @app.get("/api/admin/driver_detail")
    def admin_driver_detail():
        driver_id = str(request.args.get("id") or "").strip()
        if not driver_id:
            return jsonify({"ok": False, "error": "Driver id required"}), 400

        rows = _safe_select("drivers", {"id": driver_id}, "*", 1)
        if not rows:
            return jsonify({"ok": False, "error": "Driver not found"}), 404

        d = rows[0]
        recruiter = _agent_by_id(str(d.get("recruiter_agent_id") or "")) if d.get("recruiter_agent_id") else None

        return jsonify({
            "ok": True,
            "driver": {
                "id": d.get("id"),
                "full_name": d.get("full_name"),
                "phone": d.get("phone") or d.get("phone_number"),
                "town": d.get("town"),
                "region": d.get("region"),
                "status": d.get("status"),
                "car_type": d.get("car_details") or d.get("car_type") or "",
                "code": d.get("external_code") or d.get("license_number") or d.get("app_code") or d.get("yene_code") or "",
                "created_at": d.get("created_at"),
                "recruiter_name": d.get("recruiter_name") or (recruiter or {}).get("full_name") or (recruiter or {}).get("email") or "",
                "recruiter_email": (recruiter or {}).get("email") or "",
            }
        })

    @app.get("/api/admin/client_detail")
    def admin_client_detail():
        client_id = str(request.args.get("id") or "").strip()
        if not client_id:
            return jsonify({"ok": False, "error": "Client id required"}), 400

        rows = _safe_select("clients", {"id": client_id}, "*", 1)
        if not rows:
            return jsonify({"ok": False, "error": "Client not found"}), 404

        c = rows[0]
        recruiter = _agent_by_id(str(c.get("recruiter_agent_id") or "")) if c.get("recruiter_agent_id") else None

        return jsonify({
            "ok": True,
            "client": {
                "id": c.get("id"),
                "full_name": c.get("full_name"),
                "phone": c.get("phone") or c.get("phone_number"),
                "town": c.get("town"),
                "region": c.get("region"),
                "status": c.get("status"),
                "code": c.get("external_code") or c.get("yene_code") or c.get("app_code") or c.get("client_code") or "",
                "created_at": c.get("created_at"),
                "recruiter_name": c.get("recruiter_name") or (recruiter or {}).get("full_name") or (recruiter or {}).get("email") or "",
                "recruiter_email": (recruiter or {}).get("email") or "",
            }
        })

    @app.post("/api/admin/create_town_leader")
    def admin_create_town_leader():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        town = str(data.get("town") or "").strip()
        region = str(data.get("region") or "").strip()

        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400

        payload = {
            "role": "TOWN_LEADER",
            "status": "ACTIVE",
        }
        if town:
            payload["town"] = town
        if region:
            payload["region"] = region

        res = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        _safe_update("agents", {"id": agent_id}, payload)
        return jsonify({"ok": True, "message": "Town leader assigned"})

    @app.get("/api/admin/bonus_matrix")
    def admin_bonus_matrix():
        rules = _safe_select("payment_rules", {}, "*", 100, "updated_at", True)
        if rules:
            return jsonify({"ok": True, "rows": rules})

        default_row = {
            "id": "default",
            "rule_name": "Default",
            "driver_reg": 10,
            "client_reg": 10,
            "referral_pct": 0,
            "first_trip_bonus": 0,
            "daily_5_clients_bonus": 50,
            "daily_5_drivers_bonus": 100,
            "weekly_30_activations_bonus": 500,
            "monthly_50_driver_activations_bonus": 2500,
        }
        return jsonify({"ok": True, "rows": [default_row]})

    @app.post("/api/admin/bonus_matrix/save")
    def admin_bonus_matrix_save():
        data = request.get_json(force=True) or {}
        payload = {
            "rule_name": data.get("rule_name") or "Default",
            "driver_reg": _safe_float(data.get("driver_reg")),
            "client_reg": _safe_float(data.get("client_reg")),
            "referral_pct": _safe_float(data.get("referral_pct")),
            "first_trip_bonus": _safe_float(data.get("first_trip_bonus")),
            "daily_5_clients_bonus": _safe_float(data.get("daily_5_clients_bonus")),
            "daily_5_drivers_bonus": _safe_float(data.get("daily_5_drivers_bonus")),
            "weekly_30_activations_bonus": _safe_float(data.get("weekly_30_activations_bonus")),
            "monthly_50_driver_activations_bonus": _safe_float(data.get("monthly_50_driver_activations_bonus")),
            "updated_at": _now_iso(),
        }

        row_id = str(data.get("id") or "").strip()
        if row_id and row_id != "default":
            res = _safe_update("payment_rules", {"id": row_id}, payload)
        else:
            payload["id"] = str(uuid.uuid4())
            payload["created_at"] = _now_iso()
            res = _safe_insert("payment_rules", payload)

        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Bonus matrix saved"})
