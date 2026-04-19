from datetime import datetime, timedelta
from io import BytesIO
import os
import uuid

from flask import flash, jsonify, redirect, render_template, request, send_file, session
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def register_yene_compat_routes(app, sb_admin):
    def _now_iso():
        return datetime.utcnow().isoformat() + "Z"

    def _clean(v):
        return str(v or "").strip()

    def _safe_float(v, default=0.0):
        try:
            return float(v or 0)
        except Exception:
            return default

    def _fmt_money(v):
        return f"N$ {_safe_float(v):,.2f}"

    def _parse_date(value, default):
        try:
            return datetime.fromisoformat(str(value)).date()
        except Exception:
            return default

    def _debug(route, identity=None, **counts):
        try:
            app.logger.info(
                "compat_data route=%s identity=%s counts=%s env=%s",
                route,
                identity or {},
                counts,
                {
                    "url_present": bool(os.getenv("SUPABASE_URL")),
                    "anon_present": bool(os.getenv("SUPABASE_ANON_KEY")),
                    "service_present": bool(os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")),
                },
            )
        except Exception:
            pass

    def _safe_select(table, filters=None, cols="*", limit=None, order_col=None, desc=False):
        filters = filters or {}
        try:
            all_rows = []
            page_size = 1000
            start = 0
            remaining = int(limit) if limit else None

            while True:
                batch_size = page_size if remaining is None else min(page_size, remaining)
                if batch_size <= 0:
                    break

                q = sb_admin.table(table).select(cols).range(start, start + batch_size - 1)
                for k, v in filters.items():
                    q = q.eq(k, v)
                if order_col:
                    q = q.order(order_col, desc=desc)

                res = q.execute()
                batch = res.data or []
                _debug("_safe_select_batch", table=table, start=start, rows=len(batch))
                all_rows.extend(batch)

                if len(batch) < batch_size:
                    break

                start += batch_size
                if remaining is not None:
                    remaining -= len(batch)

            _debug("_safe_select", table=table, rows=len(all_rows))
            return all_rows
        except Exception as e:
            if order_col:
                try:
                    all_rows = []
                    page_size = 1000
                    start = 0
                    remaining = int(limit) if limit else None

                    while True:
                        batch_size = page_size if remaining is None else min(page_size, remaining)
                        if batch_size <= 0:
                            break

                        q = sb_admin.table(table).select(cols).range(start, start + batch_size - 1)
                        for k, v in filters.items():
                            q = q.eq(k, v)

                        res = q.execute()
                        batch = res.data or []
                        _debug("_safe_select_retry_no_order_batch", table=table, start=start, rows=len(batch))
                        all_rows.extend(batch)

                        if len(batch) < batch_size:
                            break

                        start += batch_size
                        if remaining is not None:
                            remaining -= len(batch)

                    _debug("_safe_select_retry_no_order", table=table, rows=len(all_rows))
                    return all_rows
                except Exception:
                    pass
            app.logger.warning("Supabase select failed table=%s error=%s", table, e)
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

    def _safe_delete(table, filters):
        try:
            q = sb_admin.table(table).delete()
            for k, v in filters.items():
                q = q.eq(k, v)
            return q.execute()
        except Exception as e:
            return e

    def _approved(row_or_status):
        status = row_or_status.get("status") if isinstance(row_or_status, dict) else row_or_status
        return str(status or "").upper() in {"ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED"}

    def _pending(row_or_status):
        status = row_or_status.get("status") if isinstance(row_or_status, dict) else row_or_status
        return str(status or "").upper() in {"PENDING", "PENDING_APPROVAL", "UNDER_REVIEW"}

    def _payment_rules_latest():
        weekly = _safe_select("weekly_payment_settings", {}, "*", 50, "updated_at", True)
        if weekly:
            row = weekly[0]
            row["_source"] = "weekly_payment_settings"
            return row
        rules = _safe_select("payment_rules", {}, "*", 100, "updated_at", True)
        if rules:
            row = rules[0]
            row["_source"] = "payment_rules"
            return row
        return {"_source": "none"}

    def _payment_amounts():
        rules = _payment_rules_latest()
        return {
            "source": rules.get("_source") or "none",
            "driver_reg": _safe_float(
                rules.get("driver_reg")
                or rules.get("driver_register_amount")
                or rules.get("driver_amount")
            ),
            "client_reg": _safe_float(
                rules.get("client_reg")
                or rules.get("client_register_amount")
                or rules.get("client_amount")
            ),
            "daily_5_drivers_bonus": _safe_float(rules.get("daily_5_drivers_bonus")),
            "daily_5_clients_bonus": _safe_float(rules.get("daily_5_clients_bonus")),
            "weekly_30_activations_bonus": _safe_float(rules.get("weekly_30_activations_bonus")),
            "first_trip_bonus": _safe_float(rules.get("first_trip_bonus")),
        }

    def _all_agents():
        return _safe_select("agent_profiles", {}, "*", 5000, "created_at", True)

    def _agent_by_id(agent_id):
        rows = _safe_select("agent_profiles", {"id": str(agent_id)}, "*", 1)
        return rows[0] if rows else None

    def _agent_by_email(email):
        email = _clean(email).lower()
        rows = _safe_select("agent_profiles", {"email": email}, "*", 1)
        return rows[0] if rows else None

    def _temp_password_for_agent(agent):
        base = str((agent or {}).get("phone") or (agent or {}).get("phone_number") or "")[-6:]
        if not base:
            base = "123456"
        return f"Yene@{base}"

    def _link_or_create_auth_user(email, password, full_name="", username=""):
        try:
            users = sb_admin.auth.admin.list_users()
            found = None
            for u in getattr(users, "users", []) or []:
                if str(getattr(u, "email", "") or "").strip().lower() == str(email or "").strip().lower():
                    found = u
                    break

            if found:
                uid = getattr(found, "id", None)
                try:
                    sb_admin.auth.admin.update_user_by_id(uid, {
                        "password": password,
                        "email_confirm": True,
                        "user_metadata": {
                            "full_name": full_name or "",
                            "username": username or "",
                        }
                    })
                except Exception:
                    pass
                return uid

            created = sb_admin.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "full_name": full_name or "",
                    "username": username or "",
                }
            })
            return getattr(getattr(created, "user", None), "id", None)
        except Exception as e:
            app.logger.warning("link_or_create_auth_user_failed email=%s error=%s", email, e)
            return None

    def _current_agent():
        return _agent_by_email(session.get("email") or session.get("agent_email"))

    def _identity_values(agent):
        vals = []
        for key in ("id", "auth_id", "user_id", "email"):
            val = str((agent or {}).get(key) or "").strip()
            if val and val not in vals:
                vals.append(val)
        return vals

    def _matches_identity(row, values, fields):
        return any(str(row.get(field) or "").strip() in values for field in fields)

    def _agent_rows(table, agent, fields):
        values = _identity_values(agent)
        return [r for r in _safe_select(table, {}, "*", 10000, "created_at", True) if _matches_identity(r, values, fields)]

    def _drivers():
        return _safe_select("drivers", {}, "*", 10000, "created_at", True)

    def _clients():
        return _safe_select("clients", {}, "*", 10000, "created_at", True)

    def _agent_registration_rows(agent_or_id, date_from=None, date_to=None):
        agent = agent_or_id if isinstance(agent_or_id, dict) else _agent_by_id(agent_or_id)
        values = _identity_values(agent) if agent else [str(agent_or_id)]
        rows = []
        for d in _drivers():
            if not _matches_identity(d, values, ("recruiter_agent_id", "agent_id", "agent_auth_id", "recruiter_auth_id", "recruiter_email")):
                continue
            rows.append({
                "type": "Driver",
                "name": d.get("full_name") or d.get("name") or "",
                "phone": d.get("phone") or d.get("phone_number") or "",
                "town": d.get("town") or "",
                "code": d.get("external_code") or d.get("license_number") or "",
                "created_at": d.get("created_at"),
                "status": d.get("status"),
                "trips_completed": d.get("trips_completed") or 0,
                "verified_trips": d.get("verified_trips") or 0,
            })
        for c in _clients():
            if not _matches_identity(c, values, ("recruiter_agent_id", "agent_id", "agent_auth_id", "recruiter_auth_id", "recruiter_email")):
                continue
            rows.append({
                "type": "Client",
                "name": c.get("full_name") or c.get("name") or "",
                "phone": c.get("phone") or c.get("phone_number") or "",
                "town": c.get("town") or "",
                "code": c.get("external_code") or c.get("yene_code") or "",
                "created_at": c.get("created_at"),
                "status": c.get("status"),
            })

        def in_range(row):
            raw = row.get("created_at")
            if not raw:
                return False
            day = str(raw)[:10]
            if date_from and day < date_from:
                return False
            if date_to and day > date_to:
                return False
            return True

        if date_from or date_to:
            rows = [r for r in rows if in_range(r)]
        rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        _debug("_agent_registration_rows", {"ids": values[:3]}, rows=len(rows))
        return rows

    def _period_from_request():
        mode = _clean(request.args.get("mode")) or "current_week"
        today = datetime.utcnow().date()
        date_from = date_to = None
        if mode == "all_time":
            return mode, None, None
        if mode == "custom":
            return mode, _clean(request.args.get("date_from")) or None, _clean(request.args.get("date_to")) or None
        if mode == "day":
            day = _clean(request.args.get("date_day")) or today.isoformat()
            return mode, day, day
        week_start = _clean(request.args.get("week_start"))
        try:
            start = datetime.fromisoformat(week_start).date() if week_start else today - timedelta(days=today.weekday())
        except Exception:
            start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return mode, start.isoformat(), end.isoformat()

    @app.get("/api/public-config")
    def public_config():
        return jsonify({
            "ok": True,
            "SUPABASE_URL": os.getenv("SUPABASE_URL", ""),
            "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY", ""),
        })

    @app.get("/admin")
    def admin_root_alias():
        return redirect("/dashboard/admin")

    @app.get("/admin/agent/<agent_id>")
    @app.get("/admin/agent_profile/<agent_id>")
    def admin_agent_profile_page(agent_id):
        if (session.get("role") or "").upper() != "ADMIN":
            return redirect("/admin/login")
        return render_template("admin_agent_profile.html", agent_id=agent_id)

    @app.route("/apply")
    def apply_alias():
        return redirect("/register")

    @app.route("/register", methods=["GET", "POST"])
    def register_agent_public():
        if request.method == "GET":
            return render_template("register.html")

        full_name = _clean(request.form.get("full_name"))
        username = _clean(request.form.get("username")).lower()
        phone = _clean(request.form.get("phone"))
        email = _clean(request.form.get("email")).lower()
        gender = _clean(request.form.get("gender"))
        password = request.form.get("password") or ""

        if not full_name or not username or not phone or not email or len(password) < 6:
            flash("Full name, username, phone, email and a 6-character password are required")
            return redirect("/register")

        if _agent_by_email(email):
            flash("An agent profile already exists for this email")
            return redirect("/login")

        auth_id = None
        auth_error = None
        try:
            auth_res = sb_admin.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name, "username": username},
            })
            auth_id = getattr(getattr(auth_res, "user", None), "id", None)
        except Exception as e:
            auth_error = str(e)

        agent_id = str(uuid.uuid4())
        payload = {
            "id": agent_id,
            "auth_id": auth_id,
            "user_id": auth_id,
            "full_name": full_name,
            "username": username,
            "phone": phone,
            "phone_number": phone,
            "email": email,
            "gender": gender,
            "role": "AGENT",
            "status": "PENDING_APPROVAL",
            "created_at": _now_iso(),
        }

        res = _safe_insert("agent_profiles", payload)
        if isinstance(res, Exception):
            flash(f"Registration failed: {res}")
            return redirect("/register")

        if not auth_id:
            flash("Account creation failed. Please try again or contact admin.")
            return redirect("/register")

        session.clear()
        session["email"] = email
        session["agent_email"] = email
        session["role"] = "AGENT"
        return redirect("/agent/dashboard")

    @app.get("/api/agent/me")
    def agent_me_alias():
        agent = _current_agent()
        if not agent:
            return jsonify({"ok": False, "error": "Agent profile not found"}), 404
        _debug("agent_me", {"agent_id": agent.get("id"), "email": agent.get("email")}, agent_profiles=1)
        return jsonify({"ok": True, "agent": agent, "profile": agent, "me": agent})

    @app.get("/api/agent/competition_board_v1")
    def agent_competition_board_v1():
        me = _current_agent()
        if not me:
            return jsonify({"ok": False, "error": "Agent profile not found"}), 404
        agents = _all_agents()
        drivers = _drivers()
        clients = _clients()
        rows = []
        for a in agents:
            aid = str(a.get("id") or "")
            values = _identity_values(a)
            d_count = len([x for x in drivers if _matches_identity(x, values, ("recruiter_agent_id", "agent_id", "agent_auth_id", "recruiter_email"))])
            c_count = len([x for x in clients if _matches_identity(x, values, ("recruiter_agent_id", "agent_id", "agent_auth_id", "recruiter_email"))])
            rows.append({
                "agent_id": aid,
                "agent_name": a.get("full_name") or a.get("username") or a.get("email") or "Agent",
                "drivers": d_count,
                "clients": c_count,
                "score": d_count * 3 + c_count * 2,
            })
        rows.sort(key=lambda x: x["score"], reverse=True)
        for idx, row in enumerate(rows, start=1):
            row["rank"] = idx
        _debug("agent_competition_board_v1", {"agent_id": me.get("id")}, agent_profiles=len(agents), drivers=len(drivers), clients=len(clients), rows=len(rows))
        return jsonify({"ok": True, "rows": rows[:50]})

    @app.get("/api/agent/messages")
    def agent_messages():
        agent = _current_agent()
        if not agent:
            return jsonify({"ok": False, "error": "Agent profile not found"}), 404
        fields = ("agent_id", "agent_auth_id", "user_id", "auth_id", "agent_email", "email")
        rows = _agent_rows("agent_messages", agent, fields)
        notices = _agent_rows("agent_notifications", agent, fields)
        for n in notices:
            n.setdefault("subject", n.get("title") or "Notification")
            n.setdefault("message", n.get("body") or n.get("message") or "")
            n.setdefault("source", "agent_notifications")
        rows = (rows + notices)[:200]
        _debug("agent_messages", {"agent_id": agent.get("id")}, agent_messages=len(rows), agent_notifications=len(notices))
        return jsonify({"ok": True, "rows": rows})

    @app.get("/api/agent/messages/summary")
    def agent_messages_summary():
        rows = agent_messages().json.get("rows", [])
        unread = len([r for r in rows if str(r.get("status") or "").lower() not in {"read", "closed"}])
        _debug("agent_messages_summary", total=len(rows), unread=unread)
        return jsonify({"ok": True, "unread": unread, "total": len(rows)})

    @app.post("/api/agent/messages/<message_id>/read")
    def agent_message_read(message_id):
        _safe_update("agent_messages", {"id": message_id}, {"status": "read", "read_at": _now_iso()})
        return jsonify({"ok": True})

    @app.post("/api/agent/messages/send")
    def agent_message_send():
        agent = _current_agent()
        if not agent:
            return jsonify({"ok": False, "error": "Agent profile not found"}), 404
        data = request.get_json(silent=True) or {}
        payload = {
            "agent_id": str(agent.get("id")),
            "agent_email": agent.get("email"),
            "agent_name": agent.get("full_name") or agent.get("username") or agent.get("email"),
            "subject": _clean(data.get("subject")) or "Agent support message",
            "message": _clean(data.get("message") or data.get("body")),
            "status": "sent",
            "created_at": _now_iso(),
        }
        if not payload["message"]:
            return jsonify({"ok": False, "error": "message required"}), 400
        res = _safe_insert("agent_messages", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": True, "warning": str(res)})
        return jsonify({"ok": True})

    @app.get("/api/admin/overview")
    def admin_overview():
        agents = _all_agents()
        drivers = _drivers()
        clients = _clients()
        ledger = _safe_select("agent_wallet_ledger", {}, "*", 10000, "created_at", True)
        recent = []
        for d in drivers[:20]:
            recent.append({"type": "Driver", "name": d.get("full_name"), "phone": d.get("phone") or d.get("phone_number"), "town": d.get("town"), "agent": d.get("recruiter_name"), "created_at": d.get("created_at")})
        for c in clients[:20]:
            recent.append({"type": "Client", "name": c.get("full_name"), "phone": c.get("phone") or c.get("phone_number"), "town": c.get("town"), "agent": c.get("recruiter_name"), "created_at": c.get("created_at")})
        recent.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        data = {
            "agents_total": len(agents),
            "agents_active": len([a for a in agents if _approved(a)]),
            "agents_pending": len([a for a in agents if _pending(a)]),
            "agents_blocked": len([a for a in agents if str(a.get("status") or "").upper() == "BLOCKED"]),
            "drivers_total": len(drivers),
            "drivers_registered": len(drivers),
            "clients_total": len(clients),
            "clients_registered": len(clients),
            "total_paid": sum(_safe_float(x.get("amount")) for x in ledger if str(x.get("entry_type") or x.get("txn_type") or "").lower() == "debit"),
            "recent_activity": recent[:25],
        }
        _debug("admin_overview", agent_profiles=len(agents), drivers=len(drivers), clients=len(clients), agent_wallet_ledger=len(ledger))
        return jsonify({"ok": True, "data": data})

    @app.get("/api/admin/drivers")
    def admin_drivers():
        rows = _drivers()
        _debug("admin_drivers", drivers=len(rows))
        return jsonify({"ok": True, "data": rows, "rows": rows})

    @app.get("/api/admin/clients")
    def admin_clients():
        rows = _clients()
        _debug("admin_clients", clients=len(rows))
        return jsonify({"ok": True, "data": rows, "rows": rows})

    @app.get("/api/admin/finance")
    def admin_finance():
        ledger = _safe_select("agent_wallet_ledger", {}, "*", 5000, "created_at", True)
        wallets = _safe_select("agent_wallets", {}, "*", 5000, "updated_at", True)
        withdraws = _safe_select("agent_withdraw_requests", {}, "*", 5000, "created_at", True)
        rows = ledger + wallets + withdraws
        _debug("admin_finance", agent_wallet_ledger=len(ledger), agent_wallets=len(wallets), agent_withdraw_requests=len(withdraws), rows=len(rows))
        return jsonify({"ok": True, "data": rows, "rows": rows})

    @app.route("/api/admin/payment_rules", methods=["GET", "POST"])
    def admin_payment_rules_compat():
        if request.method == "GET":
            rows = _safe_select("payment_rules", {}, "*", 500, "updated_at", True)
            return jsonify({"ok": True, "data": rows, "rows": rows})
        data = request.get_json(silent=True) or {}
        payload = dict(data)
        payload["updated_at"] = _now_iso()
        row_id = _clean(payload.pop("id", ""))
        res = _safe_update("payment_rules", {"id": row_id}, payload) if row_id else _safe_insert("payment_rules", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500
        return jsonify({"ok": True})

    @app.post("/api/admin/broadcast")
    def admin_broadcast_compat():
        data = request.get_json(silent=True) or {}
        message = _clean(data.get("message"))
        if not message:
            return jsonify({"ok": False, "error": "message required"}), 400
        res = _safe_insert("broadcasts", {
            "title": _clean(data.get("title")) or "Admin Broadcast",
            "audience": _clean(data.get("audience")) or "all",
            "message": message,
            "created_at": _now_iso(),
        })
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500
        return jsonify({"ok": True})

    @app.get("/api/admin/audit_logs")
    def admin_audit_logs():
        system = _safe_select("system_logs", {}, "*", 500, "created_at", True)
        updates = _safe_select("admin_updates", {}, "*", 500, "created_at", True)
        rows = (system + updates)[:500]
        _debug("admin_audit_logs", system_logs=len(system), admin_updates=len(updates), rows=len(rows))
        return jsonify({"ok": True, "data": rows, "rows": rows})

    @app.get("/api/admin/agent_center/<agent_id>")
    def admin_agent_center(agent_id):
        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404
        mode, date_from, date_to = _period_from_request()
        rows = _agent_registration_rows(agent, date_from, date_to)
        days = {d: {"drivers": 0, "clients": 0} for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
        for row in rows:
            try:
                name = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00")).strftime("%a")
            except Exception:
                continue
            if name in days:
                key = "drivers" if row["type"] == "Driver" else "clients"
                days[name][key] += 1
        data = {
            "agent": agent,
            "mode": mode,
            "date_from": date_from,
            "date_to": date_to,
            "drivers_total": len([r for r in rows if r["type"] == "Driver"]),
            "clients_total": len([r for r in rows if r["type"] == "Client"]),
            "days": days,
            "rows": rows,
        }
        _debug("admin_agent_center", {"agent_id": agent_id}, rows=len(rows))
        return jsonify({"ok": True, "data": data})

    @app.get("/api/admin/agent_messages/<agent_id>")
    def admin_agent_messages(agent_id):
        agent = _agent_by_id(agent_id)
        rows = _safe_select("agent_messages", {"agent_id": str(agent_id)}, "*", 200, "created_at", True)
        if not rows and agent:
            rows = _safe_select("agent_messages", {"agent_email": agent.get("email")}, "*", 200, "created_at", True)
        return jsonify({"ok": True, "rows": rows})

    @app.post("/api/admin/agent_messages")
    def admin_agent_messages_post():
        data = request.get_json(silent=True) or {}
        agent_id = _clean(data.get("agent_id"))
        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404
        payload = {
            "agent_id": agent_id,
            "agent_email": agent.get("email"),
            "agent_name": data.get("agent_name") or agent.get("full_name") or agent.get("email"),
            "registration_type": _clean(data.get("registration_type")),
            "registration_id": _clean(data.get("registration_id")),
            "subject": _clean(data.get("subject")) or "Admin message",
            "message": _clean(data.get("message")),
            "status": "unread",
            "created_at": _now_iso(),
        }
        res = _safe_insert("agent_messages", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": True, "warning": str(res)})
        return jsonify({"ok": True})

    @app.get("/api/admin/agent_profile/<agent_id>")
    def admin_agent_profile_alias(agent_id):
        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404
        rows = _agent_registration_rows(agent_id)
        return jsonify({"ok": True, "agent": agent, "rows": rows})

    @app.post("/api/admin/agent_reset_pin/<agent_id>")
    def admin_agent_reset_pin(agent_id):
        pin = str(uuid.uuid4().int)[0:6]
        _safe_update("agent_profiles", {"id": agent_id}, {"pin": pin})
        return jsonify({"ok": True, "pin": pin})

    @app.post("/api/admin/agent_set_status/<agent_id>")
    def admin_agent_set_status(agent_id):
        data = request.get_json(silent=True) or {}
        status = _clean(data.get("status")) or "ACTIVE"
        _safe_update("agent_profiles", {"id": agent_id}, {"status": status})
        _safe_update("agents", {"id": agent_id}, {"status": status})
        return jsonify({"ok": True})

    @app.post("/api/admin/approve_agent/<agent_id>")
    def admin_approve_agent(agent_id):
        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        payload = {
            "status": "ACTIVE",
            "must_change_password": False,
        }
        _safe_update("agent_profiles", {"id": agent_id}, payload)
        _safe_update("agents", {"id": agent_id}, {"status": "ACTIVE"})
        return jsonify({"ok": True, "message": "Agent approved", "agent_id": agent_id})

    @app.post("/api/admin/reset_agent_account/<agent_id>")
    def admin_reset_agent_account(agent_id):
        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        email = _clean(agent.get("email")).lower()
        if not email:
            return jsonify({"ok": False, "error": "Agent email missing"}), 400

        temp_password = _temp_password_for_agent(agent)
        uid = _link_or_create_auth_user(
            email=email,
            password=temp_password,
            full_name=agent.get("full_name") or "",
            username=agent.get("username") or "",
        )

        if not uid:
            return jsonify({"ok": False, "error": "Could not create or update auth user"}), 500

        payload = {
            "auth_id": uid,
            "user_id": uid,
            "status": "ACTIVE",
            "must_change_password": True,
            "temp_password": temp_password,
            "last_reset_at": _now_iso(),
            "reset_by_admin": session.get("admin_email") or session.get("email") or "admin",
        }
        _safe_update("agent_profiles", {"id": agent_id}, payload)
        _safe_update("agents", {"id": agent_id}, {"auth_id": uid, "status": "ACTIVE"})

        return jsonify({
            "ok": True,
            "message": "Agent account reset successfully",
            "agent_id": agent_id,
            "email": email,
            "temp_password": temp_password,
            "must_change_password": True,
        })

    @app.get("/api/admin/agent_account_help/<agent_id>")
    def admin_agent_account_help(agent_id):
        agent = _agent_by_id(agent_id)
        if not agent:
            return jsonify({"ok": False, "error": "Agent not found"}), 404

        mode, date_from, date_to = _period_from_request()
        rows = _agent_registration_rows(agent, date_from, date_to)
        days = {d: {"drivers": 0, "clients": 0} for d in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}
        for row in rows:
            try:
                day_name = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00")).strftime("%a")
            except Exception:
                continue
            if day_name in days:
                key = "drivers" if row.get("type") == "Driver" else "clients"
                days[day_name][key] += 1

        auth_id = agent.get("auth_id") or agent.get("user_id")
        safe_agent = dict(agent)
        safe_agent["auth_linked"] = bool(auth_id)
        safe_agent["must_change_password"] = bool(agent.get("must_change_password"))

        return jsonify({
            "ok": True,
            "agent": safe_agent,
            "summary": {
                "mode": mode,
                "date_from": date_from,
                "date_to": date_to,
                "drivers_total": len([r for r in rows if r.get("type") == "Driver"]),
                "clients_total": len([r for r in rows if r.get("type") == "Client"]),
                "rows_total": len(rows),
                "daily": days,
            },
        })

    @app.get("/api/public/jobs")
    def public_jobs():
        rows = _safe_select("remote_jobs", {}, "*", 20, "created_at", True)
        out = []
        for row in rows:
            status = _clean(row.get("status") or "ACTIVE").upper()
            if status in {"CLOSED", "ARCHIVED", "INACTIVE"}:
                continue
            out.append(row)
        return jsonify({"ok": True, "rows": out[:10]})

    @app.route("/api/admin/jobs", methods=["GET", "POST"])
    def admin_jobs():
        if request.method == "GET":
            rows = _safe_select("remote_jobs", {}, "*", 200, "created_at", True)
            return jsonify({"ok": True, "rows": rows})

        data = request.get_json(silent=True) or {}
        title = _clean(data.get("title"))
        description = _clean(data.get("description") or data.get("details"))
        if not title or not description:
            return jsonify({"ok": False, "error": "title and description required"}), 400
        payload = {
            "title": title,
            "description": description,
            "town": _clean(data.get("town")),
            "region": _clean(data.get("region")),
            "target_count": int(data.get("target_count") or 0),
            "status": _clean(data.get("status")) or "ACTIVE",
            "created_by": session.get("admin_email") or session.get("email") or "admin",
            "created_at": _now_iso(),
        }
        res = _safe_insert("remote_jobs", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res), "hint": "Run sql/yene_upgrade.sql in Supabase."}), 500
        return jsonify({"ok": True, "row": (res.data or [payload])[0] if hasattr(res, "data") else payload})

    @app.get("/api/agent/jobs")
    def agent_jobs():
        rows = _safe_select("remote_jobs", {}, "*", 50, "created_at", True)
        out = []
        for row in rows:
            status = _clean(row.get("status") or "ACTIVE").upper()
            if status in {"CLOSED", "ARCHIVED", "INACTIVE"}:
                continue
            out.append(row)
        return jsonify({"ok": True, "rows": out[:20]})

    @app.get("/api/agent/group_messages")
    def agent_group_messages():
        rows = _safe_select("agent_group_messages", {}, "*", 100, "created_at", True)
        broadcasts = _safe_select("broadcasts", {}, "*", 50, "created_at", True)
        out = []
        for row in rows:
            out.append(row)
        for row in broadcasts:
            out.append({
                "id": row.get("id"),
                "author_name": "YENE Admin",
                "message": row.get("message"),
                "title": row.get("title") or "Admin notice",
                "created_at": row.get("created_at"),
                "source": "broadcasts",
            })
        out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return jsonify({"ok": True, "rows": out[:100]})

    @app.route("/agent/change-password", methods=["GET", "POST"])
    def agent_change_password():
        email = _clean(session.get("agent_email") or session.get("email")).lower()
        if not email:
            return redirect("/login")

        agent = _agent_by_email(email)
        if not agent:
            session.clear()
            flash("Agent profile not found")
            return redirect("/login")

        if request.method == "GET":
            return render_template("agent_change_password.html", agent=agent)

        current_password = request.form.get("current_password") or ""
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not current_password or not new_password or not confirm_password:
            flash("All password fields are required")
            return redirect("/agent/change-password")

        if len(new_password) < 6:
            flash("New password must be at least 6 characters")
            return redirect("/agent/change-password")

        if new_password != confirm_password:
            flash("New password and confirm password do not match")
            return redirect("/agent/change-password")

        try:
            _sign_in_supabase(email, current_password)
        except Exception:
            flash("Current password is incorrect")
            return redirect("/agent/change-password")

        uid = agent.get("auth_id") or agent.get("user_id")
        if not uid:
            flash("Account auth link missing. Contact admin.")
            return redirect("/agent/change-password")

        try:
            sb_admin.auth.admin.update_user_by_id(uid, {"password": new_password})
        except Exception as e:
            app.logger.warning("agent_change_password_failed email=%s error=%s", email, e)
            flash("Could not update password")
            return redirect("/agent/change-password")

        _safe_update("agent_profiles", {"id": agent.get("id")}, {
            "must_change_password": False,
            "temp_password": None,
        })

        flash("Password changed successfully")
        return redirect("/agent/dashboard")

    @app.post("/api/admin/delete_agent/<agent_id>")
    def admin_delete_agent_alias(agent_id):
        _safe_delete("agent_profiles", {"id": agent_id})
        _safe_delete("agents", {"id": agent_id})
        return jsonify({"ok": True})

    @app.get("/api/admin/weekly_agent_report_pdf")
    def admin_weekly_agent_report_pdf():
        today = datetime.utcnow().date()
        default_start = today - timedelta(days=today.weekday())
        start = _parse_date(_clean(request.args.get("week_start")), default_start)
        end = start + timedelta(days=6)

        incoming = _payment_rules_latest() or {}
        json_data = request.get_json(silent=True) or {}

        def _pick_value(*keys, fallback=0):
            for key in keys:
                v = request.args.get(key)
                if v not in (None, ""):
                    try:
                        return float(v)
                    except Exception:
                        pass
                v = json_data.get(key)
                if v not in (None, ""):
                    try:
                        return float(v)
                    except Exception:
                        pass
                v = incoming.get(key)
                if v not in (None, ""):
                    try:
                        return float(v)
                    except Exception:
                        pass
            return fallback

        rules = {
            "source": incoming.get("_source") or "request_or_db",
            "driver_reg": _pick_value("driver_reg", "driver_amount", fallback=0),
            "client_reg": _pick_value("client_reg", "client_amount", fallback=0),
            "daily_5_drivers_bonus": _pick_value("daily_5_drivers_bonus", fallback=0),
            "daily_5_clients_bonus": _pick_value("daily_5_clients_bonus", fallback=0),
            "weekly_30_activations_bonus": _pick_value("weekly_30_activations_bonus", fallback=0),
            "first_trip_bonus": _pick_value("first_trip_bonus", fallback=0),
        }

        selected_agent_id = _clean(request.args.get("agent_id"))
        if selected_agent_id:
            one = _agent_by_id(selected_agent_id)
            agents = [one] if one else []
        else:
            agents = _all_agents()

        def registration_payment(row):
            return rules["driver_reg"] if row.get("type") == "Driver" else rules["client_reg"]

        def approved_rows(rows):
            return [r for r in rows if _approved(r.get("status"))]

        report_rows = []
        grand_total = 0.0
        grand_drivers = 0
        grand_clients = 0
        for agent in agents:
            rows = _agent_registration_rows(agent, start.isoformat(), end.isoformat())
            rows = approved_rows(rows)
            if not rows:
                continue

            daily = {}
            base_total = 0.0
            for row in rows:
                day = str(row.get("created_at") or "")[:10] or "Unknown"
                bucket = daily.setdefault(day, {"drivers": 0, "clients": 0, "amount": 0.0})
                amount = registration_payment(row)
                row["payment_due"] = amount
                bucket["amount"] += amount
                base_total += amount
                if row.get("type") == "Driver":
                    bucket["drivers"] += 1
                else:
                    bucket["clients"] += 1

            bonus_total = 0.0
            bonus_lines = []
            for day, values in sorted(daily.items()):
                if values["drivers"] >= 5 and rules["daily_5_drivers_bonus"]:
                    bonus_total += rules["daily_5_drivers_bonus"]
                    bonus_lines.append(f"{day}: daily driver threshold bonus {_fmt_money(rules['daily_5_drivers_bonus'])}")
                if values["clients"] >= 5 and rules["daily_5_clients_bonus"]:
                    bonus_total += rules["daily_5_clients_bonus"]
                    bonus_lines.append(f"{day}: daily client threshold bonus {_fmt_money(rules['daily_5_clients_bonus'])}")

            driver_count = len([r for r in rows if r.get("type") == "Driver"])
            client_count = len([r for r in rows if r.get("type") == "Client"])
            first_trip_count = len([
                r for r in rows
                if r.get("type") == "Driver"
                and (
                    _safe_float(r.get("verified_trips")) > 0
                    or _safe_float(r.get("trips_completed")) > 0
                )
            ])
            if first_trip_count and rules["first_trip_bonus"]:
                first_trip_total = first_trip_count * rules["first_trip_bonus"]
                bonus_total += first_trip_total
                bonus_lines.append(f"First trip bonus: {first_trip_count} drivers x {_fmt_money(rules['first_trip_bonus'])} = {_fmt_money(first_trip_total)}")
            if (driver_count + client_count) >= 30 and rules["weekly_30_activations_bonus"]:
                bonus_total += rules["weekly_30_activations_bonus"]
                bonus_lines.append(f"Weekly 30 activation bonus {_fmt_money(rules['weekly_30_activations_bonus'])}")

            agent_total = base_total + bonus_total
            grand_total += agent_total
            grand_drivers += driver_count
            grand_clients += client_count
            report_rows.append({
                "agent": agent,
                "rows": rows,
                "daily": daily,
                "driver_count": driver_count,
                "client_count": client_count,
                "base_total": base_total,
                "bonus_total": bonus_total,
                "bonus_lines": bonus_lines,
                "agent_total": agent_total,
            })

        report_rows.sort(key=lambda item: item["agent_total"], reverse=True)

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        w, h = A4

        def new_page(title=False):
            c.showPage()
            draw_header()
            return h - 108

        def ensure_space(y, needed=60):
            if y < needed:
                return new_page()
            return y

        def draw_header():
            c.setFont("Helvetica-Bold", 16)
            c.drawString(40, h - 42, "YENE Weekly Agent Payment Report")
            c.setFont("Helvetica", 9)
            c.drawString(40, h - 58, f"Week: {start.isoformat()} to {end.isoformat()}")
            c.drawString(40, h - 72, f"Rate source: {rules['source']} | Driver {_fmt_money(rules['driver_reg'])} | Client {_fmt_money(rules['client_reg'])}")
            c.line(40, h - 82, w - 40, h - 82)

        draw_header()
        y = h - 108
        c.setFont("Helvetica-Bold", 11)
        c.drawString(40, y, f"Agents with activity: {len(report_rows)}")
        c.drawString(220, y, f"Drivers: {grand_drivers}")
        c.drawString(310, y, f"Clients: {grand_clients}")
        c.drawString(400, y, f"Grand Total Due: {_fmt_money(grand_total)}")
        y -= 24

        if not report_rows:
            c.setFont("Helvetica", 10)
            c.drawString(40, y, "No approved weekly registration activity found for this period.")
        for item in report_rows:
            y = ensure_space(y, 120)
            agent = item["agent"]
            c.setFont("Helvetica-Bold", 12)
            c.drawString(40, y, str(agent.get("full_name") or agent.get("username") or agent.get("email") or "Agent")[:70])
            y -= 14
            c.setFont("Helvetica", 9)
            c.drawString(40, y, f"Email: {agent.get('email') or '-'} | ID: {agent.get('id') or '-'} | Town: {agent.get('town') or '-'}")
            y -= 14
            c.drawString(40, y, f"Drivers: {item['driver_count']} | Clients: {item['client_count']} | Base: {_fmt_money(item['base_total'])} | Bonuses: {_fmt_money(item['bonus_total'])} | Total: {_fmt_money(item['agent_total'])}")
            y -= 16

            c.setFont("Helvetica-Bold", 8)
            c.drawString(48, y, "Date")
            c.drawString(116, y, "Type")
            c.drawString(170, y, "Name")
            c.drawString(318, y, "Phone")
            c.drawString(400, y, "Town")
            c.drawRightString(w - 42, y, "Due")
            y -= 10
            c.setFont("Helvetica", 8)
            for row in item["rows"][:80]:
                y = ensure_space(y, 60)
                c.drawString(48, y, str(row.get("created_at") or "")[:10])
                c.drawString(116, y, str(row.get("type") or "")[:12])
                c.drawString(170, y, str(row.get("name") or "")[:28])
                c.drawString(318, y, str(row.get("phone") or "")[:16])
                c.drawString(400, y, str(row.get("town") or "")[:18])
                c.drawRightString(w - 42, y, _fmt_money(row.get("payment_due")))
                y -= 10

            if len(item["rows"]) > 80:
                c.drawString(48, y, f"... {len(item['rows']) - 80} more rows not shown in detail")
                y -= 10

            y -= 4
            c.setFont("Helvetica-Bold", 8)
            c.drawString(48, y, "Daily totals")
            y -= 10
            c.setFont("Helvetica", 8)
            for day, values in sorted(item["daily"].items()):
                y = ensure_space(y, 50)
                c.drawString(58, y, f"{day}: drivers {values['drivers']} | clients {values['clients']} | base {_fmt_money(values['amount'])}")
                y -= 10
            for line in item["bonus_lines"]:
                y = ensure_space(y, 50)
                c.drawString(58, y, line[:100])
                y -= 10
            y -= 12
            if y < 90:
                c.showPage()
                draw_header()
                y = h - 108

        c.save()
        buf.seek(0)
        report_name = f"yene_weekly_report_{start.isoformat()}.pdf" if not selected_agent_id else f"yene_agent_{selected_agent_id}_{start.isoformat()}.pdf"
        return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=report_name)

    @app.get("/api/admin/agent_center_pdf/<agent_id>")
    def admin_agent_center_pdf(agent_id):
        agent = _agent_by_id(agent_id) or {}
        mode, date_from, date_to = _period_from_request()
        rows = _agent_registration_rows(agent_id, date_from, date_to)
        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        w, h = A4
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, h - 50, "YENE Agent Center Report")
        c.setFont("Helvetica", 10)
        c.drawString(50, h - 70, f"Agent: {agent.get('full_name') or agent.get('email') or agent_id}")
        c.drawString(50, h - 85, f"Mode: {mode}  Range: {date_from or 'all'} to {date_to or 'all'}")
        y = h - 115
        for row in rows[:80]:
            c.drawString(50, y, f"{row.get('created_at') or ''} {row.get('type')}: {row.get('name') or ''} {row.get('phone') or ''}"[:115])
            y -= 14
            if y < 60:
                c.showPage()
                y = h - 50
        c.save()
        buf.seek(0)
        return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name=f"yene_agent_{agent_id}_report.pdf")
