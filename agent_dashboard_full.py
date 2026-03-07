from datetime import datetime, timedelta, timezone
from flask import jsonify, request, session

def register_agent_dashboard_routes(app, sb_admin, require_login, log_system_event=None):
    UTC = timezone.utc

    def safe_log(action, message):
        try:
            if log_system_event:
                log_system_event(action, message)
        except Exception:
            pass

    def now_utc():
        return datetime.now(UTC)

    def week_range():
        now = now_utc()
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return monday, sunday

    def iso(dt):
        return dt.astimezone(UTC).isoformat()

    def get_agent_profile():
        email = session.get("email")
        if not email:
            return None, "Missing agent session email"

        try:
            res = (
                sb_admin.table("agent_profiles")
                .select("*")
                .eq("email", email)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            if not rows:
                return None, "Agent profile not found"
            return rows[0], None
        except Exception as e:
            return None, str(e)

    def first_row(table, select="*"):
        try:
            res = sb_admin.table(table).select(select).limit(1).execute()
            rows = res.data or []
            return rows[0] if rows else None
        except Exception:
            return None

    def get_rate_settings():
        """
        Tries multiple places for admin-configured rates.
        Edit this function only if your table names differ.
        """
        defaults = {
            "client_register_amount": 10,
            "client_activate_amount": 10,
            "driver_register_amount": 10,
            "driver_activate_amount": 10,
        }

        row = first_row("agent_rate_settings")
        if row:
            return {
                "client_register_amount": float(row.get("client_register_amount", 10) or 10),
                "client_activate_amount": float(row.get("client_activate_amount", 10) or 10),
                "driver_register_amount": float(row.get("driver_register_amount", 10) or 10),
                "driver_activate_amount": float(row.get("driver_activate_amount", 10) or 10),
            }

        row = first_row("admin_settings")
        if row:
            return {
                "client_register_amount": float(row.get("client_register_amount", 10) or 10),
                "client_activate_amount": float(row.get("client_activate_amount", 10) or 10),
                "driver_register_amount": float(row.get("driver_register_amount", 10) or 10),
                "driver_activate_amount": float(row.get("driver_activate_amount", 10) or 10),
            }

        return defaults

    def get_wallet_balance(agent_profile):
        agent_id = agent_profile.get("id")
        if not agent_id:
            return 0.0

        # 1) direct wallet table
        try:
            res = (
                sb_admin.table("agent_wallets")
                .select("*")
                .eq("agent_id", agent_id)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            if rows:
                row = rows[0]
                return float(row.get("balance", 0) or 0)
        except Exception:
            pass

        # 2) agent profile balance field
        try:
            return float(agent_profile.get("wallet_balance", 0) or 0)
        except Exception:
            pass

        # 3) ledger sum fallback
        try:
            res = (
                sb_admin.table("agent_wallet_ledger")
                .select("amount")
                .eq("agent_id", agent_id)
                .execute()
            )
            rows = res.data or []
            total = 0.0
            for r in rows:
                total += float(r.get("amount", 0) or 0)
            return total
        except Exception:
            return 0.0

    def query_rows(table, filters=None, select="*"):
        filters = filters or []
        q = sb_admin.table(table).select(select)
        for kind, a, b in filters:
            if kind == "eq":
                q = q.eq(a, b)
            elif kind == "gte":
                q = q.gte(a, b)
            elif kind == "lte":
                q = q.lte(a, b)
            elif kind == "order":
                q = q.order(a, desc=bool(b))
            elif kind == "limit":
                q = q.limit(a)
        return q.execute().data or []

    def count_weekly_clients(agent_profile, monday, sunday):
        agent_id = agent_profile.get("id")
        auth_id = agent_profile.get("auth_id")
        email = agent_profile.get("email")

        filters_base = [
            ("gte", "created_at", iso(monday)),
            ("lte", "created_at", iso(sunday)),
        ]

        attempts = [
            [("eq", "recruiter_agent_id", agent_id)] + filters_base,
            [("eq", "agent_id", agent_id)] + filters_base,
            [("eq", "created_by_agent_id", agent_id)] + filters_base,
            [("eq", "recruiter_email", email)] + filters_base,
            [("eq", "created_by_auth_id", auth_id)] + filters_base,
        ]

        for filters in attempts:
            try:
                rows = query_rows("clients", filters)
                return rows
            except Exception:
                continue
        return []

    def count_weekly_drivers(agent_profile, monday, sunday):
        agent_id = agent_profile.get("id")
        auth_id = agent_profile.get("auth_id")
        email = agent_profile.get("email")

        filters_base = [
            ("gte", "created_at", iso(monday)),
            ("lte", "created_at", iso(sunday)),
        ]

        attempts = [
            [("eq", "recruiter_agent_id", agent_id)] + filters_base,
            [("eq", "agent_id", agent_id)] + filters_base,
            [("eq", "created_by_agent_id", agent_id)] + filters_base,
            [("eq", "recruiter_email", email)] + filters_base,
            [("eq", "created_by_auth_id", auth_id)] + filters_base,
        ]

        for filters in attempts:
            try:
                rows = query_rows("drivers", filters)
                return rows
            except Exception:
                continue
        return []

    def list_activity(agent_profile, monday, sunday):
        items = []

        for r in count_weekly_clients(agent_profile, monday, sunday):
            items.append({
                "subject_type": "client",
                "full_name": r.get("full_name") or r.get("name") or "",
                "phone": r.get("phone") or r.get("phone_number") or "",
                "town": r.get("town") or r.get("region") or "",
                "created_at": r.get("created_at"),
            })

        for r in count_weekly_drivers(agent_profile, monday, sunday):
            items.append({
                "subject_type": "driver",
                "full_name": r.get("full_name") or r.get("name") or "",
                "phone": r.get("phone") or r.get("phone_number") or "",
                "town": r.get("town") or r.get("region") or "",
                "created_at": r.get("created_at"),
            })

        def sort_key(x):
            return x.get("created_at") or ""

        items.sort(key=sort_key, reverse=True)
        return items[:100]

    def try_team_trip_counts(agent_profile):
        """
        Best-effort support for ride/trip progress.
        If your trip table uses different names, update only this function.
        """
        agent_id = agent_profile.get("id")
        counters = {
            "team_driver_trips": 0,
            "team_rider_trips": 0,
            "drivers_with_5_trips": 0,
        }

        for table_name in ["trips", "rides"]:
            try:
                rows = (
                    sb_admin.table(table_name)
                    .select("*")
                    .eq("recruiter_agent_id", agent_id)
                    .execute()
                    .data or []
                )
                counters["team_driver_trips"] = len(rows)
                counters["team_rider_trips"] = len(rows)

                per_driver = {}
                for r in rows:
                    did = r.get("driver_id") or r.get("driver_uuid") or r.get("driver_phone")
                    if did:
                        per_driver[did] = per_driver.get(did, 0) + 1
                counters["drivers_with_5_trips"] = sum(1 for _, n in per_driver.items() if n >= 5)
                return counters
            except Exception:
                continue

        return counters

    @app.route("/api/agent/summary_v3", methods=["GET"], endpoint="agent_summary_v3_full")
    @require_login("AGENT")
    def agent_summary_v3_full():
        agent, err = get_agent_profile()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        monday, sunday = week_range()
        rates = get_rate_settings()
        weekly_clients = count_weekly_clients(agent, monday, sunday)
        weekly_drivers = count_weekly_drivers(agent, monday, sunday)
        trip_stats = try_team_trip_counts(agent)

        clients_week = len(weekly_clients)
        drivers_week = len(weekly_drivers)

        earnings_week = (
            clients_week * float(rates["client_register_amount"]) +
            drivers_week * float(rates["driver_register_amount"])
        )

        starter_progress = clients_week
        gold_progress = min(trip_stats["team_driver_trips"], trip_stats["team_rider_trips"])
        platinum_progress = max(trip_stats["team_driver_trips"], trip_stats["drivers_with_5_trips"])

        return jsonify({
            "ok": True,
            "week_start": monday.date().isoformat(),
            "week_end": sunday.date().isoformat(),
            "clients_week": clients_week,
            "drivers_week": drivers_week,
            "earnings_week": round(earnings_week, 2),
            "starter_progress": starter_progress,
            "gold_progress": gold_progress,
            "platinum_progress": platinum_progress,
            "rates": rates,
        })

    @app.route("/api/agent/wallet_v3", methods=["GET"], endpoint="agent_wallet_v3_full")
    @require_login("AGENT")
    def agent_wallet_v3_full():
        agent, err = get_agent_profile()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        return jsonify({
            "ok": True,
            "balance": round(get_wallet_balance(agent), 2)
        })

    @app.route("/api/agent/activity_v3", methods=["GET"], endpoint="agent_activity_v3_full")
    @require_login("AGENT")
    def agent_activity_v3_full():
        agent, err = get_agent_profile()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        monday, sunday = week_range()
        rows = list_activity(agent, monday, sunday)
        return jsonify({"ok": True, "rows": rows})

    @app.route("/api/agent/register_client_v3", methods=["POST"], endpoint="agent_register_client_v3_full")
    @require_login("AGENT")
    def agent_register_client_v3_full():
        agent, err = get_agent_profile()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        data = request.get_json(silent=True) or {}
        full_name = (data.get("full_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        town = (data.get("town") or "").strip()

        if not full_name or not phone:
            return jsonify({"ok": False, "error": "Full name and phone are required"}), 400

        try:
            dup = sb_admin.table("clients").select("id").eq("phone", phone).limit(1).execute()
            if dup.data:
                return jsonify({"ok": False, "error": "Phone number already exists"}), 400
        except Exception:
            pass

        payload = {
            "full_name": full_name,
            "phone": phone,
            "phone_number": phone,
            "town": town,
            "status": "pending_approval",
            "recruiter_agent_id": agent.get("id"),
            "recruiter_name": agent.get("full_name") or agent.get("email"),
            "recruiter_email": agent.get("email"),
            "created_by_agent_id": agent.get("id"),
            "created_by_auth_id": agent.get("auth_id"),
        }

        try:
            sb_admin.table("clients").insert(payload).execute()
            safe_log("REGISTER_CLIENT", f"Agent {agent.get('email')} registered client {full_name}")
            return jsonify({"ok": True, "success": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/agent/register_driver_v3", methods=["POST"], endpoint="agent_register_driver_v3_full")
    @require_login("AGENT")
    def agent_register_driver_v3_full():
        agent, err = get_agent_profile()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        data = request.get_json(silent=True) or {}
        full_name = (data.get("full_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        town = (data.get("town") or "").strip()
        email = (data.get("email") or "").strip()

        if not full_name or not phone or not town:
            return jsonify({"ok": False, "error": "Full name, phone, and town are required"}), 400

        try:
            dup = sb_admin.table("drivers").select("id").eq("phone", phone).limit(1).execute()
            if dup.data:
                return jsonify({"ok": False, "error": "Driver phone number already exists"}), 400
        except Exception:
            pass

        payload = {
            "full_name": full_name,
            "phone": phone,
            "phone_number": phone,
            "email": email,
            "town": town,
            "region": town,
            "status": "pending_approval",
            "recruiter_agent_id": agent.get("id"),
            "recruiter_name": agent.get("full_name") or agent.get("email"),
            "recruiter_email": agent.get("email"),
            "created_by_agent_id": agent.get("id"),
            "created_by_auth_id": agent.get("auth_id"),
        }

        try:
            sb_admin.table("drivers").insert(payload).execute()
            safe_log("REGISTER_DRIVER", f"Agent {agent.get('email')} registered driver {full_name}")
            return jsonify({"ok": True, "success": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
