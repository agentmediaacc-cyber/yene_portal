from datetime import datetime
import uuid
import random
import string
from flask import jsonify, request


def register_admin_compat_routes(app, sb_admin):
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

    def _safe_int(v, default=0):
        try:
            return int(v or 0)
        except Exception:
            return default

    def _approved(status):
        s = str(status or "").upper()
        return s in ("ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED")

    def _now_iso():
        return datetime.utcnow().isoformat() + "Z"

    def _get_driver_code(row):
        return (
            row.get("external_code")
            or row.get("license_number")
            or row.get("app_code")
            or row.get("yene_code")
            or ""
        )

    def _get_client_code(row):
        return (
            row.get("external_code")
            or row.get("yene_code")
            or row.get("app_code")
            or row.get("client_code")
            or ""
        )

    def _rules():
        return _safe_select("payment_rules", {}, "*", 50, "updated_at", True)

    def _best_driver_rate():
        best = 0.0
        for r in _rules():
            best = max(best, _safe_float(r.get("driver_reg")))
        return best

    def _best_client_rate():
        best = 0.0
        for r in _rules():
            best = max(best, _safe_float(r.get("client_reg")))
        return best

    def _agent_directory():
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

    def _agent_name_map():
        out = {}
        for a in _agent_directory():
            out[str(a.get("id") or "")] = a.get("full_name") or a.get("username") or a.get("email") or "Agent"
        return out

    @app.get("/api/admin/pending_drivers")
    def admin_pending_drivers():
        rows = _safe_select("drivers", {}, "*", 5000, "created_at", True)
        pending = []
        for r in rows:
            if _approved(r.get("status")):
                continue
            pending.append({
                "id": r.get("id"),
                "full_name": r.get("full_name"),
                "phone": r.get("phone") or r.get("phone_number"),
                "town": r.get("town"),
                "region": r.get("region"),
                "recruiter_name": r.get("recruiter_name") or r.get("agent_name"),
                "created_at": r.get("created_at"),
                "status": r.get("status"),
                "code": _get_driver_code(r),
            })
        return jsonify({"ok": True, "rows": pending})

    @app.get("/api/admin/pending_clients")
    def admin_pending_clients():
        rows = _safe_select("clients", {}, "*", 5000, "created_at", True)
        pending = []
        for r in rows:
            if _approved(r.get("status")):
                continue
            pending.append({
                "id": r.get("id"),
                "full_name": r.get("full_name"),
                "phone": r.get("phone") or r.get("phone_number"),
                "town": r.get("town"),
                "region": r.get("region"),
                "recruiter_name": r.get("recruiter_name") or r.get("agent_name"),
                "created_at": r.get("created_at"),
                "status": r.get("status"),
                "code": _get_client_code(r),
            })
        return jsonify({"ok": True, "rows": pending})

    @app.post("/api/admin/approve_driver")
    def admin_approve_driver():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Driver id required"}), 400
        res = _safe_update("drivers", {"id": row_id}, {"status": "APPROVED", "admin_approved": True})
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500
        return jsonify({"ok": True, "message": "Driver approved"})

    @app.post("/api/admin/approve_client")
    def admin_approve_client():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Client id required"}), 400
        res = _safe_update("clients", {"id": row_id}, {"status": "APPROVED", "admin_approved": True})
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500
        return jsonify({"ok": True, "message": "Client approved"})

    @app.get("/api/admin/finance/summary")
    @app.get("/api/admin/finance_summary")
    def admin_finance_summary():
        agents = _agent_directory()
        drivers = _safe_select("drivers", {}, "*", 10000)
        clients = _safe_select("clients", {}, "*", 10000)

        driver_rate = _best_driver_rate()
        client_rate = _best_client_rate()

        region_map = {}
        for a in agents:
            region = a.get("region") or a.get("town") or "Unassigned"
            region_map.setdefault(region, {"region": region, "agents": 0, "total_due": 0.0})
            region_map[region]["agents"] += 1

        for d in drivers:
            if not _approved(d.get("status")):
                continue
            recruiter_id = str(d.get("recruiter_agent_id") or "")
            agent = _agent_by_id(recruiter_id) if recruiter_id else None
            region = (agent or {}).get("region") or d.get("region") or d.get("town") or "Unassigned"
            region_map.setdefault(region, {"region": region, "agents": 0, "total_due": 0.0})
            region_map[region]["total_due"] += driver_rate

        for c in clients:
            if not _approved(c.get("status")):
                continue
            recruiter_id = str(c.get("recruiter_agent_id") or "")
            agent = _agent_by_id(recruiter_id) if recruiter_id else None
            region = (agent or {}).get("region") or c.get("region") or c.get("town") or "Unassigned"
            region_map.setdefault(region, {"region": region, "agents": 0, "total_due": 0.0})
            region_map[region]["total_due"] += client_rate

        rows = list(region_map.values())
        rows.sort(key=lambda x: str(x.get("region") or ""))
        for r in rows:
            r["total_due"] = round(r["total_due"], 2)

        return jsonify({"ok": True, "rows": rows})

    @app.get("/api/admin/payment_due")
    @app.get("/api/admin/agent_due")
    @app.get("/api/admin/finance/due")
    def admin_payment_due():
        agents = _agent_directory()
        drivers = _safe_select("drivers", {}, "*", 10000)
        clients = _safe_select("clients", {}, "*", 10000)

        driver_rate = _best_driver_rate()
        client_rate = _best_client_rate()

        due_map = {}
        for a in agents:
            aid = str(a.get("id") or "")
            due_map[aid] = {
                "agent_id": aid,
                "agent": a.get("full_name") or a.get("username") or a.get("email"),
                "email": a.get("email"),
                "phone": a.get("phone") or a.get("phone_number"),
                "region": a.get("region") or a.get("town") or "Unassigned",
                "amount_due": 0.0,
                "total_paid": 0.0,
            }

        for d in drivers:
            if _approved(d.get("status")) and str(d.get("recruiter_agent_id") or "") in due_map:
                due_map[str(d.get("recruiter_agent_id"))]["amount_due"] += driver_rate

        for c in clients:
            if _approved(c.get("status")) and str(c.get("recruiter_agent_id") or "") in due_map:
                due_map[str(c.get("recruiter_agent_id"))]["amount_due"] += client_rate

        ledger = _safe_select("agent_wallet_ledger", {}, "*", 10000)
        for l in ledger:
            aid = str(l.get("agent_id") or "")
            if aid in due_map:
                entry_type = str(l.get("entry_type") or "").lower()
                amount = _safe_float(l.get("amount"))
                if entry_type == "debit":
                    due_map[aid]["total_paid"] += amount

        rows = list(due_map.values())
        rows.sort(key=lambda x: x["agent"] or "")
        for r in rows:
            r["amount_due"] = round(r["amount_due"], 2)
            r["total_paid"] = round(r["total_paid"], 2)

        return jsonify({"ok": True, "rows": rows})

    @app.get("/api/admin/agent_wallet_ledger")
    @app.get("/api/admin/ledger")
    @app.get("/api/admin/finance/ledger")
    def admin_agent_wallet_ledger():
        agent_id = str(request.args.get("agent_id") or "").strip()
        email = str(request.args.get("email") or "").strip().lower()

        rows = _safe_select("agent_wallet_ledger", {}, "*", 10000, "created_at", True)
        out = []
        for r in rows:
            if agent_id and str(r.get("agent_id") or "") != agent_id:
                continue
            if email and str(r.get("agent_email") or "").strip().lower() != email:
                continue
            out.append(r)

        return jsonify({"ok": True, "rows": out})

    @app.get("/api/admin/online_agents")
    @app.get("/api/admin/online")
    def admin_online_agents():
        # Safe empty response instead of 404 until live presence is added
        return jsonify({"ok": True, "rows": []})

    @app.get("/api/admin/agent_detail")
    def admin_agent_detail():
        agent_id = str(request.args.get("id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "Agent id required"}), 400

        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        drivers = [r for r in _safe_select("drivers", {}, "*", 10000) if str(r.get("recruiter_agent_id") or "") == agent_id]
        clients = [r for r in _safe_select("clients", {}, "*", 10000) if str(r.get("recruiter_agent_id") or "") == agent_id]

        return jsonify({
            "ok": True,
            "agent": agent,
            "drivers": drivers,
            "clients": clients,
        })

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
        description = str(data.get("description") or "Admin withdrawal").strip()
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

        temp_password = "Yene@" + "".join(random.choices(string.digits, k=6))
        try:
            sb_admin.auth.admin.update_user_by_id(auth_id, {"password": temp_password})
            return jsonify({"ok": True, "temp_password": temp_password, "message": "Password reset"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
