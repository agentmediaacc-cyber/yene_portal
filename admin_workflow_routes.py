from datetime import datetime, timedelta
from flask import jsonify, request


def register_admin_workflow_routes(app, sb_admin):
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

    def _approved(status):
        s = str(status or "").upper()
        return s in ("ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED")

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

    def _rules_latest():
        rows = _safe_select("payment_rules", {}, "*", 100, "updated_at", True)
        if rows:
            return rows[0]
        return {
            "rule_name": "No active payment rule",
            "driver_reg": 0,
            "client_reg": 0,
            "referral_pct": 0,
            "first_trip_bonus": 0,
            "daily_5_clients_bonus": 0,
            "daily_5_drivers_bonus": 0,
            "weekly_30_activations_bonus": 0,
            "monthly_50_driver_activations_bonus": 0,
        }

    def _date_range(scope):
        today = datetime.utcnow().date()
        if scope == "weekly":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
            return start, end
        if scope == "last_month":
            first_this = today.replace(day=1)
            last_prev = first_this - timedelta(days=1)
            start = last_prev.replace(day=1)
            end = last_prev
            return start, end
        start = today.replace(day=1)
        if today.month == 12:
            next_month = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month = today.replace(month=today.month + 1, day=1)
        end = next_month - timedelta(days=1)
        return start, end

    def _in_range(raw, start, end):
        if not raw:
            return False
        try:
            d = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
            return start <= d <= end
        except Exception:
            return False

    @app.get("/api/admin/agent_profile")
    def admin_agent_profile_workflow():
        agent_id = str(request.args.get("id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "Agent id required"}), 400

        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        drivers = [r for r in _safe_select("drivers", {}, "*", 10000) if str(r.get("recruiter_agent_id") or "") == agent_id]
        clients = [r for r in _safe_select("clients", {}, "*", 10000) if str(r.get("recruiter_agent_id") or "") == agent_id]
        ledger = [r for r in _safe_select("agent_wallet_ledger", {}, "*", 10000, "created_at", True)
                  if str(r.get("agent_id") or "") == agent_id or str(r.get("agent_email") or "").lower() == str(agent.get("email") or "").lower()]

        available = 0.0
        credits = 0.0
        debits = 0.0
        for r in ledger:
            amount = _safe_float(r.get("amount"))
            if str(r.get("entry_type") or "").lower() == "credit":
                available += amount
                credits += amount
            else:
                available -= amount
                debits += amount

        rules = _rules_latest()
        approved_drivers = len([r for r in drivers if _approved(r.get("status"))])
        approved_clients = len([r for r in clients if _approved(r.get("status"))])
        estimated_bonus = approved_drivers * _safe_float(rules.get("driver_reg")) + approved_clients * _safe_float(rules.get("client_reg"))

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
                "estimated_bonus": round(estimated_bonus, 2),
            },
            "wallet": {
                "available": round(available, 2),
                "credits": round(credits, 2),
                "debits": round(debits, 2),
                "rows": ledger[:50],
            },
            "recent_drivers": drivers[:20],
            "recent_clients": clients[:20],
        })

    @app.post("/api/admin/approve_agent")
    def admin_approve_agent_workflow():
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
    def admin_block_agent_workflow():
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

    @app.post("/api/admin/mark_paid")
    def admin_mark_paid_workflow():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        amount = _safe_float(data.get("amount"))
        if not agent_id or amount <= 0:
            return jsonify({"ok": False, "error": "agent_id and positive amount required"}), 400

        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        payload = {
            "agent_id": agent_id,
            "agent_email": agent.get("email"),
            "agent_auth_id": agent.get("auth_id"),
            "entry_type": "debit",
            "status": "POSTED",
            "amount": amount,
            "description": "Payout approved by admin",
            "reference": f"PAYOUT-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            "created_at": _now_iso(),
        }
        res = _safe_insert("agent_wallet_ledger", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Payout approved"})

    @app.post("/api/admin/payment_rules/upsert")
    def admin_payment_rules_upsert_workflow():
        data = request.get_json(force=True) or {}
        payload = {
            "rule_name": data.get("rule_name") or "Default",
            "driver_reg": _safe_float(data.get("driver_reg")),
            "client_reg": _safe_float(data.get("client_reg")),
            "referral_pct": _safe_float(data.get("referral_pct")),
            "first_trip_bonus": _safe_float(data.get("first_trip_bonus")),
            "updated_at": _now_iso(),
        }

        row_id = str(data.get("id") or "").strip()
        if row_id and row_id != "default":
            res = _safe_update("payment_rules", {"id": row_id}, payload)
        else:
            payload["created_at"] = _now_iso()
            res = _safe_insert("payment_rules", payload)

        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Pricing saved"})

    @app.get("/api/admin/bonus_calculator")
    def admin_bonus_calculator():
        scope = str(request.args.get("scope") or "monthly").strip().lower()
        start, end = _date_range(scope)
        rules = _rules_latest()

        driver_reg = _safe_float(rules.get("driver_reg"))
        client_reg = _safe_float(rules.get("client_reg"))
        daily_5_clients_bonus = _safe_float(rules.get("daily_5_clients_bonus"))
        daily_5_drivers_bonus = _safe_float(rules.get("daily_5_drivers_bonus"))
        weekly_30_activations_bonus = _safe_float(rules.get("weekly_30_activations_bonus"))
        monthly_50_driver_activations_bonus = _safe_float(rules.get("monthly_50_driver_activations_bonus"))

        agents = _agents()
        drivers = _safe_select("drivers", {}, "*", 10000)
        clients = _safe_select("clients", {}, "*", 10000)

        out = []
        for a in agents:
            aid = str(a.get("id") or "")
            agent_drivers = [r for r in drivers if str(r.get("recruiter_agent_id") or "") == aid and _approved(r.get("status")) and _in_range(r.get("created_at"), start, end)]
            agent_clients = [r for r in clients if str(r.get("recruiter_agent_id") or "") == aid and _approved(r.get("status")) and _in_range(r.get("created_at"), start, end)]

            base_bonus = len(agent_drivers) * driver_reg + len(agent_clients) * client_reg

            by_day_drivers = {}
            for d in agent_drivers:
                key = str(d.get("created_at") or "")[:10]
                by_day_drivers[key] = by_day_drivers.get(key, 0) + 1

            by_day_clients = {}
            for c in agent_clients:
                key = str(c.get("created_at") or "")[:10]
                by_day_clients[key] = by_day_clients.get(key, 0) + 1

            extra = 0.0
            extra += sum(daily_5_drivers_bonus for _, count in by_day_drivers.items() if count >= 5)
            extra += sum(daily_5_clients_bonus for _, count in by_day_clients.items() if count >= 5)

            total_activations = len(agent_drivers) + len(agent_clients)
            if scope == "weekly" and total_activations >= 30:
                extra += weekly_30_activations_bonus
            if scope in ("monthly", "last_month") and len(agent_drivers) >= 50:
                extra += monthly_50_driver_activations_bonus

            total_bonus = round(base_bonus + extra, 2)
            out.append({
                "agent_id": aid,
                "agent": a.get("full_name") or a.get("username") or a.get("email"),
                "email": a.get("email"),
                "town": a.get("town"),
                "region": a.get("region"),
                "drivers": len(agent_drivers),
                "clients": len(agent_clients),
                "base_bonus": round(base_bonus, 2),
                "extra_bonus": round(extra, 2),
                "total_bonus": total_bonus,
                "scope": scope,
                "range_start": start.isoformat(),
                "range_end": end.isoformat(),
            })

        out.sort(key=lambda x: x["total_bonus"], reverse=True)
        return jsonify({"ok": True, "rows": out})

    @app.get("/api/admin/payout_workflow")
    def admin_payout_workflow():
        rows = admin_bonus_calculator().json.get("rows", [])
        return jsonify({"ok": True, "rows": rows})

    @app.get("/api/admin/town_leader_approvals")
    def admin_town_leader_approvals():
        rows = _agents()
        out = []
        for r in rows:
            role = str(r.get("role") or "").upper()
            if role not in ("TOWN_LEADER", "TOWN_PROMOTER"):
                continue
            out.append({
                "id": r.get("id"),
                "full_name": r.get("full_name") or r.get("username") or r.get("email"),
                "email": r.get("email"),
                "town": r.get("town"),
                "region": r.get("region"),
                "status": r.get("status") or "PENDING",
                "role": role,
            })
        return jsonify({"ok": True, "rows": out})

    @app.post("/api/admin/town_leader/approve")
    def admin_town_leader_approve():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400

        payload = {"status": "ACTIVE"}
        res = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500
        _safe_update("agents", {"id": agent_id}, payload)
        return jsonify({"ok": True, "message": "Town leader approved"})

    @app.post("/api/admin/town_leader/block")
    def admin_town_leader_block():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400

        payload = {"status": "BLOCKED"}
        res = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500
        _safe_update("agents", {"id": agent_id}, payload)
        return jsonify({"ok": True, "message": "Town leader blocked"})
