from datetime import datetime
import uuid
from flask import jsonify, request, session


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

    def _safe_update_resilient(table, filters, payload_attempts):
        last_error = None
        for payload in payload_attempts:
            if not payload:
                continue
            res = _safe_update(table, filters, payload)
            if not isinstance(res, Exception):
                return res
            last_error = res
        return last_error

    def _safe_insert(table, payload):
        try:
            return sb_admin.table(table).insert(payload).execute()
        except Exception as e:
            return e

    def _safe_float(value, default=0.0):
        try:
            return float(value or 0)
        except Exception:
            return default

    def _clean(value):
        return str(value or "").strip()

    def _now():
        return datetime.utcnow().isoformat() + "Z"

    def _admin_email(data=None):
        data = data or {}
        return str(
            data.get("approved_by")
            or session.get("email")
            or session.get("admin_email")
            or session.get("agent_email")
            or "admin"
        ).strip()

    def _all_agents():
        rows = _safe_select("agent_profiles", {}, "*", 10000)
        if rows:
            return rows
        return _safe_select("agents", {}, "*", 10000)

    def _agent_identity_values(agent):
        vals = []
        for key in ("id", "auth_id", "user_id", "email", "referral_code"):
            value = _clean((agent or {}).get(key))
            if value and value not in vals:
                vals.append(value)
                lower = value.lower()
                if lower not in vals:
                    vals.append(lower)
        return vals

    def _find_agent_by_any(value):
        probe = _clean(value)
        if not probe:
            return None
        probe_lower = probe.lower()
        for agent in _all_agents():
            values = _agent_identity_values(agent)
            if probe in values or probe_lower in {v.lower() for v in values}:
                return agent
        return None

    def _find_recruiter(row):
        for field in (
            "recruiter_agent_id",
            "agent_id",
            "recruiter_email",
            "created_by",
            "referral_code",
            "recruiter_auth_id",
            "agent_auth_id",
        ):
            agent = _find_agent_by_any((row or {}).get(field))
            if agent:
                return agent
        return None

    def _rule_active(rule):
        status = _clean(rule.get("status") or "active").lower()
        if status == "inactive":
            return False
        return True

    def _payment_rules():
        rows = _safe_select("weekly_payment_settings", {}, "*", 500)
        if rows:
            return rows
        return _safe_select("payment_rules", {}, "*", 500)

    def _pick_rule(row):
        region = _clean((row or {}).get("region"))
        town = _clean((row or {}).get("town"))
        rules = [r for r in _payment_rules() if _rule_active(r)]
        if not rules:
            return None
        rules.sort(
            key=lambda r: (
                3 if _clean(r.get("region")).lower() == region.lower() and _clean(r.get("town") or "All").lower() == town.lower()
                else 2 if _clean(r.get("region")).lower() == region.lower() and _clean(r.get("town") or "All").lower() == "all"
                else 1 if _clean(r.get("region") or "Namibia").lower() == "namibia" and _clean(r.get("town") or "All").lower() == "all"
                else 0,
                _clean(r.get("updated_at") or r.get("created_at")),
            ),
            reverse=True,
        )
        return rules[0]

    def _payment_amount(kind, row=None):
        rule = _pick_rule(row or {})
        if not rule:
            return 0.0, None
        if kind == "driver":
            amount = _safe_float(rule.get("driver_reward") or rule.get("driver_reg") or rule.get("driver_register_amount") or rule.get("driver_amount"))
        else:
            amount = _safe_float(rule.get("client_reward") or rule.get("client_reg") or rule.get("client_register_amount") or rule.get("client_amount"))
        return amount, rule

    def _safe_insert_resilient(payload):
        res = _safe_insert("agent_wallet_ledger", payload)
        if not isinstance(res, Exception):
            return res
        slim = dict(payload)
        for key in ("source_type", "source_id", "approved_by", "payment_rule_id", "statement_number"):
            slim.pop(key, None)
        return _safe_insert("agent_wallet_ledger", slim)

    def _audit_log(action, target_type, target_id, amount=None, metadata=None):
        payload = {
            "id": str(uuid.uuid4()),
            "action": action,
            "target_type": target_type,
            "target_id": _clean(target_id),
            "admin_email": _admin_email(),
            "amount": _safe_float(amount) if amount is not None else None,
            "created_at": _now(),
        }
        if metadata is not None:
            payload["metadata"] = metadata
        try:
            _safe_insert("admin_audit_log", payload)
        except Exception:
            pass

    def _credit_approval(table, row_id, kind):
        rows = _safe_select(table, {"id": row_id}, "*", 1)
        if not rows:
            return None
        row = rows[0]
        amount, rule = _payment_amount(kind, row)
        recruiter = _find_recruiter(row) or {}
        source_type = "driver_registration" if kind == "driver" else "client_registration"
        source_id = str(row_id or "")
        reference = f"{kind}-approval-{row_id}"
        existing = _safe_select("agent_wallet_ledger", {"source_type": source_type, "source_id": source_id}, "id", 1)
        if not existing:
            existing = _safe_select("agent_wallet_ledger", {"reference": reference}, "id", 1)
        if existing:
            return {"credited_amount": amount, "wallet_ledger_id": existing[0].get("id"), "duplicate": True}
        payload = {
            "id": f"{source_type}:{source_id}",
            "agent_id": str(recruiter.get("id") or row.get("recruiter_agent_id") or row.get("agent_id") or row.get("agent_auth_id") or ""),
            "agent_auth_id": str(recruiter.get("auth_id") or row.get("recruiter_auth_id") or row.get("agent_auth_id") or ""),
            "agent_email": recruiter.get("email") or row.get("recruiter_email") or "",
            "agent_name": recruiter.get("full_name") or recruiter.get("username") or row.get("recruiter_name") or "",
            "entry_type": "credit",
            "amount": amount,
            "reference": reference,
            "source_type": source_type,
            "source_id": source_id,
            "note": f"{kind.title()} registration approved",
            "description": f"{kind.title()} registration approved",
            "status": "posted",
            "approved_by": _admin_email(),
            "payment_rule_id": (rule or {}).get("id"),
            "created_at": _now(),
        }
        res = _safe_insert_resilient(payload)
        wallet_ledger_id = None
        if not isinstance(res, Exception):
            wallet_ledger_id = (getattr(res, "data", None) or [{}])[0].get("id") if getattr(res, "data", None) else payload["id"]
        return {"credited_amount": amount, "wallet_ledger_id": wallet_ledger_id, "payment_rule_id": (rule or {}).get("id")}

    def _approval_update_attempts(approved_by):
        return [
            {
                "status": "APPROVED",
                "approval_status": "approved",
                "approved_at": _now(),
                "approved_by": approved_by,
            },
            {
                "status": "APPROVED",
                "approval_status": "approved",
                "approved_at": _now(),
            },
            {
                "status": "APPROVED",
                "approval_status": "approved",
            },
            {"status": "APPROVED"},
        ]

    def _rejection_update_attempts(reason, rejected_by):
        return [
            {
                "status": "REJECTED",
                "approval_status": "rejected",
                "rejected_at": _now(),
                "rejected_by": rejected_by,
            },
            {
                "status": "REJECTED",
                "approval_status": "rejected",
                "rejected_at": _now(),
                "rejection_reason": reason,
            },
            {
                "status": "REJECTED",
                "approval_status": "rejected",
            },
            {"status": "REJECTED"},
        ]

    @app.post("/api/admin/approve_agent")
    def approve_agent_final():
        data = request.get_json(force=True) or {}
        agent_id = str(data.get("agent_id") or "").strip()
        if not agent_id:
            return jsonify({"ok": False, "error": "agent_id required"}), 400

        payload = {
            "status": "ACTIVE",
            "approved_at": _now(),
            "approval_status": "approved",
            "rejection_reason": None,
        }

        res1 = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res1, Exception):
            return jsonify({"ok": False, "error": str(res1)}), 500

        _safe_update("agents", {"id": agent_id}, payload)
        _audit_log("approve_agent", "agent_profile", agent_id, metadata=payload)
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
            "approval_status": "rejected",
            "rejected_at": _now(),
            "rejection_reason": reason,
        }

        res1 = _safe_update("agent_profiles", {"id": agent_id}, payload)
        if isinstance(res1, Exception):
            return jsonify({"ok": False, "error": str(res1)}), 500

        _safe_update("agents", {"id": agent_id}, payload)
        _audit_log("reject_agent", "agent_profile", agent_id, metadata={"reason": reason})
        return jsonify({"ok": True, "message": "Agent rejected successfully"})

    @app.post("/api/admin/approve_driver")
    def approve_driver_final():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Driver id required"}), 400
        approved_by = _admin_email(data)

        res = _safe_update_resilient("drivers", {"id": row_id}, _approval_update_attempts(approved_by))
        if isinstance(res, Exception):
            app.logger.exception("approve_driver_failed row_id=%s error=%s", row_id, res)
            return jsonify({"ok": False, "error": "Driver approval could not be saved. Please check the schema and try again."}), 500

        credit = _credit_approval("drivers", row_id, "driver") or {"credited_amount": 0.0, "wallet_ledger_id": None}
        _audit_log("approve_driver", "driver", row_id, amount=credit.get("credited_amount"), metadata={"approved_by": approved_by, **credit})
        rows = _safe_select("drivers", {"id": row_id}, "*", 1)
        return jsonify({"ok": True, "message": "Driver approved successfully", "approved_by": approved_by, "row": rows[0] if rows else None, **credit})

    @app.post("/api/admin/reject_driver")
    def reject_driver_final():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        reason = str(data.get("reason") or "").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Driver id required"}), 400
        if not reason:
            return jsonify({"ok": False, "error": "reason required"}), 400

        rejected_by = _admin_email(data)
        res = _safe_update_resilient("drivers", {"id": row_id}, _rejection_update_attempts(reason, rejected_by))
        if isinstance(res, Exception):
            app.logger.exception("reject_driver_failed row_id=%s error=%s", row_id, res)
            return jsonify({"ok": False, "error": "Driver rejection could not be saved. Please try again."}), 500

        _audit_log("reject_driver", "driver", row_id, metadata={"reason": reason, "rejected_by": rejected_by})
        rows = _safe_select("drivers", {"id": row_id}, "*", 1)
        return jsonify({"ok": True, "message": "Driver rejected successfully", "row": rows[0] if rows else None})

    @app.post("/api/admin/approve_client")
    def approve_client_final():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Client id required"}), 400
        approved_by = _admin_email(data)

        res = _safe_update_resilient("clients", {"id": row_id}, _approval_update_attempts(approved_by))
        if isinstance(res, Exception):
            app.logger.exception("approve_client_failed row_id=%s error=%s", row_id, res)
            return jsonify({"ok": False, "error": "Client approval could not be saved. Please check the schema and try again."}), 500

        credit = _credit_approval("clients", row_id, "client") or {"credited_amount": 0.0, "wallet_ledger_id": None}
        _audit_log("approve_client", "client", row_id, amount=credit.get("credited_amount"), metadata={"approved_by": approved_by, **credit})
        rows = _safe_select("clients", {"id": row_id}, "*", 1)
        return jsonify({"ok": True, "message": "Client approved successfully", "approved_by": approved_by, "row": rows[0] if rows else None, **credit})

    @app.post("/api/admin/reject_client")
    def reject_client_final():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        reason = str(data.get("reason") or "").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Client id required"}), 400
        if not reason:
            return jsonify({"ok": False, "error": "reason required"}), 400

        rejected_by = _admin_email(data)
        res = _safe_update_resilient("clients", {"id": row_id}, _rejection_update_attempts(reason, rejected_by))
        if isinstance(res, Exception):
            app.logger.exception("reject_client_failed row_id=%s error=%s", row_id, res)
            return jsonify({"ok": False, "error": "Client rejection could not be saved. Please try again."}), 500

        _audit_log("reject_client", "client", row_id, metadata={"reason": reason, "rejected_by": rejected_by})
        rows = _safe_select("clients", {"id": row_id}, "*", 1)
        return jsonify({"ok": True, "message": "Client rejected successfully", "row": rows[0] if rows else None})

    @app.get("/api/admin/pending_agents")
    def pending_agents():
        rows = _safe_select("agent_profiles", {}, "*", 10000)
        out = []
        for r in rows:
            status = str(r.get("status") or "").upper()
            if status in ("ACTIVE", "APPROVED"):
                continue
            auth_id = r.get("auth_id") or r.get("user_id")
            out.append({
                "id": r.get("id"),
                "full_name": r.get("full_name") or r.get("username") or r.get("email"),
                "email": r.get("email"),
                "phone": r.get("phone") or r.get("phone_number"),
                "town": r.get("town"),
                "region": r.get("region"),
                "status": r.get("status") or "PENDING",
                "created_at": r.get("created_at"),
                "auth_linked": bool(auth_id),
                "must_change_password": bool(r.get("must_change_password")),
                "temp_password": r.get("temp_password"),
                "last_reset_at": r.get("last_reset_at"),
                "reset_by_admin": r.get("reset_by_admin"),
            })
        return jsonify({"ok": True, "rows": out})

    @app.get("/api/admin/pending_drivers")
    def pending_drivers():
        rows = _safe_select("drivers", {}, "*", 10000)
        pending = []
        for row in rows:
            status = str(row.get("approval_status") or row.get("status") or "").strip().upper()
            if status in ("APPROVED", "ACTIVE", "VERIFIED", "ADMIN_APPROVED", "REJECTED"):
                continue
            pending.append(row)
        return jsonify({"ok": True, "rows": pending})

    @app.get("/api/admin/pending_clients")
    def pending_clients():
        rows = _safe_select("clients", {}, "*", 10000)
        pending = []
        for row in rows:
            status = str(row.get("approval_status") or row.get("status") or "").strip().upper()
            if status in ("APPROVED", "ACTIVE", "VERIFIED", "ADMIN_APPROVED", "REJECTED"):
                continue
            pending.append(row)
        return jsonify({"ok": True, "rows": pending})
