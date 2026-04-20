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

    def _safe_update_any(table, filters, payloads):
        last_error = None
        for payload in payloads:
            res = _safe_update(table, filters, payload)
            if not isinstance(res, Exception):
                return res
            last_error = res
        return last_error or Exception("No update payloads attempted")

    def _safe_insert_any(table, payloads):
        last_error = None
        for payload in payloads:
            res = _safe_insert(table, payload)
            if not isinstance(res, Exception):
                return res
            last_error = res
        return last_error or Exception("No insert payloads attempted")

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

    def _payment_amount(kind):
        def active_row(rows):
            for row in rows:
                status = str(row.get("status") or row.get("state") or "Active").strip().lower()
                active = row.get("is_active")
                if status not in {"inactive", "disabled", "off", "false"} and active is not False:
                    return row
            return rows[0] if rows else {}

        rows = _safe_select("weekly_payment_settings", {}, "*", 50, "updated_at", True)
        if not rows:
            rows = _safe_select("payment_rules", {}, "*", 50, "updated_at", True)
        row = active_row(rows)
        if kind == "driver":
            return _safe_float(row.get("driver_reg") or row.get("driver_register_amount") or row.get("driver_amount"))
        return _safe_float(row.get("client_reg") or row.get("client_register_amount") or row.get("client_amount"))

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

    def _agent_for_registration(row):
        candidates = [
            row.get("recruiter_agent_id"),
            row.get("agent_id"),
            row.get("agent_auth_id"),
            row.get("recruiter_auth_id"),
        ]
        for value in candidates:
            if value:
                agent = _agent_by_id(str(value))
                if agent:
                    return agent
                rows = _safe_select("agent_profiles", {"auth_id": value}, "*", 1)
                if rows:
                    return rows[0]
                rows = _safe_select("agent_profiles", {"user_id": value}, "*", 1)
                if rows:
                    return rows[0]
        email = str(row.get("recruiter_email") or row.get("agent_email") or "").strip().lower()
        if email:
            rows = _safe_select("agent_profiles", {"email": email}, "*", 1)
            if rows:
                return rows[0]
            rows = _safe_select("agents", {"email": email}, "*", 1)
            if rows:
                return rows[0]
        return None

    def _notify_agent(agent, subject, message, registration_type="", registration_id=""):
        if not agent:
            return
        payload = {
            "agent_id": str(agent.get("id") or ""),
            "agent_auth_id": str(agent.get("auth_id") or agent.get("user_id") or ""),
            "agent_email": agent.get("email") or "",
            "agent_name": agent.get("full_name") or agent.get("username") or agent.get("email") or "Agent",
            "registration_type": registration_type,
            "registration_id": str(registration_id or ""),
            "subject": subject,
            "message": message,
            "status": "unread",
            "created_at": _now_iso(),
        }
        res = _safe_insert("agent_messages", payload)
        if isinstance(res, Exception):
            _safe_insert("agent_notifications", {
                "agent_id": payload["agent_id"],
                "agent_email": payload["agent_email"],
                "title": subject,
                "body": message,
                "status": "unread",
                "created_at": payload["created_at"],
            })

    def _credit_approval(table, row_id, kind):
        rows = _safe_select(table, {"id": row_id}, "*", 1)
        if not rows:
            return {"credited": False, "amount": 0.0, "reason": "row_not_found"}
        row = rows[0]
        agent = _agent_for_registration(row)
        amount = _payment_amount(kind)
        if amount <= 0:
            _notify_agent(
                agent,
                f"{kind.title()} approved",
                f"Your {kind} registration was approved. No wallet amount was added because the active admin rate is zero.",
                kind,
                row_id,
            )
            return {"credited": False, "amount": 0.0, "reason": "zero_rate"}

        reference = f"{kind}-approval-{row_id}"
        existing = _safe_select("agent_wallet_ledger", {"reference": reference}, "id", 1)
        if existing:
            return {"credited": False, "amount": amount, "reason": "duplicate_reference"}

        agent_id = str((agent or {}).get("id") or row.get("recruiter_agent_id") or row.get("agent_id") or "")
        payload = {
            "agent_id": agent_id,
            "agent_auth_id": str((agent or {}).get("auth_id") or (agent or {}).get("user_id") or row.get("agent_auth_id") or ""),
            "agent_email": (agent or {}).get("email") or row.get("recruiter_email") or "",
            "agent_name": (agent or {}).get("full_name") or (agent or {}).get("username") or row.get("recruiter_name") or "",
            "entry_type": "credit",
            "amount": amount,
            "reference": reference,
            "source_type": f"{kind}_approval",
            "source_id": str(row_id),
            "note": f"{kind.title()} registration approved by admin",
            "description": f"{kind.title()} registration approved by admin",
            "status": "approved",
            "created_at": _now_iso(),
        }
        slim = {
            "agent_id": payload["agent_id"],
            "agent_email": payload["agent_email"],
            "entry_type": "credit",
            "amount": amount,
            "reference": reference,
            "note": payload["note"],
            "status": "approved",
            "created_at": payload["created_at"],
        }
        source_slim = {
            "agent_id": payload["agent_id"],
            "amount": amount,
            "entry_type": "credit",
            "source_type": payload["source_type"],
            "source_id": payload["source_id"],
            "description": payload["description"],
            "status": "approved",
            "created_at": payload["created_at"],
        }
        res = _safe_insert_any("agent_wallet_ledger", [payload, slim, source_slim])
        if isinstance(res, Exception):
            return {"credited": False, "amount": amount, "reason": str(res)}

        _notify_agent(
            agent,
            f"{kind.title()} approved and wallet credited",
            f"Admin approved your {kind} registration. N$ {amount:,.2f} was added to your wallet.",
            kind,
            row_id,
        )
        return {"credited": True, "amount": amount, "reason": ""}

    def _approval_payloads():
        now = _now_iso()
        return [
            {
                "status": "APPROVED",
                "approval_state": "APPROVED",
                "approved_at": now,
                "rejection_reason": None,
                "admin_approved": True,
            },
            {"status": "APPROVED", "approved_at": now, "admin_approved": True},
            {"status": "APPROVED", "admin_approved": True},
            {"status": "APPROVED"},
        ]

    def _rejection_payloads(reason):
        now = _now_iso()
        return [
            {
                "status": "REJECTED",
                "approval_state": "REJECTED",
                "rejected_at": now,
                "rejection_reason": reason,
                "admin_approved": False,
            },
            {"status": "REJECTED", "rejected_at": now, "rejection_reason": reason, "admin_approved": False},
            {"status": "REJECTED", "admin_approved": False},
            {"status": "REJECTED"},
        ]

    def _approve_registration(table, row_id, kind):
        rows = _safe_select(table, {"id": row_id}, "*", 1)
        if not rows:
            return {"id": row_id, "ok": False, "error": f"{kind.title()} not found"}
        res = _safe_update_any(table, {"id": row_id}, _approval_payloads())
        if isinstance(res, Exception):
            return {"id": row_id, "ok": False, "error": str(res)}
        credit = _credit_approval(table, row_id, kind)
        return {"id": row_id, "ok": True, "status": "APPROVED", "wallet": credit}

    def _reject_registration(table, row_id, kind, reason):
        rows = _safe_select(table, {"id": row_id}, "*", 1)
        if not rows:
            return {"id": row_id, "ok": False, "error": f"{kind.title()} not found"}
        agent = _agent_for_registration(rows[0])
        res = _safe_update_any(table, {"id": row_id}, _rejection_payloads(reason))
        if isinstance(res, Exception):
            return {"id": row_id, "ok": False, "error": str(res)}
        _notify_agent(agent, f"{kind.title()} rejected", f"Admin rejected your {kind} registration. Reason: {reason}", kind, row_id)
        return {"id": row_id, "ok": True, "status": "REJECTED"}

    def _bulk_response(results):
        succeeded = [r for r in results if r.get("ok")]
        failed = [r for r in results if not r.get("ok")]
        processed = len(results)
        return jsonify({
            "ok": not failed,
            "processed": processed,
            "succeeded": len(succeeded),
            "failed": len(failed),
            "count": len(succeeded),
            "percent_complete": 100 if processed else 0,
            "results": results,
            "errors": failed,
        }), (207 if failed and succeeded else (400 if failed else 200))

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
        result = _approve_registration("drivers", row_id, "driver")
        return jsonify({"ok": result["ok"], "message": "Driver approved" if result["ok"] else result.get("error"), **result}), (200 if result["ok"] else 404)

    @app.post("/api/admin/approve_client")
    def admin_approve_client():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Client id required"}), 400
        result = _approve_registration("clients", row_id, "client")
        return jsonify({"ok": result["ok"], "message": "Client approved" if result["ok"] else result.get("error"), **result}), (200 if result["ok"] else 404)

    @app.post("/api/admin/reject_driver")
    def admin_reject_driver_compat():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        reason = str(data.get("reason") or "Admin rejected").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Driver id required"}), 400
        result = _reject_registration("drivers", row_id, "driver", reason)
        return jsonify({"ok": result["ok"], "message": "Driver rejected" if result["ok"] else result.get("error"), **result}), (200 if result["ok"] else 404)

    @app.post("/api/admin/reject_client")
    def admin_reject_client_compat():
        data = request.get_json(force=True) or {}
        row_id = str(data.get("id") or "").strip()
        reason = str(data.get("reason") or "Admin rejected").strip()
        if not row_id:
            return jsonify({"ok": False, "error": "Client id required"}), 400
        result = _reject_registration("clients", row_id, "client", reason)
        return jsonify({"ok": result["ok"], "message": "Client rejected" if result["ok"] else result.get("error"), **result}), (200 if result["ok"] else 404)

    def _bulk_ids():
        data = request.get_json(silent=True) or {}
        ids = data.get("ids") or []
        if isinstance(ids, str):
            ids = [x.strip() for x in ids.split(",")]
        return [str(x).strip() for x in ids if str(x or "").strip()], data

    def _bulk_approve(table, kind):
        ids, _ = _bulk_ids()
        return _bulk_response([_approve_registration(table, row_id, kind) for row_id in ids])

    def _bulk_reject(table, kind):
        ids, data = _bulk_ids()
        reason = str(data.get("reason") or "Admin rejected").strip()
        return _bulk_response([_reject_registration(table, row_id, kind, reason) for row_id in ids])

    @app.post("/api/admin/bulk_approve_drivers")
    def admin_bulk_approve_drivers():
        return _bulk_approve("drivers", "driver")

    @app.post("/api/admin/bulk_reject_drivers")
    def admin_bulk_reject_drivers():
        return _bulk_reject("drivers", "driver")

    @app.post("/api/admin/bulk_approve_clients")
    def admin_bulk_approve_clients():
        return _bulk_approve("clients", "client")

    @app.post("/api/admin/bulk_reject_clients")
    def admin_bulk_reject_clients():
        return _bulk_reject("clients", "client")

    @app.post("/api/admin/bulk_approve_agents")
    def admin_bulk_approve_agents():
        ids, _ = _bulk_ids()
        results = []
        for agent_id in ids:
            payload = {"status": "ACTIVE", "approval_state": "APPROVED", "approved_at": _now_iso(), "rejection_reason": None}
            res = _safe_update("agent_profiles", {"id": agent_id}, payload)
            _safe_update("agents", {"id": agent_id}, payload)
            results.append({"id": agent_id, "ok": not isinstance(res, Exception), "error": str(res) if isinstance(res, Exception) else ""})
        return jsonify({"ok": True, "count": len([r for r in results if r.get("ok")]), "results": results})

    @app.post("/api/admin/bulk_reject_agents")
    def admin_bulk_reject_agents():
        ids, data = _bulk_ids()
        reason = str(data.get("reason") or "Admin rejected").strip()
        results = []
        for agent_id in ids:
            payload = {"status": "REJECTED", "approval_state": "REJECTED", "rejected_at": _now_iso(), "rejection_reason": reason}
            res = _safe_update("agent_profiles", {"id": agent_id}, payload)
            _safe_update("agents", {"id": agent_id}, payload)
            results.append({"id": agent_id, "ok": not isinstance(res, Exception), "error": str(res) if isinstance(res, Exception) else ""})
        return jsonify({"ok": True, "count": len([r for r in results if r.get("ok")]), "results": results})

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
