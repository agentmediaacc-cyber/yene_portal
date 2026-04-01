from datetime import datetime
import uuid
import random
import string
from flask import jsonify, request


def register_admin_actions_routes(app, sb_admin):
    NAMIBIA_REGIONS = [
        "Khomas", "Kavango East", "Kavango West", "Erongo", "Ohangwena",
        "Oshana", "Omusati", "Oshikoto", "Otjozondjupa", "Kunene",
        "Zambezi", "Hardap", "Karas", "Omaheke"
    ]

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

    def _approved(status):
        s = str(status or "").upper()
        return s in ("ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED")

    def _now_iso():
        return datetime.utcnow().isoformat() + "Z"

    def _temp_password():
        return "Yene@" + "".join(random.choices(string.digits, k=6))

    def _agents():
        rows = _safe_select("agent_profiles", {}, "*", 5000)
        if rows:
            return rows
        return _safe_select("agents", {}, "*", 5000)

    def _agent_by_id(agent_id):
        rows = _safe_select("agent_profiles", {"id": agent_id}, "*", 1)
        if rows:
            return rows[0]
        rows = _safe_select("agents", {"id": agent_id}, "*", 1)
        if rows:
            return rows[0]
        return None

    def _team_leader_name(agent):
        return agent.get("team_leader_name") or ""

    def _wallet_rows(agent_id, email="", auth_id=""):
        rows = _safe_select("agent_wallet_ledger", {}, "*", 10000, "created_at", True)
        out = []
        for r in rows:
            if agent_id and str(r.get("agent_id") or "") == str(agent_id):
                out.append(r)
                continue
            if email and str(r.get("agent_email") or "").lower() == str(email).lower():
                out.append(r)
                continue
            if auth_id and str(r.get("agent_auth_id") or "") == str(auth_id):
                out.append(r)
                continue
        return out

    def _wallet_summary(agent):
        rows = _wallet_rows(agent.get("id"), agent.get("email"), agent.get("auth_id"))
        available = 0.0
        credits = 0.0
        debits = 0.0
        for r in rows:
            amount = _safe_float(r.get("amount"))
            entry_type = str(r.get("entry_type") or "").lower()
            if entry_type == "credit":
                available += amount
                credits += amount
            else:
                available -= amount
                debits += amount
        return {
            "available": round(available, 2),
            "credits": round(credits, 2),
            "debits": round(debits, 2),
            "rows": rows[:50],
        }

    def _driver_rate():
        best = 0.0
        for r in _safe_select("payment_rules", {}, "*", 100):
            best = max(best, _safe_float(r.get("driver_reg")))
        return best

    def _client_rate():
        best = 0.0
        for r in _safe_select("payment_rules", {}, "*", 100):
            best = max(best, _safe_float(r.get("client_reg")))
        return best

    @app.get("/api/admin/agents_safe")
    def admin_agents_safe():
        rows = _agents()
        out = []
        for r in rows:
            out.append({
                "id": r.get("id"),
                "full_name": r.get("full_name") or r.get("username") or r.get("email"),
                "username": r.get("username"),
                "email": r.get("email"),
                "phone": r.get("phone") or r.get("phone_number"),
                "town": r.get("town"),
                "region": r.get("region"),
                "status": r.get("status") or "PENDING",
                "team_leader_name": _team_leader_name(r),
                "role": r.get("role") or "AGENT",
                "referral_code": r.get("referral_code") or "",
                "created_at": r.get("created_at"),
            })
        return jsonify({"ok": True, "rows": out})

    @app.get("/api/admin/regions")
    def admin_regions():
        return jsonify({"ok": True, "rows": [{"name": x} for x in NAMIBIA_REGIONS]})

    @app.get("/api/admin/team_leader_options")
    def admin_team_leader_options():
        rows = _agents()
        out = []
        for r in rows:
            status = str(r.get("status") or "").upper()
            if status not in ("ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED"):
                continue
            out.append({
                "id": r.get("id"),
                "name": r.get("full_name") or r.get("username") or r.get("email"),
                "town": r.get("town"),
                "region": r.get("region"),
            })
        out.sort(key=lambda x: x["name"] or "")
        return jsonify({"ok": True, "rows": out})

    @app.get("/api/admin/agent_profile")
    def admin_agent_profile():
        agent_id = str(request.args.get("id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "Agent id required"}), 400

        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        drivers = [r for r in _safe_select("drivers", {}, "*", 10000) if str(r.get("recruiter_agent_id") or "") == agent_id]
        clients = [r for r in _safe_select("clients", {}, "*", 10000) if str(r.get("recruiter_agent_id") or "") == agent_id]

        wallet = _wallet_summary(agent)

        driver_rate = _driver_rate()
        client_rate = _client_rate()

        approved_drivers = len([r for r in drivers if _approved(r.get("status"))])
        approved_clients = len([r for r in clients if _approved(r.get("status"))])

        return jsonify({
            "ok": True,
            "agent": {
                "id": agent.get("id"),
                "full_name": agent.get("full_name") or agent.get("username") or agent.get("email"),
                "email": agent.get("email"),
                "phone": agent.get("phone") or agent.get("phone_number"),
                "town": agent.get("town"),
                "region": agent.get("region"),
                "status": agent.get("status"),
                "team_leader_name": agent.get("team_leader_name") or "",
                "role": agent.get("role") or "AGENT",
                "referral_code": agent.get("referral_code") or "",
                "created_at": agent.get("created_at"),
            },
            "stats": {
                "drivers_total": len(drivers),
                "clients_total": len(clients),
                "approved_drivers": approved_drivers,
                "approved_clients": approved_clients,
                "estimated_bonus": round((approved_drivers * driver_rate) + (approved_clients * client_rate), 2),
            },
            "wallet": wallet,
            "recent_drivers": drivers[:20],
            "recent_clients": clients[:20],
        })

    @app.post("/api/admin/approve_agent")
    def admin_approve_agent():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400

        payload = {"status": "ACTIVE"}
        res = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        _safe_update("agents", {"id": agent_id}, payload)
        return jsonify({"ok": True, "message": "Agent approved"})

    @app.post("/api/admin/block_agent")
    def admin_block_agent():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400

        payload = {"status": "BLOCKED"}
        res = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        _safe_update("agents", {"id": agent_id}, payload)
        return jsonify({"ok": True, "message": "Agent blocked"})

    @app.post("/api/admin/assign_team_leader")
    def admin_assign_team_leader():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        leader_id = str(data.get("leader_id") or "").strip()
        if not agent_id or not leader_id:
            return jsonify({"ok": False, "error": "agent_id and leader_id required"}), 400

        leader = _agent_by_id(leader_id)
        if not leader:
            return jsonify({"ok": False, "error": "Leader not found"}), 404

        payload = {
            "team_leader_id": leader_id,
            "team_leader_name": leader.get("full_name") or leader.get("username") or leader.get("email"),
            "referred_by": leader_id,
        }
        res = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Team leader assigned"})

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
            "town": town or "Unassigned",
            "region": region or "Unassigned",
            "status": "ACTIVE",
        }

        res = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        _safe_update("agents", {"id": agent_id}, payload)
        return jsonify({"ok": True, "message": "Town leader created / updated"})

    @app.post("/api/admin/reset_agent_password")
    def admin_reset_agent_password():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400

        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        auth_id = agent.get("auth_id") or agent.get("user_id")
        if not auth_id:
            return jsonify({"ok": False, "error": "Agent auth id not found"}), 400

        temp_password = _temp_password()
        try:
            sb_admin.auth.admin.update_user_by_id(auth_id, {"password": temp_password})
            return jsonify({"ok": True, "message": "Password reset", "temp_password": temp_password})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.post("/api/admin/agent_wallet/credit")
    def admin_agent_wallet_credit():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        amount = _safe_float(data.get("amount"))
        description = str(data.get("description") or "Admin credit").strip()

        if not agent_id or amount <= 0:
            return jsonify({"ok": False, "error": "agent_id and positive amount required"}), 400

        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        payload = {
            "id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "agent_email": agent.get("email"),
            "agent_auth_id": agent.get("auth_id"),
            "entry_type": "credit",
            "status": "POSTED",
            "amount": amount,
            "description": description,
            "reference": f"ADMIN-CREDIT-{uuid.uuid4().hex[:8].upper()}",
            "created_at": _now_iso(),
        }
        res = _safe_insert("agent_wallet_ledger", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Funds credited"})

    @app.post("/api/admin/agent_wallet/debit")
    def admin_agent_wallet_debit():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        amount = _safe_float(data.get("amount"))
        description = str(data.get("description") or "Admin debit").strip()

        if not agent_id or amount <= 0:
            return jsonify({"ok": False, "error": "agent_id and positive amount required"}), 400

        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        payload = {
            "id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "agent_email": agent.get("email"),
            "agent_auth_id": agent.get("auth_id"),
            "entry_type": "debit",
            "status": "POSTED",
            "amount": amount,
            "description": description,
            "reference": f"ADMIN-DEBIT-{uuid.uuid4().hex[:8].upper()}",
            "created_at": _now_iso(),
        }
        res = _safe_insert("agent_wallet_ledger", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Funds debited"})

    @app.post("/api/admin/mark_paid")
    def admin_mark_paid():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        amount = _safe_float(data.get("amount"))
        if not agent_id or amount <= 0:
            return jsonify({"ok": False, "error": "agent_id and positive amount required"}), 400

        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        payload = {
            "id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "agent_email": agent.get("email"),
            "agent_auth_id": agent.get("auth_id"),
            "entry_type": "debit",
            "status": "POSTED",
            "amount": amount,
            "description": "Marked paid by admin",
            "reference": f"PAID-{uuid.uuid4().hex[:8].upper()}",
            "created_at": _now_iso(),
        }
        res = _safe_insert("agent_wallet_ledger", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Payment marked as sent"})

    @app.post("/api/admin/payment_rules/upsert")
    def admin_payment_rules_upsert():
        data = request.get_json(force=True) or {}
        payload = {
            "rule_name": data.get("rule_name") or "Default",
            "driver_reg": _safe_float(data.get("driver_reg")),
            "client_reg": _safe_float(data.get("client_reg")),
            "referral_pct": _safe_float(data.get("referral_pct")),
            "first_trip_bonus": _safe_float(data.get("first_trip_bonus")),
            "updated_at": _now_iso(),
        }

        rule_id = str(data.get("id") or "").strip()
        if rule_id:
            res = _safe_update("payment_rules", {"id": rule_id}, payload)
        else:
            payload["id"] = str(uuid.uuid4())
            payload["created_at"] = _now_iso()
            res = _safe_insert("payment_rules", payload)

        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Pricing / bonuses saved"})
