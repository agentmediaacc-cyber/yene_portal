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

    def safe_float(v, default=0.0):
        try:
            return float(v or 0)
        except Exception:
            return default

    def get_agent_profile():
        email = session.get("email")
        if not email:
            return None, "Missing session email"

        try:
            rows = (
                sb_admin.table("agent_profiles")
                .select("*")
                .eq("email", email)
                .limit(1)
                .execute()
                .data or []
            )
            if not rows:
                return None, f"Agent profile not found for {email}"
            return rows[0], None
        except Exception as e:
            return None, str(e)

    def get_rate_settings():
        defaults = {
            "client_register_amount": 10,
            "client_activate_amount": 10,
            "driver_register_amount": 10,
            "driver_activate_amount": 10,
        }

        for table_name in ["agent_rate_settings", "admin_settings", "settings"]:
            try:
                rows = sb_admin.table(table_name).select("*").limit(1).execute().data or []
                if rows:
                    row = rows[0]
                    return {
                        "client_register_amount": safe_float(row.get("client_register_amount", 10), 10),
                        "client_activate_amount": safe_float(row.get("client_activate_amount", 10), 10),
                        "driver_register_amount": safe_float(row.get("driver_register_amount", 10), 10),
                        "driver_activate_amount": safe_float(row.get("driver_activate_amount", 10), 10),
                    }
            except Exception:
                continue

        return defaults

    def get_wallet_balance(agent):
        aid = agent.get("id")
        try:
            rows = (
                sb_admin.table("agent_wallets")
                .select("*")
                .eq("agent_id", aid)
                .limit(1)
                .execute()
                .data or []
            )
            if rows:
                return safe_float(rows[0].get("balance", 0), 0)
        except Exception:
            pass

        try:
            return safe_float(agent.get("wallet_balance", 0), 0)
        except Exception:
            return 0.0

    def weekly_clients(agent, monday, sunday):
        aid = agent.get("id")
        try:
            return (
                sb_admin.table("clients")
                .select("*")
                .eq("recruiter_agent_id", aid)
                .gte("created_at", iso(monday))
                .lte("created_at", iso(sunday))
                .order("created_at", desc=True)
                .execute()
                .data or []
            )
        except Exception:
            return []

    def weekly_drivers(agent, monday, sunday):
        aid = agent.get("id")
        try:
            return (
                sb_admin.table("drivers")
                .select("*")
                .eq("recruiter_agent_id", aid)
                .gte("created_at", iso(monday))
                .lte("created_at", iso(sunday))
                .order("created_at", desc=True)
                .execute()
                .data or []
            )
        except Exception:
            return []

    def all_clients(agent):
        aid = agent.get("id")
        try:
            return (
                sb_admin.table("clients")
                .select("*")
                .eq("recruiter_agent_id", aid)
                .order("created_at", desc=True)
                .limit(5000)
                .execute()
                .data or []
            )
        except Exception:
            return []

    def all_drivers(agent):
        aid = agent.get("id")
        try:
            return (
                sb_admin.table("drivers")
                .select("*")
                .eq("recruiter_agent_id", aid)
                .order("created_at", desc=True)
                .limit(5000)
                .execute()
                .data or []
            )
        except Exception:
            return []

    def activity_rows(agent, period="week"):
        monday, sunday = week_range()
        if period == "all":
            client_rows = all_clients(agent)
            driver_rows = all_drivers(agent)
        else:
            client_rows = weekly_clients(agent, monday, sunday)
            driver_rows = weekly_drivers(agent, monday, sunday)

        rows = []
        for r in client_rows:
            rows.append({
                "subject_type": "client",
                "full_name": r.get("full_name") or "",
                "phone": r.get("phone") or r.get("phone_number") or "",
                "town": r.get("town") or "",
                "created_at": r.get("created_at"),
                "status": r.get("status") or "",
            })

        for r in driver_rows:
            rows.append({
                "subject_type": "driver",
                "full_name": r.get("full_name") or "",
                "phone": r.get("phone") or r.get("phone_number") or "",
                "town": r.get("town") or "",
                "created_at": r.get("created_at"),
                "status": r.get("status") or "",
            })

        rows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return rows

    def get_trip_stats(agent):
        aid = agent.get("id")
        stats = {
            "team_driver_trips": 0,
            "team_rider_trips": 0,
            "drivers_with_5_trips": 0,
        }

        for table_name in ["trips", "rides"]:
            try:
                rows = (
                    sb_admin.table(table_name)
                    .select("*")
                    .eq("recruiter_agent_id", aid)
                    .execute()
                    .data or []
                )

                if not rows:
                    continue

                stats["team_driver_trips"] = len(rows)
                stats["team_rider_trips"] = len(rows)

                per_driver = {}
                for r in rows:
                    did = r.get("driver_id") or r.get("driver_uuid") or r.get("driver_phone") or r.get("driver")
                    if did:
                        per_driver[did] = per_driver.get(did, 0) + 1

                stats["drivers_with_5_trips"] = sum(1 for _, n in per_driver.items() if n >= 5)
                return stats
            except Exception:
                continue

        return stats

    @app.route("/api/agent/profile_v3", methods=["GET"], endpoint="agent_profile_v3_full")
    @require_login("AGENT")
    def agent_profile_v3_full():
        agent, err = get_agent_profile()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        return jsonify({
            "ok": True,
            "profile": {
                "id": agent.get("id"),
                "full_name": agent.get("full_name"),
                "email": agent.get("email"),
                "phone": agent.get("phone"),
                "town": agent.get("town"),
                "username": agent.get("username"),
                "wallet_balance": safe_float(agent.get("wallet_balance", 0), 0),
            }
        })

    @app.route("/api/agent/summary_v3", methods=["GET"], endpoint="agent_summary_v3_full")
    @require_login("AGENT")
    def agent_summary_v3_full():
        agent, err = get_agent_profile()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        monday, sunday = week_range()
        rates = get_rate_settings()

        wk_c = weekly_clients(agent, monday, sunday)
        wk_d = weekly_drivers(agent, monday, sunday)
        all_c = all_clients(agent)
        all_d = all_drivers(agent)
        trip_stats = get_trip_stats(agent)

        clients_week = len(wk_c)
        drivers_week = len(wk_d)
        clients_all = len(all_c)
        drivers_all = len(all_d)

        earnings_week = (
            clients_week * safe_float(rates["client_register_amount"], 10) +
            drivers_week * safe_float(rates["driver_register_amount"], 10)
        )

        return jsonify({
            "ok": True,
            "week_start": monday.date().isoformat(),
            "week_end": sunday.date().isoformat(),
            "clients_week": clients_week,
            "drivers_week": drivers_week,
            "clients_all": clients_all,
            "drivers_all": drivers_all,
            "earnings_week": round(earnings_week, 2),
            "starter_progress": clients_week,
            "gold_progress": min(trip_stats["team_driver_trips"], 50),
            "platinum_progress": min(max(trip_stats["team_driver_trips"], trip_stats["drivers_with_5_trips"]), 100),
            "rates": rates,
            "matched_agent": {
                "id": agent.get("id"),
                "full_name": agent.get("full_name"),
                "email": agent.get("email"),
            }
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

        period = (request.args.get("period") or "week").strip().lower()
        if period not in {"week", "all"}:
            period = "week"

        return jsonify({
            "ok": True,
            "period": period,
            "rows": activity_rows(agent, period)
        })

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
            dup = sb_admin.table("clients").select("id").eq("phone_number", phone).execute().data or []
            if not dup:
                dup = sb_admin.table("clients").select("id").eq("phone", phone).execute().data or []
            if dup:
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
            dup = sb_admin.table("drivers").select("id").eq("phone_number", phone).execute().data or []
            if not dup:
                dup = sb_admin.table("drivers").select("id").eq("phone", phone).execute().data or []
            if dup:
                return jsonify({"ok": False, "error": "Driver phone number already exists"}), 400
        except Exception:
            pass

        payload = {
            "full_name": full_name,
            "phone": phone,
            "phone_number": phone,
            "email": email,
            "town": town,
            "status": "pending_approval",
            "recruiter_agent_id": agent.get("id"),
            "recruiter_name": agent.get("full_name") or agent.get("email"),
        }

        try:
            sb_admin.table("drivers").insert(payload).execute()
            safe_log("REGISTER_DRIVER", f"Agent {agent.get('email')} registered driver {full_name}")
            return jsonify({"ok": True, "success": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

def register_agent_dashboard_debug_routes(app, sb_admin, require_login):
    @app.route("/api/agent/debug_link_v3", methods=["GET"], endpoint="agent_debug_link_v3_full")
    @require_login("AGENT")
    def agent_debug_link_v3_full():
        email = session.get("email")
        out = {
            "ok": True,
            "session_email": email,
            "agent_profile_found": False,
            "agent_profile": None,
            "drivers_count_by_recruiter_agent_id": 0,
            "clients_count_by_recruiter_agent_id": 0,
            "sample_drivers": [],
            "sample_clients": [],
        }

        if not email:
            out["ok"] = False
            out["error"] = "No session email"
            return jsonify(out), 401

        try:
            agent_rows = (
                sb_admin.table("agent_profiles")
                .select("*")
                .eq("email", email)
                .limit(1)
                .execute()
                .data or []
            )
            if agent_rows:
                agent = agent_rows[0]
                out["agent_profile_found"] = True
                out["agent_profile"] = {
                    "id": agent.get("id"),
                    "full_name": agent.get("full_name"),
                    "email": agent.get("email"),
                    "phone": agent.get("phone"),
                    "town": agent.get("town"),
                    "username": agent.get("username"),
                    "status": agent.get("status"),
                }

                aid = agent.get("id")

                try:
                    d = (
                        sb_admin.table("drivers")
                        .select("*")
                        .eq("recruiter_agent_id", aid)
                        .order("created_at", desc=True)
                        .limit(5)
                        .execute()
                        .data or []
                    )
                    out["drivers_count_by_recruiter_agent_id"] = len(
                        sb_admin.table("drivers")
                        .select("id", count="exact")
                        .eq("recruiter_agent_id", aid)
                        .execute()
                        .data or []
                    )
                    out["sample_drivers"] = d
                except Exception as e:
                    out["drivers_error"] = str(e)

                try:
                    c = (
                        sb_admin.table("clients")
                        .select("*")
                        .eq("recruiter_agent_id", aid)
                        .order("created_at", desc=True)
                        .limit(5)
                        .execute()
                        .data or []
                    )
                    out["clients_count_by_recruiter_agent_id"] = len(
                        sb_admin.table("clients")
                        .select("id", count="exact")
                        .eq("recruiter_agent_id", aid)
                        .execute()
                        .data or []
                    )
                    out["sample_clients"] = c
                except Exception as e:
                    out["clients_error"] = str(e)
            else:
                out["error"] = f"No agent profile found for email: {email}"

        except Exception as e:
            out["ok"] = False
            out["error"] = str(e)

        return jsonify(out)
