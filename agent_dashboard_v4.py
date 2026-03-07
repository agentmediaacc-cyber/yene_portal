from datetime import datetime, timedelta, timezone
from flask import jsonify, request, session, redirect

def register_agent_dashboard_v4_routes(app, sb_admin, require_login, log_system_event=None):
    UTC = timezone.utc

    def safe_log(action, message):
        try:
            if log_system_event:
                log_system_event(action, message)
        except Exception:
            pass

    def safe_float(v, default=0.0):
        try:
            return float(v or 0)
        except Exception:
            return default

    def week_range():
        now = datetime.now(UTC)
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)
        return monday, sunday

    def iso(dt):
        return dt.astimezone(UTC).isoformat()

    def get_agent():
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

    def get_rates():
        defaults = {
            "client_register_amount": 10,
            "driver_register_amount": 10,
        }
        for table_name in ["agent_rate_settings", "admin_settings", "settings"]:
            try:
                rows = sb_admin.table(table_name).select("*").limit(1).execute().data or []
                if rows:
                    row = rows[0]
                    return {
                        "client_register_amount": safe_float(row.get("client_register_amount", 10), 10),
                        "driver_register_amount": safe_float(row.get("driver_register_amount", 10), 10),
                    }
            except Exception:
                continue
        return defaults

    def get_wallet(agent):
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
        return safe_float(agent.get("wallet_balance", 0), 0)

    def drivers_week(agent, monday, sunday):
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

    def clients_week(agent, monday, sunday):
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

    def drivers_all(agent):
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

    def clients_all(agent):
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

    def activity(agent, period="week"):
        monday, sunday = week_range()
        drows = drivers_week(agent, monday, sunday) if period == "week" else drivers_all(agent)
        crows = clients_week(agent, monday, sunday) if period == "week" else clients_all(agent)

        rows = []
        for r in crows:
            rows.append({
                "subject_type": "client",
                "full_name": r.get("full_name") or "",
                "phone": r.get("phone") or r.get("phone_number") or "",
                "town": r.get("town") or "",
                "created_at": r.get("created_at"),
                "status": r.get("status") or "",
            })
        for r in drows:
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

    def team_agents(agent):
        aid = agent.get("id")
        email = agent.get("email")
        agents = []

        # Preferred: agent_referrals table
        try:
            refs = (
                sb_admin.table("agent_referrals")
                .select("*")
                .eq("parent_agent_id", aid)
                .order("created_at", desc=True)
                .limit(5000)
                .execute()
                .data or []
            )
            if refs:
                for r in refs:
                    agents.append({
                        "full_name": r.get("child_agent_name") or "",
                        "email": r.get("child_agent_email") or "",
                        "joined_at": r.get("created_at"),
                    })
                return agents
        except Exception:
            pass

        # Fallback if agent_profiles already has parent fields
        for col, value in [("parent_agent_id", aid), ("parent_agent_email", email)]:
            if not value:
                continue
            try:
                rows = (
                    sb_admin.table("agent_profiles")
                    .select("*")
                    .eq(col, value)
                    .order("created_at", desc=True)
                    .limit(5000)
                    .execute()
                    .data or []
                )
                if rows:
                    for r in rows:
                        agents.append({
                            "full_name": r.get("full_name") or "",
                            "email": r.get("email") or "",
                            "joined_at": r.get("created_at"),
                        })
                    return agents
            except Exception:
                pass

        return agents

    @app.route("/join")
    def join_agent_team():
        ref = (request.args.get("ref") or "").strip()
        if ref:
            session["agent_ref"] = ref
        # change this if your signup page URL is different
        return redirect("/register")

    @app.route("/api/agent/me_v4", methods=["GET"], endpoint="agent_me_v4")
    @require_login("AGENT")
    def agent_me_v4():
        agent, err = get_agent()
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
            }
        })

    @app.route("/api/agent/summary_v4", methods=["GET"], endpoint="agent_summary_v4")
    @require_login("AGENT")
    def agent_summary_v4():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        monday, sunday = week_range()
        rates = get_rates()

        wk_d = drivers_week(agent, monday, sunday)
        wk_c = clients_week(agent, monday, sunday)
        all_d = drivers_all(agent)
        all_c = clients_all(agent)
        team = team_agents(agent)

        earnings_week = (
            len(wk_c) * safe_float(rates["client_register_amount"], 10) +
            len(wk_d) * safe_float(rates["driver_register_amount"], 10)
        )

        return jsonify({
            "ok": True,
            "week_start": monday.date().isoformat(),
            "week_end": sunday.date().isoformat(),
            "drivers_week": len(wk_d),
            "clients_week": len(wk_c),
            "drivers_all": len(all_d),
            "clients_all": len(all_c),
            "earnings_week": round(earnings_week, 2),
            "wallet_balance": round(get_wallet(agent), 2),
            "team_agents_count": len(team),
            "referral_link": f"/join?ref={agent.get('email') or ''}",
            "matched_agent": {
                "id": agent.get("id"),
                "full_name": agent.get("full_name"),
                "email": agent.get("email"),
            }
        })

    @app.route("/api/agent/activity_v4", methods=["GET"], endpoint="agent_activity_v4")
    @require_login("AGENT")
    def agent_activity_v4():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        period = (request.args.get("period") or "week").strip().lower()
        if period not in {"week", "all"}:
            period = "week"

        return jsonify({
            "ok": True,
            "rows": activity(agent, period),
        })

    @app.route("/api/agent/team_v4", methods=["GET"], endpoint="agent_team_v4")
    @require_login("AGENT")
    def agent_team_v4():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        rows = team_agents(agent)
        return jsonify({
            "ok": True,
            "rows": rows,
        })

    @app.route("/api/agent/register_driver_v4", methods=["POST"], endpoint="agent_register_driver_v4")
    @require_login("AGENT")
    def agent_register_driver_v4():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        data = request.get_json(silent=True) or {}
        full_name = (data.get("full_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        town = (data.get("town") or "").strip()
        email = (data.get("email") or "").strip()
        external_code = (data.get("external_code") or "").strip()

        if not full_name or not phone or not town:
            return jsonify({"ok": False, "error": "Full name, phone and town are required"}), 400

        try:
            dup = sb_admin.table("drivers").select("id").eq("phone_number", phone).execute().data or []
            if not dup:
                dup = sb_admin.table("drivers").select("id").eq("phone", phone).execute().data or []
            if dup:
                return jsonify({"ok": False, "error": "Driver phone already exists"}), 400
        except Exception:
            pass

        payload = {
            "full_name": full_name,
            "phone": phone,
            "phone_number": phone,
            "town": town,
            "external_code": external_code,
            "status": "pending_approval",
            "recruiter_agent_id": agent.get("id"),
            "recruiter_name": agent.get("full_name") or agent.get("email"),
            "external_code": external_code,
        }

        try:
            sb_admin.table("drivers").insert(payload).execute()
            safe_log("REGISTER_DRIVER", f"Agent {agent.get('email')} registered driver {full_name}")
            return jsonify({"ok": True, "success": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/agent/register_client_v4", methods=["POST"], endpoint="agent_register_client_v4")
    @require_login("AGENT")
    def agent_register_client_v4():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        data = request.get_json(silent=True) or {}
        full_name = (data.get("full_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        town = (data.get("town") or "").strip()
        external_code = (data.get("external_code") or "").strip()

        if not full_name or not phone:
            return jsonify({"ok": False, "error": "Full name and phone are required"}), 400

        try:
            dup = sb_admin.table("clients").select("id").eq("phone_number", phone).execute().data or []
            if not dup:
                dup = sb_admin.table("clients").select("id").eq("phone", phone).execute().data or []
            if dup:
                return jsonify({"ok": False, "error": "Client phone already exists"}), 400
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
