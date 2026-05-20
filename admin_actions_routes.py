from datetime import datetime, timedelta
import json
import uuid
import random
import string
from flask import jsonify, request, session
from yene_shared import pick_region_access_rule


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

    def _clean(v):
        return str(v or "").strip()

    def _approved(status):
        s = str(status or "").upper()
        return s in ("ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED")

    def _now_iso():
        return datetime.utcnow().isoformat() + "Z"

    def _week_bounds():
        today = datetime.utcnow()
        monday = today - timedelta(days=today.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return monday, sunday

    def _admin_email():
        return (
            session.get("admin_email")
            or session.get("email")
            or session.get("agent_email")
            or "admin"
        )

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

    def _agent_identity_values(agent):
        vals = []
        for key in ("id", "auth_id", "user_id", "email", "referral_code"):
            val = _clean((agent or {}).get(key))
            if val and val not in vals:
                vals.append(val)
                lower = val.lower()
                if lower not in vals:
                    vals.append(lower)
        return vals

    def _matches_values(row, values, fields):
        wanted = {_clean(v) for v in values if _clean(v)}
        wanted_lower = {v.lower() for v in wanted}
        for field in fields:
            raw = _clean((row or {}).get(field))
            if raw in wanted or raw.lower() in wanted_lower:
                return True
        return False

    def _safe_insert_resilient(table, payload, drop_fields=None):
        res = _safe_insert(table, payload)
        if not isinstance(res, Exception):
            return res
        slim = dict(payload)
        for key in (drop_fields or []):
            slim.pop(key, None)
        return _safe_insert(table, slim)

    def _audit_log(action, target_type, target_id, amount=None, metadata=None):
        payload = {
            "id": str(uuid.uuid4()),
            "action": action,
            "target_type": target_type,
            "target_id": _clean(target_id),
            "admin_email": _admin_email(),
            "amount": _safe_float(amount) if amount is not None else None,
            "created_at": _now_iso(),
        }
        if metadata is not None:
            try:
                payload["metadata"] = metadata if isinstance(metadata, (dict, list)) else {"value": metadata}
            except Exception:
                payload["metadata"] = {"value": str(metadata)}
        return _safe_insert_resilient("admin_audit_log", payload, ("metadata",))

    def _all_rows(table, order_col="created_at"):
        return _safe_select(table, {}, "*", 10000, order_col, True)

    def _find_agent_by_any(value):
        ref = _clean(value)
        if not ref:
            return None
        for row in _agents():
            vals = _agent_identity_values(row)
            if ref in vals or ref.lower() in {v.lower() for v in vals}:
                return row
        return None

    def _find_recruiter_agent(row):
        fields = (
            "recruiter_agent_id",
            "agent_id",
            "recruiter_email",
            "created_by",
            "referral_code",
            "recruiter_auth_id",
            "agent_auth_id",
        )
        for field in fields:
            agent = _find_agent_by_any((row or {}).get(field))
            if agent:
                return agent
        return None

    def _rule_date_active(rule):
        now = datetime.utcnow().date()
        week_start = _clean(rule.get("week_start"))
        week_end = _clean(rule.get("week_end"))
        effective_from = _clean(rule.get("effective_from"))
        effective_to = _clean(rule.get("effective_to"))
        try:
            if week_start and week_end:
                start = datetime.fromisoformat(week_start[:10]).date()
                end = datetime.fromisoformat(week_end[:10]).date()
                return start <= now <= end
            if effective_from and effective_to:
                start = datetime.fromisoformat(effective_from[:10]).date()
                end = datetime.fromisoformat(effective_to[:10]).date()
                return start <= now <= end
            if effective_from:
                start = datetime.fromisoformat(effective_from[:10]).date()
                return start <= now
            if effective_to:
                end = datetime.fromisoformat(effective_to[:10]).date()
                return now <= end
        except Exception:
            return True
        return True

    def _payment_rule_rows():
        weekly = _safe_select("weekly_payment_settings", {}, "*", 500, "updated_at", True)
        if weekly:
            return weekly
        return _safe_select("payment_rules", {}, "*", 500, "updated_at", True)

    def _rule_amount(rule, kind):
        if kind == "driver":
            return _safe_float(
                rule.get("driver_reward")
                or rule.get("driver_reg")
                or rule.get("driver_register_amount")
                or rule.get("driver_amount")
            )
        return _safe_float(
            rule.get("client_reward")
            or rule.get("client_reg")
            or rule.get("client_register_amount")
            or rule.get("client_amount")
        )

    def _pick_payment_rule(region="", town=""):
        region = _clean(region)
        town = _clean(town)
        rules = [
            row for row in _payment_rule_rows()
            if _clean(row.get("status") or "active").lower() != "inactive" and _rule_date_active(row)
        ]
        if not rules:
            return None

        def score(rule):
            r_region = _clean(rule.get("region") or "Namibia")
            r_town = _clean(rule.get("town") or "All")
            points = 0
            if town and region and r_town.lower() == town.lower() and r_region.lower() == region.lower():
                points = 300
            elif region and r_region.lower() == region.lower() and r_town.lower() == "all":
                points = 200
            elif r_region.lower() == "namibia" and r_town.lower() == "all":
                points = 100
            elif r_region.lower() == region.lower():
                points = 150
            return points

        rules.sort(key=lambda row: (score(row), _clean(row.get("updated_at") or row.get("created_at"))), reverse=True)
        return rules[0] if score(rules[0]) > 0 else rules[0]

    def _wallet_balance_for(agent):
        rows = _wallet_rows(agent.get("id"), agent.get("email"), agent.get("auth_id"))
        balance = 0.0
        credits = 0.0
        debits = 0.0
        for row in rows:
            amount = _safe_float(row.get("amount"))
            entry_type = _clean(row.get("entry_type") or row.get("txn_type") or row.get("type")).lower()
            status = _clean(row.get("status") or "posted").lower()
            if status in {"rejected", "cancelled"}:
                continue
            if entry_type == "debit":
                debits += amount
                balance -= amount
            else:
                credits += amount
                balance += amount
        return {"balance": round(balance, 2), "credits": round(credits, 2), "debits": round(debits, 2), "rows": rows}

    def _statement_number():
        year = datetime.utcnow().year
        rows = _safe_select("finance_statements", {}, "statement_number", 5000, "created_at", True)
        max_seq = 0
        for row in rows:
            value = _clean(row.get("statement_number"))
            if value.startswith(f"YENE-FIN-{year}-"):
                try:
                    max_seq = max(max_seq, int(value.rsplit("-", 1)[-1]))
                except Exception:
                    pass
        return f"YENE-FIN-{year}-{max_seq + 1:04d}"

    def _list_statements():
        return _safe_select("finance_statements", {}, "*", 5000, "created_at", True)

    def _withdraw_rows():
        rows = _safe_select("agent_withdraw_requests", {}, "*", 5000, "created_at", True)
        if rows:
            return rows
        return _safe_select("withdrawal_requests", {}, "*", 5000, "created_at", True)

    def _region_setting_rows():
        return _safe_select("region_access_settings", {}, "*", 1000, "updated_at", True)

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
        pending = 0.0
        credits = 0.0
        debits = 0.0
        for r in rows:
            amount = _safe_float(r.get("amount"))
            entry_type = str(r.get("entry_type") or "").lower()
            signed = amount if entry_type == "credit" else -amount
            status = str(r.get("status") or "").lower()
            if status in {"pending", "hold", "requested"}:
                pending += signed
            elif entry_type == "credit":
                available += amount
                credits += amount
            else:
                available -= amount
                debits += amount
        return {
            "available": round(available, 2),
            "pending": round(pending, 2),
            "credits": round(credits, 2),
            "debits": round(debits, 2),
            "rows": rows[:50],
        }

    def _payment_rate(kind):
        best = 0.0
        for table_name in ("weekly_payment_settings", "payment_rules"):
            for r in _safe_select(table_name, {}, "*", 100):
                if kind == "driver":
                    best = max(best, _safe_float(r.get("driver_reg") or r.get("driver_register_amount") or r.get("driver_amount")))
                else:
                    best = max(best, _safe_float(r.get("client_reg") or r.get("client_register_amount") or r.get("client_amount")))
            if best > 0:
                break
        return best

    def _driver_rate():
        return _payment_rate("driver")

    def _client_rate():
        return _payment_rate("client")

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

    @app.get("/api/admin/agents")
    def admin_agents():
        rows = _agents()
        drivers = _all_rows("drivers")
        clients = _all_rows("clients")
        region = _clean(request.args.get("region"))
        town = _clean(request.args.get("town"))
        status = _clean(request.args.get("status")).upper()
        missing_profile = _clean(request.args.get("missing_profile")).lower() in {"1", "true", "yes"}
        no_registrations = _clean(request.args.get("no_registrations")).lower() in {"1", "true", "yes"}

        out = []
        for row in rows:
            values = _agent_identity_values(row)
            linked_driver_rows = [r for r in drivers if _matches_values(r, values, ("recruiter_agent_id", "agent_id", "recruiter_email", "recruiter_auth_id", "agent_auth_id", "created_by", "referral_code"))]
            linked_client_rows = [r for r in clients if _matches_values(r, values, ("recruiter_agent_id", "agent_id", "recruiter_email", "recruiter_auth_id", "agent_auth_id", "created_by", "referral_code"))]
            shaped = {
                "id": row.get("id"),
                "full_name": row.get("full_name") or row.get("username") or row.get("email"),
                "username": row.get("username"),
                "email": row.get("email"),
                "phone": row.get("phone") or row.get("phone_number"),
                "town": row.get("town") or row.get("current_working_town"),
                "region": row.get("region") or row.get("operation_region"),
                "current_working_town": row.get("current_working_town") or row.get("town"),
                "operation_region": row.get("operation_region") or row.get("region"),
                "status": row.get("status") or "PENDING",
                "referral_code": row.get("referral_code") or "",
                "created_at": row.get("created_at"),
                "region_locked": bool(row.get("region_locked")),
                "region_locked_at": row.get("region_locked_at"),
                "region_locked_by": row.get("region_locked_by"),
                "drivers": len(linked_driver_rows),
                "clients": len(linked_client_rows),
                "pending_approvals": len([
                    r for r in linked_driver_rows + linked_client_rows
                    if _clean(r.get("approval_status") or r.get("status")).upper() not in {"APPROVED", "ACTIVE", "VERIFIED", "ADMIN_APPROVED", "REJECTED"}
                ]),
            }
            if region and _clean(shaped["region"]).lower() != region.lower():
                continue
            if town and _clean(shaped["town"]).lower() != town.lower():
                continue
            if status and status not in _clean(shaped["status"]).upper():
                continue
            if missing_profile and not any(not _clean(row.get(key)) for key in ("full_name", "username", "phone", "town")):
                continue
            if no_registrations and (shaped["drivers"] or shaped["clients"]):
                continue
            out.append(shaped)
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
    def admin_approve_agent_actions():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400

        payload = {"status": "ACTIVE"}
        res = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        _safe_update("agents", {"id": agent_id}, payload)
        _audit_log("approve_agent", "agent_profile", agent_id, metadata=payload)
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
        _audit_log("agent_region_lock", "agent_profile", agent_id, metadata=payload)
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
        monday, sunday = _week_bounds()
        region = _clean(data.get("region") or "Namibia")
        town = _clean(data.get("town") or "All")
        payload = {
            "rule_name": data.get("rule_name") or f"{region} / {town}",
            "region": region,
            "town": town,
            "driver_reg": _safe_float(data.get("driver_reg") or data.get("driver_reward")),
            "driver_reward": _safe_float(data.get("driver_reward") or data.get("driver_reg")),
            "client_reg": _safe_float(data.get("client_reg") or data.get("client_reward")),
            "client_reward": _safe_float(data.get("client_reward") or data.get("client_reg")),
            "activation_bonus": _safe_float(data.get("activation_bonus") or data.get("weekly_30_activations_bonus")),
            "referral_pct": _safe_float(data.get("referral_pct")),
            "first_trip_bonus": _safe_float(data.get("first_trip_bonus")),
            "daily_5_drivers_bonus": _safe_float(data.get("daily_5_drivers_bonus")),
            "daily_5_clients_bonus": _safe_float(data.get("daily_5_clients_bonus")),
            "daily_driver_threshold": int(_safe_float(data.get("daily_driver_threshold"), 5)),
            "daily_client_threshold": int(_safe_float(data.get("daily_client_threshold"), 5)),
            "weekly_activation_threshold": int(_safe_float(data.get("weekly_activation_threshold"), 30)),
            "weekly_30_activations_bonus": _safe_float(data.get("weekly_30_activations_bonus") or data.get("activation_bonus")),
            "status": _clean(data.get("status") or "active") or "active",
            "effective_from": _clean(data.get("effective_from")),
            "effective_to": _clean(data.get("effective_to")),
            "week_start": _clean(data.get("week_start")) or monday.date().isoformat(),
            "week_end": _clean(data.get("week_end")) or sunday.date().isoformat(),
            "updated_at": _now_iso(),
        }

        rule_id = str(data.get("id") or "").strip()
        if not rule_id:
            current_rows = _safe_select("weekly_payment_settings", {}, "*", 500, "updated_at", True)
            for row in current_rows:
                if (
                    _clean(row.get("region")).lower() == region.lower()
                    and _clean(row.get("town") or "All").lower() == town.lower()
                    and _clean(row.get("week_start")) == payload["week_start"]
                    and _clean(row.get("week_end")) == payload["week_end"]
                ):
                    rule_id = _clean(row.get("id"))
                    break
        if rule_id:
            res = _safe_update("weekly_payment_settings", {"id": rule_id}, payload)
            if isinstance(res, Exception):
                res = _safe_update("payment_rules", {"id": rule_id}, payload)
        else:
            payload["id"] = str(uuid.uuid4())
            payload["created_at"] = _now_iso()
            res = _safe_insert("weekly_payment_settings", payload)
            if isinstance(res, Exception):
                res = _safe_insert("payment_rules", payload)

        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        _audit_log("payment_rule_upsert", "payment_rule", rule_id or payload["id"], metadata=payload)
        return jsonify({"ok": True, "message": "Payment rule saved", "row": payload})

    @app.get("/api/admin/region_settings")
    def admin_region_settings():
        return jsonify({"ok": True, "rows": _region_setting_rows()})

    @app.post("/api/admin/region_settings/upsert")
    def admin_region_settings_upsert():
        data = request.get_json(force=True) or {}
        region = _clean(data.get("region") or "Namibia")
        town = _clean(data.get("town") or "All")
        if not region:
            return jsonify({"ok": False, "error": "Region is required"}), 400
        payload = {
            "id": _clean(data.get("id")) or str(uuid.uuid4()),
            "region": region,
            "town": town or "All",
            "is_active": bool(data.get("is_active", True)),
            "registration_open": bool(data.get("registration_open", True)),
            "locked_by": _clean(data.get("locked_by")) or _admin_email(),
            "updated_by": _admin_email(),
            "updated_at": _now_iso(),
            "note": _clean(data.get("note")),
        }
        existing = pick_region_access_rule(_region_setting_rows(), region=region, town=town)
        if existing and _clean(existing.get("region")).lower() == region.lower() and _clean(existing.get("town") or "All").lower() == (town or "All").lower():
            res = _safe_update("region_access_settings", {"id": existing.get("id")}, payload)
            payload["id"] = existing.get("id")
        else:
            payload["created_at"] = _now_iso()
            res = _safe_insert("region_access_settings", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500
        _audit_log("region_access_upsert", "region_access_setting", payload["id"], metadata=payload)
        return jsonify({"ok": True, "message": "Region setting saved", "row": payload})

    @app.get("/api/admin/finance/summary")
    def admin_finance_summary():
        ledger = _all_rows("agent_wallet_ledger")
        statements = _list_statements()
        credits = 0.0
        debits = 0.0
        last_executed_date = None
        last_executed_by = ""
        for row in ledger:
            amount = _safe_float(row.get("amount"))
            if _clean(row.get("entry_type") or row.get("txn_type") or row.get("type")).lower() == "debit":
                debits += amount
            else:
                credits += amount
        for row in statements:
            executed_at = _clean(row.get("executed_at") or row.get("updated_at") or row.get("created_at"))
            if executed_at and (not last_executed_date or executed_at > last_executed_date):
                last_executed_date = executed_at
                last_executed_by = _clean(row.get("executed_by"))
        return jsonify({
            "ok": True,
            "summary": {
                "total_credits": round(credits, 2),
                "total_paid": round(debits, 2),
                "estimated_due": round(max(0.0, credits - debits), 2),
                "statements_generated": len(statements),
                "last_executed_date": last_executed_date,
                "executed_by": last_executed_by,
            }
        })

    @app.get("/api/admin/payout_workflow")
    def admin_payout_workflow():
        ledger = _all_rows("agent_wallet_ledger")
        by_agent = {}
        for row in ledger:
            agent_id = _clean(row.get("agent_id"))
            agent_email = _clean(row.get("agent_email"))
            key = agent_id or agent_email
            if not key:
                continue
            item = by_agent.setdefault(key, {
                "agent_id": agent_id,
                "email": agent_email,
                "agent": row.get("agent_name") or agent_email or "Agent",
                "total_bonus": 0.0,
                "total_paid": 0.0,
                "amount_due": 0.0,
            })
            amount = _safe_float(row.get("amount"))
            entry_type = _clean(row.get("entry_type") or row.get("txn_type") or row.get("type")).lower()
            if entry_type == "debit":
                item["total_paid"] += amount
                item["amount_due"] -= amount
            else:
                item["total_bonus"] += amount
                item["amount_due"] += amount
        rows = list(by_agent.values())
        rows.sort(key=lambda x: x["amount_due"], reverse=True)
        return jsonify({"ok": True, "rows": rows, "summary": {"count": len(rows), "scope": _clean(request.args.get("scope") or "weekly")}})

    @app.get("/api/admin/finance/statements")
    def admin_finance_statements():
        return jsonify({"ok": True, "rows": _list_statements()})

    @app.post("/api/admin/finance/execute_statement")
    def admin_finance_execute_statement():
        data = request.get_json(force=True) or {}
        agent_ids = data.get("agent_ids") or ([data.get("agent_id")] if data.get("agent_id") else [])
        agent_ids = [str(x).strip() for x in agent_ids if str(x).strip()]
        if not agent_ids:
            return jsonify({"ok": False, "error": "agent_id or agent_ids required"}), 400

        period_start = _clean(data.get("period_start") or data.get("week_start"))
        period_end = _clean(data.get("period_end") or data.get("week_end"))
        note = _clean(data.get("note"))
        status = _clean(data.get("status") or "paid") or "paid"
        rows = []
        for agent_id in agent_ids:
            agent = _agent_by_id(agent_id)
            if not agent:
                continue
            wallet = _wallet_balance_for(agent)
            amount = _safe_float(data.get("amount"))
            if amount <= 0:
                amount = wallet["balance"]
            if amount <= 0:
                continue
            statement_id = str(uuid.uuid4())
            statement_number = _statement_number()
            payload = {
                "id": statement_id,
                "statement_number": statement_number,
                "agent_id": agent.get("id"),
                "agent_email": agent.get("email"),
                "amount": amount,
                "period_start": period_start,
                "period_end": period_end,
                "status": status,
                "executed_by": _admin_email(),
                "executed_at": _now_iso(),
                "note": note,
                "created_at": _now_iso(),
            }
            res = _safe_insert_resilient("finance_statements", payload, ("period_start", "period_end", "note"))
            if isinstance(res, Exception):
                return jsonify({"ok": False, "error": str(res)}), 500

            ledger_payload = {
                "id": str(uuid.uuid4()),
                "agent_id": agent.get("id"),
                "agent_email": agent.get("email"),
                "agent_auth_id": agent.get("auth_id"),
                "entry_type": "debit",
                "status": "posted",
                "amount": amount,
                "description": note or "Payout statement executed",
                "reference": statement_number,
                "source_type": "payout_statement",
                "source_id": statement_id,
                "statement_number": statement_number,
                "approved_by": _admin_email(),
                "created_at": _now_iso(),
            }
            _safe_insert_resilient("agent_wallet_ledger", ledger_payload, ("source_type", "source_id", "statement_number", "approved_by"))
            _audit_log("execute_statement", "finance_statement", statement_id, amount=amount, metadata=payload)
            rows.append(payload)
        return jsonify({"ok": True, "rows": rows})

    @app.get("/api/admin/withdraw_requests")
    def admin_withdraw_requests():
        rows = _withdraw_rows()
        status = _clean(request.args.get("status")).lower()
        if status:
            rows = [r for r in rows if _clean(r.get("status")).lower() == status]
        return jsonify({"ok": True, "rows": rows})

    @app.post("/api/admin/withdraw_requests/respond")
    def admin_withdraw_requests_respond():
        data = request.get_json(force=True) or {}
        request_id = _clean(data.get("request_id") or data.get("id"))
        response_status = _clean(data.get("status")).lower()
        note = _clean(data.get("note") or data.get("admin_note"))
        if not request_id or response_status not in {"approved", "rejected"}:
            return jsonify({"ok": False, "error": "request_id and valid status required"}), 400

        table_name = "agent_withdraw_requests" if _safe_select("agent_withdraw_requests", {"id": request_id}, "id", 1) else "withdrawal_requests"
        rows = _safe_select(table_name, {"id": request_id}, "*", 1)
        if not rows:
            return jsonify({"ok": False, "error": "Withdrawal request not found"}), 404
        row = rows[0]
        current_status = _clean(row.get("status")).lower()
        if current_status == response_status:
            return jsonify({"ok": True, "message": "Withdrawal request already updated"})
        amount = _safe_float(row.get("amount") or row.get("request_amount"))
        agent = _find_recruiter_agent(row) or _find_agent_by_any(row.get("agent_id")) or _find_agent_by_any(row.get("agent_email"))
        update_payload = {
            "status": response_status,
            "admin_response": note,
            "responded_at": _now_iso(),
            "responded_by": _admin_email(),
        }
        res = _safe_update(table_name, {"id": request_id}, update_payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500
        statement = None
        if response_status == "approved" and agent and amount > 0:
            statement_id = str(uuid.uuid4())
            statement_number = _statement_number()
            statement = {
                "id": statement_id,
                "statement_number": statement_number,
                "agent_id": agent.get("id"),
                "agent_email": agent.get("email"),
                "amount": amount,
                "status": "paid",
                "executed_by": _admin_email(),
                "executed_at": _now_iso(),
                "note": note or "Withdrawal request approved",
                "created_at": _now_iso(),
            }
            _safe_insert_resilient("finance_statements", statement, ("note",))
            ledger_payload = {
                "id": str(uuid.uuid4()),
                "agent_id": agent.get("id"),
                "agent_email": agent.get("email"),
                "agent_auth_id": agent.get("auth_id"),
                "entry_type": "debit",
                "status": "posted",
                "amount": amount,
                "description": note or "Withdrawal request paid",
                "reference": statement_number,
                "source_type": "payout_statement",
                "source_id": statement_id,
                "statement_number": statement_number,
                "approved_by": _admin_email(),
                "created_at": _now_iso(),
            }
            _safe_insert_resilient("agent_wallet_ledger", ledger_payload, ("source_type", "source_id", "statement_number", "approved_by"))
        _audit_log("withdraw_request_response", "withdraw_request", request_id, amount=amount, metadata={"status": response_status, "note": note})
        return jsonify({"ok": True, "message": "Withdrawal request updated", "statement": statement})

    @app.post("/api/admin/agent_region_update")
    def admin_agent_region_update():
        data = request.get_json(force=True) or {}
        agent_id = _clean(data.get("agent_id"))
        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400
        payload = {
            "region": _clean(data.get("region")),
            "town": _clean(data.get("town")),
            "current_working_town": _clean(data.get("current_working_town") or data.get("town")),
            "operation_region": _clean(data.get("operation_region") or data.get("region")),
            "region_locked": bool(data.get("region_locked")),
            "region_locked_at": _now_iso() if data.get("region_locked") else None,
            "region_locked_by": _admin_email() if data.get("region_locked") else None,
        }
        payload = {k: v for k, v in payload.items() if v not in ("", None) or k == "region_locked"}
        res = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500
        _safe_update("agents", {"id": agent_id}, {k: v for k, v in payload.items() if k in {"region", "town", "operation_region", "current_working_town"}})
        _audit_log("agent_region_update", "agent_profile", agent_id, metadata=payload)
        if data.get("region_locked") is not None:
            _audit_log("agent_region_lock", "agent_profile", agent_id, metadata={"region_locked": bool(data.get("region_locked"))})
        return jsonify({"ok": True, "message": "Agent region updated"})
