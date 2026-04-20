from datetime import datetime, timedelta, timezone

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

    def _approved(row):
        return _norm_status((row or {}).get("status")) in {"ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED"}

    def _pending(row):
        return _norm_status((row or {}).get("status")) in {"PENDING", "PENDING_APPROVAL", "UNDER_REVIEW"}

    def _row_dt(row):
        raw = _clean((row or {}).get("created_at"))
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except Exception:
            return None

    def _current_week_bounds():
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=now.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return start, end

    def _in_current_week(row):
        dt = _row_dt(row)
        if not dt:
            return False
        start, end = _current_week_bounds()
        return start <= dt <= end

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
                "auth_id": existing.get("auth_id") or r.get("auth_id"),
                "user_id": existing.get("user_id") or r.get("user_id"),
                "status": existing.get("status") or _norm_status(r.get("status")),
                "full_name": existing.get("full_name") or _clean(r.get("full_name")) or _clean(r.get("username")) or _clean(r.get("email")),
                "username": existing.get("username") or r.get("username"),
                "email": existing.get("email") or _clean(r.get("email")),
                "phone": existing.get("phone") or _clean(r.get("phone")) or _clean(r.get("phone_number")),
                "town": existing.get("town") or _clean(r.get("town")),
                "region": existing.get("region") or _clean(r.get("region")) or _clean(r.get("operation_region")),
                "operation_region": existing.get("operation_region") or _clean(r.get("operation_region")) or _clean(r.get("region")),
                "drivers": existing.get("drivers") or 0,
                "clients": existing.get("clients") or 0,
                "drivers_week": existing.get("drivers_week") or 0,
                "clients_week": existing.get("clients_week") or 0,
                "approved_drivers": existing.get("approved_drivers") or 0,
                "approved_clients": existing.get("approved_clients") or 0,
                "pending_approvals": existing.get("pending_approvals") or 0,
                "created_at": existing.get("created_at") or r.get("created_at"),
                "team_leader_name": existing.get("team_leader_name") or _clean(r.get("team_leader_name")),
                "role": existing.get("role") or _clean(r.get("role")) or "AGENT",
                "referral_code": existing.get("referral_code") or _clean(r.get("referral_code")),
                "referred_by_code": existing.get("referred_by_code") or _clean(r.get("referred_by_code")),
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
            for identity_key in ("id", "auth_id", "user_id"):
                ident = _clean(r.get(identity_key))
                if ident:
                    by_id_lookup[ident] = key
            if _clean(r.get("email")):
                by_email_lookup[_clean(r.get("email")).lower()] = key
            if _clean(r.get("full_name")):
                by_name_lookup[_clean(r.get("full_name")).lower()] = key

        def find_agent_key(row):
            ids = [
                _clean(row.get("recruiter_agent_id")),
                _clean(row.get("agent_id")),
                _clean(row.get("recruiter_auth_id")),
                _clean(row.get("agent_auth_id")),
            ]
            recruiter_email = _clean(row.get("recruiter_email")).lower()
            recruiter_name = _clean(row.get("recruiter_name")) or _clean(row.get("agent_name")) or _clean(row.get("recruiter"))
            for ident in ids:
                if ident and ident in by_id_lookup:
                    return by_id_lookup[ident]
            if recruiter_email and recruiter_email in by_email_lookup:
                return by_email_lookup[recruiter_email]
            if recruiter_name and recruiter_name.lower() in by_name_lookup:
                return by_name_lookup[recruiter_name.lower()]
            return None

        def attach_to_agent(row, kind):
            found_key = find_agent_key(row)
            if found_key:
                merged[found_key][kind] = int(merged[found_key].get(kind) or 0) + 1
                if _in_current_week(row):
                    merged[found_key][f"{kind}_week"] = int(merged[found_key].get(f"{kind}_week") or 0) + 1
                if _approved(row):
                    approved_key = "approved_drivers" if kind == "drivers" else "approved_clients"
                    merged[found_key][approved_key] = int(merged[found_key].get(approved_key) or 0) + 1
                if _pending(row):
                    merged[found_key]["pending_approvals"] = int(merged[found_key].get("pending_approvals") or 0) + 1
                if not merged[found_key].get("town") and row.get("town"):
                    merged[found_key]["town"] = _clean(row.get("town"))
                if not merged[found_key].get("region") and row.get("region"):
                    merged[found_key]["region"] = _clean(row.get("region"))
            else:
                # fallback agent creation from recruiter info
                recruiter_id = _clean(row.get("recruiter_agent_id") or row.get("agent_id") or row.get("agent_auth_id"))
                recruiter_email = _clean(row.get("recruiter_email")).lower()
                recruiter_name = _clean(row.get("recruiter_name")) or _clean(row.get("agent_name")) or _clean(row.get("recruiter"))
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
                    source = "driver_recruiter_fallback" if kind == "drivers" else "client_recruiter_fallback"
                    upsert_agent(fallback, source)
                    new_key = _key_from_row(fallback)
                    if new_key in merged:
                        merged[new_key][kind] = int(merged[new_key].get(kind) or 0) + 1
                        if _in_current_week(row):
                            merged[new_key][f"{kind}_week"] = int(merged[new_key].get(f"{kind}_week") or 0) + 1
                        if _pending(row):
                            merged[new_key]["pending_approvals"] = int(merged[new_key].get("pending_approvals") or 0) + 1

        for d in drivers:
            attach_to_agent(d, "drivers")

        for c in clients:
            attach_to_agent(c, "clients")

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
