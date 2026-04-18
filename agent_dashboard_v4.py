import time
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

    def debug(route, identity=None, **counts):
        try:
            app.logger.info(
                "agent_data route=%s identity=%s counts=%s",
                route,
                identity or {},
                counts,
            )
        except Exception:
            pass

    def _is_transient_error(exc):
        text = str(exc).lower()
        return (
            "resource temporarily unavailable" in text
            or "errno 35" in text
            or "temporarily unavailable" in text
            or "timeout" in text
            or "timed out" in text
            or "connection reset" in text
            or "connection aborted" in text
        )

    def _execute_with_retry(label, query_factory, retries=2, delay=0.25):
        last_exc = None
        for attempt in range(retries + 1):
            try:
                return query_factory().execute()
            except Exception as exc:
                last_exc = exc
                transient = _is_transient_error(exc)
                app.logger.warning(
                    "supabase_execute_failed label=%s attempt=%s transient=%s error=%s",
                    label,
                    attempt + 1,
                    transient,
                    exc,
                )
                if not transient or attempt >= retries:
                    raise
                time.sleep(delay * (attempt + 1))
        raise last_exc

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

    def _identity_values(agent):
        vals = []
        for key in ("id", "auth_id", "user_id", "email"):
            val = str(agent.get(key) or "").strip()
            if val and val not in vals:
                vals.append(val)
        return vals

    def _matches_identity(row, values, fields):
        for field in fields:
            if str(row.get(field) or "").strip() in values:
                return True
        return False

    def _select_all(table, limit=10000, order_col="created_at", desc=True):
        try:
            q = sb_admin.table(table).select("*")
            if order_col:
                q = q.order(order_col, desc=desc)
            if limit:
                q = q.limit(limit)
            rows = q.execute().data or []
            debug("_select_all", table=table, rows=len(rows))
            return rows
        except Exception as e:
            if order_col:
                try:
                    q = sb_admin.table(table).select("*")
                    if limit:
                        q = q.limit(limit)
                    rows = q.execute().data or []
                    debug("_select_all_retry_no_order", table=table, rows=len(rows))
                    return rows
                except Exception:
                    pass
            app.logger.warning("Supabase select failed table=%s error=%s", table, e)
            return []

    def get_agent():
        email = (session.get("email") or session.get("agent_email") or "").strip().lower()
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
            debug("get_agent", {"email": email}, agent_profiles=len(rows))
            if not rows:
                return None, f"Agent profile not found for {email}"
            return rows[0], None
        except Exception as e:
            return None, str(e)

    def get_rates():
        for table_name in ["weekly_payment_settings", "payment_rules"]:
            try:
                rows = _select_all(table_name, 50, "updated_at", True)
                debug("get_rates", table=table_name, rows=len(rows))
                if rows:
                    row = rows[0]
                    return {
                        "client_register_amount": safe_float(
                            row.get("client_register_amount")
                            or row.get("client_reg")
                            or row.get("client_amount")
                        ),
                        "driver_register_amount": safe_float(
                            row.get("driver_register_amount")
                            or row.get("driver_reg")
                            or row.get("driver_amount")
                        ),
                    }
            except Exception:
                continue
        return {"client_register_amount": 0, "driver_register_amount": 0}

    def get_wallet(agent):
        ids = _identity_values(agent)
        try:
            rows = _select_all("agent_wallets", 5000)
            rows = [
                r for r in rows
                if _matches_identity(r, ids, ("agent_id", "agent_auth_id", "user_id", "auth_id", "agent_email", "email"))
            ]
            debug("get_wallet", {"agent_id": agent.get("id"), "email": agent.get("email")}, agent_wallets=len(rows))
            if rows:
                row = rows[0]
                return safe_float(
                    row.get("available")
                    or row.get("available_balance")
                    or row.get("balance")
                    or row.get("wallet_balance")
                )
        except Exception:
            pass
        return 0.0

    def _filter_created(rows, start=None, end=None):
        if not start and not end:
            return rows
        out = []
        for r in rows:
            raw = r.get("created_at")
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except Exception:
                continue
            if start and dt < start:
                continue
            if end and dt > end:
                continue
            out.append(r)
        return out

    def _norm_identity(value):
        return str(value or "").strip().lower()

    def _leaderboard_period_bounds(period):
        now = datetime.now(UTC)
        period = (period or "week").strip().lower()
        if period == "all":
            return "all", None, None
        if period == "month":
            return "month", now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now
        start, end = week_range()
        return "week", start, end

    def _leaderboard_people(table, start=None, end=None):
        column_sets = [
            (
            "id,full_name,town,created_at,recruiter_agent_id,agent_id,"
            "agent_auth_id,recruiter_auth_id,recruiter_email"
            ),
            "id,full_name,town,created_at,recruiter_agent_id,agent_id",
            "id,full_name,created_at,recruiter_agent_id",
        ]
        last_exc = None
        for cols in column_sets:
            try:
                def build(cols=cols):
                    q = sb_admin.table(table).select(cols)
                    if start:
                        q = q.gte("created_at", iso(start))
                    if end:
                        q = q.lte("created_at", iso(end))
                    return q.limit(10000)
                rows = _execute_with_retry(f"leaderboard_{table}", build, retries=1).data or []
                return rows
            except Exception as exc:
                last_exc = exc
                app.logger.warning(
                    "leaderboard_table_query_failed table=%s columns=%s error=%s",
                    table,
                    cols,
                    exc,
                )
        raise last_exc

    def _agent_identity_index(agents):
        index = {}
        for agent in agents:
            for key in ("id", "auth_id", "user_id", "email"):
                ident = _norm_identity(agent.get(key))
                if ident and ident not in index:
                    index[ident] = agent
        return index

    def _leaderboard_agent_for_row(row, agent_index):
        for key in ("recruiter_agent_id", "agent_id", "agent_auth_id", "recruiter_auth_id", "recruiter_email"):
            ident = _norm_identity(row.get(key))
            if ident and ident in agent_index:
                return agent_index[ident]
        return None

    def _aggregate_leaderboard(rows, agents, metric):
        agent_index = _agent_identity_index(agents)
        by_id = {}
        unmatched = 0

        for row in rows:
            agent = _leaderboard_agent_for_row(row, agent_index)
            if not agent:
                unmatched += 1
                continue

            aid = str(agent.get("id") or agent.get("email") or "").strip()
            if not aid:
                unmatched += 1
                continue

            item = by_id.setdefault(aid, {
                "agent_id": agent.get("id"),
                "agent_name": agent.get("full_name") or agent.get("username") or agent.get("email") or "Agent",
                "full_name": agent.get("full_name") or agent.get("username") or agent.get("email") or "Agent",
                "email": agent.get("email") or "",
                "count": 0,
                "town": agent.get("operation_region") or agent.get("town") or "Unknown",
                "_towns": {},
            })
            item["count"] += 1
            town = (row.get("town") or agent.get("operation_region") or agent.get("town") or "Unknown").strip()
            item["_towns"][town] = item["_towns"].get(town, 0) + 1

        ranked = []
        for item in by_id.values():
            towns = item.pop("_towns", {})
            if towns:
                item["town"] = max(towns, key=towns.get)
            item[metric] = item["count"]
            ranked.append(item)

        ranked.sort(key=lambda item: item["count"], reverse=True)
        return ranked[:20], unmatched

    def _duplicate_phone_exists(table, phone):
        for column in ("phone_number", "phone"):
            try:
                rows = (
                    _execute_with_retry(
                        f"{table}_duplicate_{column}",
                        lambda column=column: sb_admin.table(table).select("id").eq(column, phone).limit(1),
                        retries=1,
                    ).data or []
                )
                if rows:
                    return True, column, None
            except Exception as exc:
                app.logger.warning(
                    "registration_duplicate_check_failed table=%s column=%s phone_present=%s error=%s",
                    table,
                    column,
                    bool(phone),
                    exc,
                )
                if _is_transient_error(exc):
                    return False, column, exc
                continue
        return False, None, None

    def driver_rows(agent_or_id, start=None, end=None):
        if isinstance(agent_or_id, dict):
            values = _identity_values(agent_or_id)
        else:
            values = [str(agent_or_id)]
        rows = _select_all("drivers", 10000)
        rows = [
            r for r in rows
            if _matches_identity(r, values, ("recruiter_agent_id", "agent_id", "agent_auth_id", "recruiter_auth_id", "recruiter_email"))
        ]
        rows = _filter_created(rows, start, end)
        debug("driver_rows", {"ids": values[:3]}, drivers=len(rows))
        return rows

    def client_rows(agent_or_id, start=None, end=None):
        if isinstance(agent_or_id, dict):
            values = _identity_values(agent_or_id)
        else:
            values = [str(agent_or_id)]
        rows = _select_all("clients", 10000)
        rows = [
            r for r in rows
            if _matches_identity(r, values, ("recruiter_agent_id", "agent_id", "agent_auth_id", "recruiter_auth_id", "recruiter_email"))
        ]
        rows = _filter_created(rows, start, end)
        debug("client_rows", {"ids": values[:3]}, clients=len(rows))
        return rows

    def activity(agent, period="week"):
        aid = agent.get("id")
        if period == "all":
            drows = driver_rows(agent)
            crows = client_rows(agent)
        elif period == "day":
            ds, de = day_range()
            drows = driver_rows(agent, ds, de)
            crows = client_rows(agent, ds, de)
        else:
            ws, we = week_range()
            drows = driver_rows(agent, ws, we)
            crows = client_rows(agent, ws, we)

        rows = []
        for r in crows:
            rows.append({
                "subject_type": "client",
                "full_name": r.get("full_name") or "",
                "phone": r.get("phone") or r.get("phone_number") or "",
                "town": r.get("town") or r.get("region") or "",
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
        ids = _identity_values(agent)
        rows = []
        refs = _select_all("agent_referrals", 5000)
        child_ids = set()
        for r in refs:
            if _matches_identity(r, ids, ("parent_agent_id", "parent_agent_auth_id", "parent_agent_email", "referred_by")):
                child = str(r.get("child_agent_id") or r.get("agent_id") or "").strip()
                if child:
                    child_ids.add(child)
                    rows.append({
                        "child_agent_id": child,
                        "full_name": r.get("child_agent_name") or "",
                        "email": r.get("child_agent_email") or "",
                        "joined_at": r.get("created_at"),
                    })

        leader_code = str(agent.get("referral_code") or "").strip()
        all_agents = _select_all("agent_profiles", 5000)
        for child in all_agents:
            child_id = str(child.get("id") or "").strip()
            if child_id == str(agent.get("id") or "") or child_id in child_ids:
                continue
            if (
                str(child.get("team_leader_id") or "").strip() in ids
                or str(child.get("referred_by") or "").strip() in ids
                or (leader_code and str(child.get("referred_by_code") or "").strip() == leader_code)
            ):
                child_ids.add(child_id)
                rows.append({
                    "child_agent_id": child_id,
                    "full_name": child.get("full_name") or child.get("username") or "",
                    "email": child.get("email") or "",
                    "joined_at": child.get("created_at"),
                })

        ds, de = day_range()
        ws, we = week_range()
        for r in rows:
            child = r.get("child_agent_id")
            r["drivers_day"] = len(driver_rows(child, ds, de)) if child else 0
            r["clients_day"] = len(client_rows(child, ds, de)) if child else 0
            r["drivers_week"] = len(driver_rows(child, ws, we)) if child else 0
            r["clients_week"] = len(client_rows(child, ws, we)) if child else 0
            r["drivers_all"] = len(driver_rows(child)) if child else 0
            r["clients_all"] = len(client_rows(child)) if child else 0

        debug("team_agents", {"agent_id": agent.get("id"), "email": agent.get("email")}, team=len(rows))
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
                "region": agent.get("region") or agent.get("operation_region"),
                "badge": agent.get("badge") or agent.get("role"),
                "referral_code": agent.get("referral_code"),
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

        wk_d = driver_rows(agent, ws, we)
        wk_c = client_rows(agent, ws, we)
        all_d = driver_rows(agent)
        all_c = client_rows(agent)
        team = team_agents(agent)

        earnings_week = (
            len(wk_c) * safe_float(rates["client_register_amount"]) +
            len(wk_d) * safe_float(rates["driver_register_amount"])
        )
        debug(
            "agent_summary_v4",
            {"agent_id": agent.get("id"), "email": agent.get("email")},
            drivers_week=len(wk_d),
            clients_week=len(wk_c),
            drivers_all=len(all_d),
            clients_all=len(all_c),
            team=len(team),
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
            "referral_link": f"/join?ref={agent.get('referral_code') or agent.get('email') or ''}",
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

        return jsonify({"ok": True, "rows": activity(agent, period)})

    @app.route("/api/agent/team_v4", methods=["GET"], endpoint="agent_team_v4")
    @require_login("AGENT")
    def agent_team_v4():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        return jsonify({"ok": True, "rows": team_agents(agent)})

    
    @app.route("/api/agent/leaderboard_v4", methods=["GET"], endpoint="agent_leaderboard_v4")
    @require_login("AGENT")
    def agent_leaderboard_v4():
        route_start = time.monotonic()
        agent, agent_err = get_agent()
        period, start, end = _leaderboard_period_bounds(request.args.get("period"))

        try:
            agents = (
                sb_admin.table("agent_profiles")
                .select("id,auth_id,user_id,full_name,username,email,town,operation_region")
                .limit(5000)
                .execute()
                .data or []
            )
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

        try:
            drivers = _leaderboard_people("drivers", start, end)
            clients = _leaderboard_people("clients", start, end)
        except Exception as e:
            duration_ms = int((time.monotonic() - route_start) * 1000)
            app.logger.exception(
                "agent_leaderboard_v4_failed period=%s agent=%s duration_ms=%s error=%s",
                period,
                {"id": (agent or {}).get("id"), "email": (agent or {}).get("email"), "err": agent_err},
                duration_ms,
                e,
            )
            return jsonify({"ok": False, "error": "Could not load leaderboard data"}), 500

        driver_rows_rank, unmatched_drivers = _aggregate_leaderboard(drivers, agents, "drivers")
        client_rows_rank, unmatched_clients = _aggregate_leaderboard(clients, agents, "clients")
        duration_ms = int((time.monotonic() - route_start) * 1000)

        app.logger.info(
            "agent_leaderboard_v4 period=%s agent=%s rows_scanned=%s rows_returned=%s unmatched=%s duration_ms=%s",
            period,
            {"id": (agent or {}).get("id"), "email": (agent or {}).get("email"), "err": agent_err},
            {"drivers": len(drivers), "clients": len(clients), "agents": len(agents)},
            {"drivers": len(driver_rows_rank), "clients": len(client_rows_rank)},
            {"drivers": unmatched_drivers, "clients": unmatched_clients},
            duration_ms,
        )

        return jsonify({
            "ok": True,
            "period": period,
            "range": {
                "start": iso(start) if start else None,
                "end": iso(end) if end else None,
            },
            "top_driver_recruiters": driver_rows_rank,
            "top_client_recruiters": client_rows_rank
        })

    @app.route("/api/agent/wallet_history_v4", methods=["GET"], endpoint="agent_wallet_history_v4")
    @require_login("AGENT")
    def agent_wallet_history_v4():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        try:
            rows = [
                r for r in _select_all("agent_wallet_ledger", 5000)
                if _matches_identity(
                    r,
                    _identity_values(agent),
                    ("agent_id", "agent_auth_id", "user_id", "auth_id", "agent_email", "email"),
                )
            ][:100]
            debug("agent_wallet_history_v4", {"agent_id": agent.get("id")}, agent_wallet_ledger=len(rows))
            return jsonify({"ok": True, "rows": rows})
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e),
                "hint": "Run leaderboard_wallet_schema.sql in Supabase SQL editor first."
            }), 500

    @app.route("/api/agent/team_summary_v4", methods=["GET"], endpoint="agent_team_summary_v4")
    @require_login("AGENT")
    def agent_team_summary_v4():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        team = team_agents(agent)
        total_agents = len(team)
        drivers_day = sum(int(x.get("drivers_day") or 0) for x in team)
        clients_day = sum(int(x.get("clients_day") or 0) for x in team)
        drivers_week = sum(int(x.get("drivers_week") or 0) for x in team)
        clients_week = sum(int(x.get("clients_week") or 0) for x in team)
        drivers_all = sum(int(x.get("drivers_all") or 0) for x in team)
        clients_all = sum(int(x.get("clients_all") or 0) for x in team)

        return jsonify({
            "ok": True,
            "summary": {
                "team_agents": total_agents,
                "drivers_day": drivers_day,
                "clients_day": clients_day,
                "drivers_week": drivers_week,
                "clients_week": clients_week,
                "drivers_all": drivers_all,
                "clients_all": clients_all,
            }
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
            "phone": (data.get("phone") or "").strip() or agent.get("phone"),
            "email": (data.get("email") or "").strip() or agent.get("email"),
            "town": (data.get("town") or "").strip() or agent.get("town"),
            "region": (data.get("region") or "").strip() or agent.get("region"),
            "operation_region": (
                (data.get("operation_region") or "").strip()
                or (data.get("region") or "").strip()
                or agent.get("operation_region")
            ),
        }
        for optional_key in ("profile_picture_url", "residential_address", "pin"):
            if optional_key in data:
                updates[optional_key] = (data.get(optional_key) or "").strip()

        try:
            sb_admin.table("agent_profiles").update(updates).eq("id", agent.get("id")).execute()
            session["email"] = updates["email"]
            session["agent_email"] = updates["email"]
            return jsonify({"ok": True, "success": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/agent/register_driver_v4", methods=["POST"], endpoint="agent_register_driver_v4")
    @require_login("AGENT")
    def agent_register_driver_v4():
        route_start = time.monotonic()
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        data = request.get_json(silent=True) or {}
        full_name = (data.get("full_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        town = (data.get("town") or "").strip()
        license_number = (data.get("license_number") or "").strip()
        car_details = (data.get("car_details") or "").strip()
        external_code = (data.get("external_code") or "").strip()

        if not full_name or not phone or not license_number or not car_details:
            return jsonify({"ok": False, "error": "Full name, phone, license number and car details are required"}), 400

        app.logger.info(
            "agent_register_driver_v4 payload agent=%s fields=%s phone_present=%s",
            {"id": agent.get("id"), "email": agent.get("email")},
            sorted([k for k, v in data.items() if v not in (None, "")]),
            bool(phone),
        )

        duplicate, duplicate_column, duplicate_error = _duplicate_phone_exists("drivers", phone)
        if duplicate_error:
            app.logger.exception(
                "agent_register_driver_v4_failed phase=duplicate_check agent_id=%s error=%s",
                agent.get("id"),
                duplicate_error,
            )
            return jsonify({"ok": False, "error": "Could not validate driver phone. Please try again."}), 503
        app.logger.info(
            "agent_register_driver_v4 duplicate_check agent_id=%s duplicate=%s column=%s",
            agent.get("id"),
            duplicate,
            duplicate_column,
        )
        if duplicate:
            return jsonify({"ok": False, "error": "Driver phone already exists"}), 400

        payload = {
            "full_name": full_name,
            "phone_number": phone,
            "phone": phone,
            "license_number": license_number,
            "car_details": car_details,
            "town": town,
            "status": "pending_approval",
            "trips_completed": 0,
            "verified_trips": 0,
            "recruiter_agent_id": str(agent.get("id") or ""),
            "recruiter_name": agent.get("full_name") or agent.get("email"),
        }

        if external_code:
            payload["external_code"] = external_code

        try:
            res = _execute_with_retry(
                "drivers_insert",
                lambda: sb_admin.table("drivers").insert(payload),
                retries=2,
            )
            inserted = res.data or []
            safe_log("REGISTER_DRIVER", f"Agent {agent.get('email')} registered driver {full_name}")
            app.logger.info(
                "agent_register_driver_v4 insert_ok agent_id=%s inserted_rows=%s created_id=%s duration_ms=%s",
                agent.get("id"),
                len(inserted),
                (inserted[0] or {}).get("id") if inserted else None,
                int((time.monotonic() - route_start) * 1000),
            )
            return jsonify({"ok": True, "success": True, "row": inserted[0] if inserted else None})
        except Exception as e:
            app.logger.exception(
                "agent_register_driver_v4_failed phase=insert agent_id=%s duration_ms=%s error=%s",
                agent.get("id"),
                int((time.monotonic() - route_start) * 1000),
                e,
            )
            status = 503 if _is_transient_error(e) else 500
            return jsonify({"ok": False, "error": "Driver registration could not be saved. Please try again."}), status

    @app.route("/api/agent/register_client_v4", methods=["POST"], endpoint="agent_register_client_v4")
    @require_login("AGENT")
    def agent_register_client_v4():
        route_start = time.monotonic()
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        data = request.get_json(silent=True) or {}
        full_name = (data.get("full_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        town = (data.get("town") or "").strip()
        external_code = (data.get("external_code") or "").strip()

        if not phone:
            return jsonify({"ok": False, "error": "Phone number is required"}), 400

        app.logger.info(
            "agent_register_client_v4 payload agent=%s fields=%s phone_present=%s",
            {"id": agent.get("id"), "email": agent.get("email")},
            sorted([k for k, v in data.items() if v not in (None, "")]),
            bool(phone),
        )

        duplicate, duplicate_column, duplicate_error = _duplicate_phone_exists("clients", phone)
        if duplicate_error:
            app.logger.exception(
                "agent_register_client_v4_failed phase=duplicate_check agent_id=%s error=%s",
                agent.get("id"),
                duplicate_error,
            )
            return jsonify({"ok": False, "error": "Could not validate client phone. Please try again."}), 503
        app.logger.info(
            "agent_register_client_v4 duplicate_check agent_id=%s duplicate=%s column=%s",
            agent.get("id"),
            duplicate,
            duplicate_column,
        )
        if duplicate:
            return jsonify({"ok": False, "error": "Client phone already exists"}), 400

        payload = {
            "phone_number": phone,
            "phone": phone,
            "yene_code": external_code or "PENDING",
            "status": "pending_approval",
            "trips_completed": 0,
            "recruiter_agent_id": str(agent.get("id") or ""),
            "recruiter_name": agent.get("full_name") or agent.get("email"),
        }

        if full_name:
            payload["full_name"] = full_name
        if town:
            payload["town"] = town
        if external_code:
            payload["external_code"] = external_code

        try:
            res = _execute_with_retry(
                "clients_insert",
                lambda: sb_admin.table("clients").insert(payload),
                retries=2,
            )
            inserted = res.data or []
            safe_log("REGISTER_CLIENT", f"Agent {agent.get('email')} registered client {phone}")
            app.logger.info(
                "agent_register_client_v4 insert_ok agent_id=%s inserted_rows=%s created_id=%s duration_ms=%s",
                agent.get("id"),
                len(inserted),
                (inserted[0] or {}).get("id") if inserted else None,
                int((time.monotonic() - route_start) * 1000),
            )
            return jsonify({"ok": True, "success": True, "row": inserted[0] if inserted else None})
        except Exception as e:
            app.logger.exception(
                "agent_register_client_v4_failed phase=insert agent_id=%s duration_ms=%s error=%s",
                agent.get("id"),
                int((time.monotonic() - route_start) * 1000),
                e,
            )
            status = 503 if _is_transient_error(e) else 500
            return jsonify({"ok": False, "error": "Client registration could not be saved. Please try again."}), status
