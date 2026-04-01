from flask import jsonify


def register_admin_agents_master_routes(app, sb_admin):
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

    def _clean(v):
        return str(v or "").strip()

    def _norm_status(v):
        s = _clean(v).upper()
        if not s:
            return "PENDING"
        return s

    @app.get("/api/admin/agents_master")
    def admin_agents_master():
        profiles = _safe_select("agent_profiles", {}, "*", 10000)
        agents_tbl = _safe_select("agents", {}, "*", 10000)
        drivers = _safe_select("drivers", {}, "*", 10000)
        clients = _safe_select("clients", {}, "*", 10000)
        due_rows = _safe_select("agent_wallet_ledger", {}, "*", 10000)

        merged = {}

        def upsert_agent(row, source="unknown"):
            email = _clean(row.get("email")).lower()
            row_id = _clean(row.get("id"))
            name = _clean(row.get("full_name")) or _clean(row.get("username")) or _clean(row.get("agent")) or email
            if not (email or row_id or name):
                return

            key = email or row_id or name.lower()

            existing = merged.get(key, {})
            merged[key] = {
                "id": existing.get("id") or row.get("id"),
                "full_name": existing.get("full_name") or name,
                "username": existing.get("username") or row.get("username"),
                "email": existing.get("email") or row.get("email"),
                "phone": existing.get("phone") or row.get("phone") or row.get("phone_number"),
                "town": existing.get("town") or row.get("town"),
                "region": existing.get("region") or row.get("region"),
                "status": existing.get("status") or _norm_status(row.get("status")),
                "team_leader_name": existing.get("team_leader_name") or row.get("team_leader_name") or "",
                "role": existing.get("role") or row.get("role") or "AGENT",
                "referral_code": existing.get("referral_code") or row.get("referral_code") or "",
                "created_at": existing.get("created_at") or row.get("created_at"),
                "source": existing.get("source") or source,
            }

        for r in profiles:
            upsert_agent(r, "agent_profiles")

        for r in agents_tbl:
            upsert_agent(r, "agents")

        # Build fallback agents from recruiter names in drivers
        for r in drivers:
            recruiter_name = _clean(r.get("recruiter_name")) or _clean(r.get("agent_name")) or _clean(r.get("recruiter"))
            recruiter_email = _clean(r.get("recruiter_email")).lower()
            recruiter_phone = _clean(r.get("recruiter_phone"))
            if recruiter_name or recruiter_email:
                upsert_agent({
                    "full_name": recruiter_name,
                    "email": recruiter_email,
                    "phone": recruiter_phone,
                    "town": r.get("town"),
                    "region": r.get("region"),
                    "status": "ACTIVE" if _norm_status(r.get("status")) in ("APPROVED", "ACTIVE") else "PENDING",
                }, "drivers_recruiter_fallback")

        # Build fallback agents from recruiter names in clients
        for r in clients:
            recruiter_name = _clean(r.get("recruiter_name")) or _clean(r.get("agent_name")) or _clean(r.get("recruiter"))
            recruiter_email = _clean(r.get("recruiter_email")).lower()
            recruiter_phone = _clean(r.get("recruiter_phone"))
            if recruiter_name or recruiter_email:
                upsert_agent({
                    "full_name": recruiter_name,
                    "email": recruiter_email,
                    "phone": recruiter_phone,
                    "town": r.get("town"),
                    "region": r.get("region"),
                    "status": "ACTIVE" if _norm_status(r.get("status")) in ("APPROVED", "ACTIVE") else "PENDING",
                }, "clients_recruiter_fallback")

        # Improve status using payout/due rows if agent_email exists there
        by_email = {}
        for key, row in merged.items():
            email = _clean(row.get("email")).lower()
            if email:
                by_email[email] = key

        for d in due_rows:
            agent_email = _clean(d.get("agent_email")).lower()
            if agent_email and agent_email in by_email:
                merged[by_email[agent_email]]["status"] = merged[by_email[agent_email]].get("status") or "ACTIVE"

        rows = list(merged.values())

        # remove obvious admin records from the list
        rows = [
            r for r in rows
            if "admin" not in _clean(r.get("role")).lower()
            and not _clean(r.get("email")).lower().startswith("admin")
            and _clean(r.get("full_name")).lower() != "admin"
        ]

        rows.sort(key=lambda x: (_clean(x.get("full_name")).lower(), _clean(x.get("email")).lower()))

        return jsonify({
            "ok": True,
            "count": len(rows),
            "rows": rows
        })
