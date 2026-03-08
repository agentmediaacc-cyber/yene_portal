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

    def day_range():
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1, seconds=-1)
        return start, end

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

    def driver_rows(agent_id, start=None, end=None):
        q = sb_admin.table("drivers").select("*").eq("recruiter_agent_id", agent_id)
        if start:
            q = q.gte("created_at", iso(start))
        if end:
            q = q.lte("created_at", iso(end))
        try:
            return q.order("created_at", desc=True).limit(5000).execute().data or []
        except Exception:
            return []

    def client_rows(agent_id, start=None, end=None):
        q = sb_admin.table("clients").select("*").eq("recruiter_agent_id", agent_id)
        if start:
            q = q.gte("created_at", iso(start))
        if end:
            q = q.lte("created_at", iso(end))
        try:
            return q.order("created_at", desc=True).limit(5000).execute().data or []
        except Exception:
            return []

    def activity(agent, period="week"):
        aid = agent.get("id")
        if period == "all":
            drows = driver_rows(aid)
            crows = client_rows(aid)
        elif period == "day":
            ds, de = day_range()
            drows = driver_rows(aid, ds, de)
            crows = client_rows(aid, ds, de)
        else:
            ws, we = week_range()
            drows = driver_rows(aid, ws, we)
            crows = client_rows(aid, ws, we)

        rows = []
        for r in crows:
            rows.append({
                "subject_type": "client",
                "full_name": r.get("full_name") or "",
                "phone": r.get("phone") or r.get("phone_number") or "",
                "town": r.get("town") or "",
                "external_code": r.get("external_code") or r.get("yene_code") or "",
                "created_at": r.get("created_at"),
                "status": r.get("status") or "",
            })
        for r in drows:
            rows.append({
                "subject_type": "driver",
                "full_name": r.get("full_name") or "",
                "phone": r.get("phone") or r.get("phone_number") or "",
                "town": r.get("town") or "",
                "external_code": r.get("external_code") or "",
                "created_at": r.get("created_at"),
                "status": r.get("status") or "",
            })

        rows.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return rows

    def team_agents(agent):
        aid = agent.get("id")
        rows = []
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
            for r in refs:
                child_id = r.get("child_agent_id")
                ds, de = day_range()
                ws, we = week_range()

                d_day = len(driver_rows(child_id, ds, de)) if child_id else 0
                c_day = len(client_rows(child_id, ds, de)) if child_id else 0
                d_week = len(driver_rows(child_id, ws, we)) if child_id else 0
                c_week = len(client_rows(child_id, ws, we)) if child_id else 0
                d_all = len(driver_rows(child_id)) if child_id else 0
                c_all = len(client_rows(child_id)) if child_id else 0

                rows.append({
                    "child_agent_id": child_id,
                    "full_name": r.get("child_agent_name") or "",
                    "email": r.get("child_agent_email") or "",
                    "joined_at": r.get("created_at"),
                    "drivers_day": d_day,
                    "clients_day": c_day,
                    "drivers_week": d_week,
                    "clients_week": c_week,
                    "drivers_all": d_all,
                    "clients_all": c_all,
                })
        except Exception:
            pass
        return rows

    @app.route("/join")
    def join_agent_team():
        ref = (request.args.get("ref") or "").strip()
        if ref:
            session["agent_ref"] = ref
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
                "profile_picture_url": agent.get("profile_picture_url"),
                "residential_address": agent.get("residential_address"),
                "operation_region": agent.get("operation_region"),
                "pin": agent.get("pin"),
            }
        })

    @app.route("/api/agent/summary_v4", methods=["GET"], endpoint="agent_summary_v4")
    @require_login("AGENT")
    def agent_summary_v4():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        ws, we = week_range()
        rates = get_rates()

        wk_d = driver_rows(agent.get("id"), ws, we)
        wk_c = client_rows(agent.get("id"), ws, we)
        all_d = driver_rows(agent.get("id"))
        all_c = client_rows(agent.get("id"))
        team = team_agents(agent)

        earnings_week = (
            len(wk_c) * safe_float(rates["client_register_amount"], 10) +
            len(wk_d) * safe_float(rates["driver_register_amount"], 10)
        )

        return jsonify({
            "ok": True,
            "week_start": ws.date().isoformat(),
            "week_end": we.date().isoformat(),
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
        if period not in {"day", "week", "all"}:
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

        return jsonify({
            "ok": True,
            "rows": team_agents(agent),
        })

    @app.route("/api/agent/settings_v4", methods=["POST"], endpoint="agent_settings_v4")
    @require_login("AGENT")
    def agent_settings_v4():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        data = request.get_json(silent=True) or {}
        updates = {
            "full_name": (data.get("full_name") or "").strip() or agent.get("full_name"),
            "phone": (data.get("phone") or "").strip(),
            "email": (data.get("email") or "").strip() or agent.get("email"),
            "profile_picture_url": (data.get("profile_picture_url") or "").strip(),
            "residential_address": (data.get("residential_address") or "").strip(),
            "operation_region": (data.get("operation_region") or "").strip(),
            "pin": (data.get("pin") or "").strip(),
        }

        try:
            sb_admin.table("agent_profiles").update(updates).eq("id", agent.get("id")).execute()
            session["email"] = updates["email"]
            return jsonify({"ok": True, "success": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

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
        license_number = (data.get("license_number") or "").strip()
        external_code = (data.get("external_code") or "").strip()

        if not full_name or not phone or not license_number:
            return jsonify({"ok": False, "error": "Full name, phone and license number are required"}), 400

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
            "license_number": license_number,
            "status": "pending_approval",
            "recruiter_agent_id": agent.get("id"),
            "recruiter_name": agent.get("full_name") or agent.get("email"),
        }

        # only include external_code if the table supports it
        if external_code:
            payload["external_code"] = external_code

        try:
            sb_admin.table("drivers").insert(payload).execute()
            safe_log("REGISTER_DRIVER", f"Agent {agent.get('email')} registered driver {full_name}")
            return jsonify({"ok": True, "success": True})
        except Exception as e:
            # retry without external_code if schema does not support it
            if "external_code" in str(e):
                payload.pop("external_code", None)
                try:
                    sb_admin.table("drivers").insert(payload).execute()
                    safe_log("REGISTER_DRIVER", f"Agent {agent.get('email')} registered driver {full_name}")
                    return jsonify({"ok": True, "success": True})
                except Exception as e2:
                    return jsonify({"ok": False, "error": str(e2)}), 500
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
            "yene_code": external_code or "PENDING",
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
