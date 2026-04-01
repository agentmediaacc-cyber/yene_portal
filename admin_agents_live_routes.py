from flask import jsonify


def register_admin_agents_live_routes(app, sb_admin):
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
        return s if s else "PENDING"

    def _key_from_row(r):
        email = _clean(r.get("email")).lower()
        row_id = _clean(r.get("id"))
        name = _clean(r.get("full_name")) or _clean(r.get("username"))
        if email:
            return f"email:{email}"
        if row_id:
            return f"id:{row_id}"
        if name:
            return f"name:{name.lower()}"
        return ""

    @app.get("/api/admin/agents")
    @app.get("/api/admin/agents_safe")
    @app.get("/api/admin/list_agents")
    def admin_agents_live():
        agent_profiles = _safe_select("agent_profiles", {}, "*", 10000)
        agents_table = _safe_select("agents", {}, "*", 10000)
        drivers = _safe_select("drivers", {}, "*", 10000)
        clients = _safe_select("clients", {}, "*", 10000)

        merged = {}

        def upsert_agent(r, source="unknown"):
            key = _key_from_row(r)
            if not key:
                return

            existing = merged.get(key, {})
            merged[key] = {
                "id": existing.get("id") or r.get("id"),
                "status": existing.get("status") or _norm_status(r.get("status")),
                "full_name": existing.get("full_name") or _clean(r.get("full_name")) or _clean(r.get("username")) or _clean(r.get("email")),
                "username": existing.get("username") or r.get("username"),
                "email": existing.get("email") or _clean(r.get("email")),
                "phone": existing.get("phone") or _clean(r.get("phone")) or _clean(r.get("phone_number")),
                "town": existing.get("town") or _clean(r.get("town")),
                "region": existing.get("region") or _clean(r.get("region")),
                "drivers": existing.get("drivers") or 0,
                "clients": existing.get("clients") or 0,
                "created_at": existing.get("created_at") or r.get("created_at"),
                "team_leader_name": existing.get("team_leader_name") or _clean(r.get("team_leader_name")),
                "role": existing.get("role") or _clean(r.get("role")) or "AGENT",
                "referral_code": existing.get("referral_code") or _clean(r.get("referral_code")),
                "source": source,
            }

        # Primary sources
        for r in agent_profiles:
            upsert_agent(r, "agent_profiles")

        for r in agents_table:
            upsert_agent(r, "agents")

        # Count recruiter performance by recruiter_agent_id first
        by_id_lookup = {}
        by_email_lookup = {}
        by_name_lookup = {}

        for key, r in merged.items():
            if _clean(r.get("id")):
                by_id_lookup[_clean(r.get("id"))] = key
            if _clean(r.get("email")):
                by_email_lookup[_clean(r.get("email")).lower()] = key
            if _clean(r.get("full_name")):
                by_name_lookup[_clean(r.get("full_name")).lower()] = key

        def attach_driver_to_agent(row):
            recruiter_id = _clean(row.get("recruiter_agent_id"))
            recruiter_email = _clean(row.get("recruiter_email")).lower()
            recruiter_name = _clean(row.get("recruiter_name")) or _clean(row.get("agent_name")) or _clean(row.get("recruiter"))

            found_key = None
            if recruiter_id and recruiter_id in by_id_lookup:
                found_key = by_id_lookup[recruiter_id]
            elif recruiter_email and recruiter_email in by_email_lookup:
                found_key = by_email_lookup[recruiter_email]
            elif recruiter_name and recruiter_name.lower() in by_name_lookup:
                found_key = by_name_lookup[recruiter_name.lower()]

            if found_key:
                merged[found_key]["drivers"] = int(merged[found_key].get("drivers") or 0) + 1
                if not merged[found_key].get("town") and row.get("town"):
                    merged[found_key]["town"] = _clean(row.get("town"))
                if not merged[found_key].get("region") and row.get("region"):
                    merged[found_key]["region"] = _clean(row.get("region"))
            else:
                # fallback agent creation from recruiter info
                if recruiter_name or recruiter_email:
                    fallback = {
                        "id": recruiter_id or "",
                        "full_name": recruiter_name or recruiter_email,
                        "email": recruiter_email,
                        "phone": _clean(row.get("recruiter_phone")),
                        "town": _clean(row.get("town")),
                        "region": _clean(row.get("region")),
                        "status": "ACTIVE",
                    }
                    upsert_agent(fallback, "driver_recruiter_fallback")
                    new_key = _key_from_row(fallback)
                    if new_key in merged:
                        merged[new_key]["drivers"] = int(merged[new_key].get("drivers") or 0) + 1

        def attach_client_to_agent(row):
            recruiter_id = _clean(row.get("recruiter_agent_id"))
            recruiter_email = _clean(row.get("recruiter_email")).lower()
            recruiter_name = _clean(row.get("recruiter_name")) or _clean(row.get("agent_name")) or _clean(row.get("recruiter"))

            found_key = None
            if recruiter_id and recruiter_id in by_id_lookup:
                found_key = by_id_lookup[recruiter_id]
            elif recruiter_email and recruiter_email in by_email_lookup:
                found_key = by_email_lookup[recruiter_email]
            elif recruiter_name and recruiter_name.lower() in by_name_lookup:
                found_key = by_name_lookup[recruiter_name.lower()]

            if found_key:
                merged[found_key]["clients"] = int(merged[found_key].get("clients") or 0) + 1
                if not merged[found_key].get("town") and row.get("town"):
                    merged[found_key]["town"] = _clean(row.get("town"))
                if not merged[found_key].get("region") and row.get("region"):
                    merged[found_key]["region"] = _clean(row.get("region"))
            else:
                if recruiter_name or recruiter_email:
                    fallback = {
                        "id": recruiter_id or "",
                        "full_name": recruiter_name or recruiter_email,
                        "email": recruiter_email,
                        "phone": _clean(row.get("recruiter_phone")),
                        "town": _clean(row.get("town")),
                        "region": _clean(row.get("region")),
                        "status": "ACTIVE",
                    }
                    upsert_agent(fallback, "client_recruiter_fallback")
                    new_key = _key_from_row(fallback)
                    if new_key in merged:
                        merged[new_key]["clients"] = int(merged[new_key].get("clients") or 0) + 1

        for d in drivers:
            attach_driver_to_agent(d)

        for c in clients:
            attach_client_to_agent(c)

        rows = list(merged.values())

        # remove obvious admin-only records
        rows = [
            r for r in rows
            if "ADMIN" not in _clean(r.get("role")).upper()
        ]

        rows.sort(key=lambda x: (_clean(x.get("full_name")).lower(), _clean(x.get("email")).lower()))

        return jsonify({
            "ok": True,
            "rows": rows,
            "count": len(rows),
        })
