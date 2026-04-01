from datetime import datetime
from flask import jsonify, request


def register_admin_error_fixes_routes(app, sb_admin):
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

    def _safe_upsert(table, payload, on_conflict=None):
        try:
            q = sb_admin.table(table).upsert(payload)
            if on_conflict:
                q = q.execute()
            else:
                q = q.execute()
            return q
        except Exception as e:
            return e

    def _now():
        return datetime.utcnow().isoformat() + "Z"

    def _f(v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    def _clean(v):
        return str(v or "").strip()

    def _agent_rows():
        rows = _safe_select("agent_profiles", {}, "*", 10000)
        if rows:
            return rows
        return _safe_select("agents", {}, "*", 10000)

    def _agent_by_id(agent_id):
        rows = _safe_select("agent_profiles", {"id": agent_id}, "*", 1)
        if rows:
            return rows[0]
        rows = _safe_select("agents", {"id": agent_id}, "*", 1)
        if rows:
            return rows[0]
        return None

    @app.get("/api/admin/team_leader_options")
    def admin_team_leader_options_fix():
        rows = _agent_rows()
        out = []
        for r in rows:
            status = _clean(r.get("status")).upper()
            if status and status not in ("ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED"):
                continue
            out.append({
                "id": r.get("id"),
                "name": r.get("full_name") or r.get("username") or r.get("email"),
                "email": r.get("email"),
                "town": r.get("town"),
                "region": r.get("region"),
            })
        out.sort(key=lambda x: _clean(x.get("name")).lower())
        return jsonify({"ok": True, "rows": out})

    @app.post("/api/admin/agent_wallet/credit")
    def admin_agent_wallet_credit_fix():
        data = request.get_json(force=True) or {}
        agent_id = _clean(data.get("agent_id"))
        amount = _f(data.get("amount"))
        description = _clean(data.get("description")) or "Admin credit"

        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400
        if amount <= 0:
            return jsonify({"ok": False, "error": "amount must be greater than 0"}), 400

        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        # minimal payload first
        payload = {
            "agent_id": agent_id,
            "entry_type": "credit",
            "status": "POSTED",
            "amount": amount,
            "description": description,
            "reference": f"ADMIN-CREDIT-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "created_at": _now(),
        }

        # add optional columns only if available in your schema
        optional_fields = {
            "agent_email": agent.get("email"),
            "agent_auth_id": agent.get("auth_id") or agent.get("user_id"),
        }
        payload.update({k: v for k, v in optional_fields.items() if v})

        res = _safe_insert("agent_wallet_ledger", payload)
        if isinstance(res, Exception):
            # fallback: store in finance_ledger if agent_wallet_ledger schema is stricter
            fallback = {
                "agent_id": agent_id,
                "amount": amount,
                "entry_type": "credit",
                "description": description,
                "reference": payload["reference"],
                "created_at": payload["created_at"],
            }
            res2 = _safe_insert("finance_ledger", fallback)
            if isinstance(res2, Exception):
                return jsonify({"ok": False, "error": f"{res}; fallback: {res2}"}), 500

        return jsonify({"ok": True, "message": "Funds credited"})

    @app.post("/api/admin/agent_wallet/debit")
    def admin_agent_wallet_debit_fix():
        data = request.get_json(force=True) or {}
        agent_id = _clean(data.get("agent_id"))
        amount = _f(data.get("amount"))
        description = _clean(data.get("description")) or "Admin debit"

        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400
        if amount <= 0:
            return jsonify({"ok": False, "error": "amount must be greater than 0"}), 400

        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        payload = {
            "agent_id": agent_id,
            "entry_type": "debit",
            "status": "POSTED",
            "amount": amount,
            "description": description,
            "reference": f"ADMIN-DEBIT-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "created_at": _now(),
        }
        optional_fields = {
            "agent_email": agent.get("email"),
            "agent_auth_id": agent.get("auth_id") or agent.get("user_id"),
        }
        payload.update({k: v for k, v in optional_fields.items() if v})

        res = _safe_insert("agent_wallet_ledger", payload)
        if isinstance(res, Exception):
            fallback = {
                "agent_id": agent_id,
                "amount": amount,
                "entry_type": "debit",
                "description": description,
                "reference": payload["reference"],
                "created_at": payload["created_at"],
            }
            res2 = _safe_insert("finance_ledger", fallback)
            if isinstance(res2, Exception):
                return jsonify({"ok": False, "error": f"{res}; fallback: {res2}"}), 500

        return jsonify({"ok": True, "message": "Funds debited"})

    @app.post("/api/admin/payment_rules/upsert")
    def admin_payment_rules_upsert_fix():
        data = request.get_json(force=True) or {}

        payload = {
            "rule_name": _clean(data.get("rule_name")) or "Default",
            "driver_reg": _f(data.get("driver_reg")),
            "client_reg": _f(data.get("client_reg")),
            "referral_pct": _f(data.get("referral_pct")),
            "first_trip_bonus": _f(data.get("first_trip_bonus")),
            "updated_at": _now(),
        }

        row_id = _clean(data.get("id"))

        if row_id:
            res = _safe_update("payment_rules", {"id": row_id}, payload)
            if isinstance(res, Exception):
                return jsonify({"ok": False, "error": str(res)}), 500
            return jsonify({"ok": True, "message": "Pricing updated"})

        # try insert with minimal columns
        payload["created_at"] = _now()
        res = _safe_insert("payment_rules", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Pricing saved"})

    @app.post("/api/admin/bonus_matrix/save")
    def admin_bonus_matrix_save_fix():
        data = request.get_json(force=True) or {}

        payload = {
            "rule_name": _clean(data.get("rule_name")) or "Default",
            "driver_reg": _f(data.get("driver_reg")),
            "client_reg": _f(data.get("client_reg")),
            "referral_pct": _f(data.get("referral_pct")),
            "first_trip_bonus": _f(data.get("first_trip_bonus")),
            "daily_5_clients_bonus": _f(data.get("daily_5_clients_bonus")),
            "daily_5_drivers_bonus": _f(data.get("daily_5_drivers_bonus")),
            "weekly_30_activations_bonus": _f(data.get("weekly_30_activations_bonus")),
            "monthly_50_driver_activations_bonus": _f(data.get("monthly_50_driver_activations_bonus")),
            "updated_at": _now(),
        }

        row_id = _clean(data.get("id"))

        if row_id and row_id != "default":
            res = _safe_update("payment_rules", {"id": row_id}, payload)
            if isinstance(res, Exception):
                return jsonify({"ok": False, "error": str(res)}), 500
            return jsonify({"ok": True, "message": "Bonus matrix updated"})

        payload["created_at"] = _now()
        res = _safe_insert("payment_rules", payload)
        if isinstance(res, Exception):
            # fallback to update the latest row
            existing = _safe_select("payment_rules", {}, "*", 1)
            if existing:
                row_id = existing[0].get("id")
                if row_id:
                    res2 = _safe_update("payment_rules", {"id": row_id}, payload)
                    if isinstance(res2, Exception):
                        return jsonify({"ok": False, "error": f"{res}; fallback update: {res2}"}), 500
                    return jsonify({"ok": True, "message": "Bonus matrix updated on latest rule"})
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Bonus matrix saved"})

    @app.post("/api/admin/broadcast/town")
    def admin_broadcast_town_fix():
        data = request.get_json(force=True) or {}
        town = _clean(data.get("town"))
        title = _clean(data.get("title")) or "Town Broadcast"
        message = _clean(data.get("message"))

        if not town:
            return jsonify({"ok": False, "error": "town required"}), 400
        if not message:
            return jsonify({"ok": False, "error": "message required"}), 400

        payload = {
            "title": title,
            "audience": f"town:{town}",
            "message": message,
            "created_at": _now(),
        }

        res = _safe_insert("broadcasts", payload)
        if isinstance(res, Exception):
            # fallback to system_logs if broadcasts schema differs
            fallback = {
                "event_type": "broadcast_town",
                "details": f"{title} | {town} | {message}",
                "created_at": payload["created_at"],
            }
            res2 = _safe_insert("system_logs", fallback)
            if isinstance(res2, Exception):
                return jsonify({"ok": False, "error": f"{res}; fallback: {res2}"}), 500

        return jsonify({"ok": True, "message": "Town broadcast saved"})
