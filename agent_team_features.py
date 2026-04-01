import uuid
import random
import string
from datetime import datetime, timedelta

from flask import jsonify, request, session


def register_agent_team_routes(app, sb_admin):
    def _now_iso():
        return datetime.utcnow().isoformat() + "Z"

    def _email():
        return (session.get("email") or "").strip().lower()

    def _role():
        return (session.get("role") or "").strip().upper()

    def _unauth():
        return jsonify({"ok": False, "error": "Unauthorized. Please log in again."}), 401

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
        except Exception:
            return None

    def _safe_int(v, default=0):
        try:
            return int(v or 0)
        except Exception:
            return default

    def _safe_float(v, default=0.0):
        try:
            return float(v or 0)
        except Exception:
            return default

    def _approved(row):
        status = str(row.get("status") or "").upper()
        admin_approved = row.get("admin_approved")
        if admin_approved is True:
            return True
        return status in ("ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED")

    def _week_bounds():
        today = datetime.utcnow().date()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return monday, sunday

    def _within_current_week(created_at):
        if not created_at:
            return False
        try:
            d = datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).date()
            monday, sunday = _week_bounds()
            return monday <= d <= sunday
        except Exception:
            return False

    def _agent_profile_by_email(email):
        rows = _safe_select("agent_profiles", {"email": email}, "*", 1)
        if rows:
            return rows[0], "agent_profiles"
        rows = _safe_select("agents", {"email": email}, "*", 1)
        if rows:
            return rows[0], "agents"
        return None, None

    def _agent_profile_by_id(agent_id):
        rows = _safe_select("agent_profiles", {"id": agent_id}, "*", 1)
        if rows:
            return rows[0]
        rows = _safe_select("agents", {"id": agent_id}, "*", 1)
        if rows:
            return rows[0]
        return None

    def _current_agent():
        email = _email()
        if not email:
            return None, None
        return _agent_profile_by_email(email)

    def _make_referral_code(full_name, phone):
        base = "".join(ch for ch in (full_name or "AGENT").upper() if ch.isalnum())[:5] or "AGENT"
        tail = "".join(ch for ch in str(phone or "") if ch.isdigit())[-4:] or "".join(random.choices(string.digits, k=4))
        return f"YENE-{base}-{tail}"

    def _make_temp_password():
        return "Yene@" + "".join(random.choices(string.digits, k=6))

    def _ensure_agent_referral_code(agent):
        if not agent:
            return ""
        code = (agent.get("referral_code") or "").strip()
        if code:
            return code
        code = _make_referral_code(agent.get("full_name") or agent.get("username") or "AGENT", agent.get("phone") or agent.get("phone_number"))
        _safe_update("agent_profiles", {"id": agent.get("id")}, {"referral_code": code})
        return code

    def _team_members(leader):
        leader_id = str(leader.get("id") or "")
        leader_code = _ensure_agent_referral_code(leader)
        rows = _safe_select("agent_profiles", {}, "*", 5000)
        out = []
        for r in rows:
            if str(r.get("id") or "") == leader_id:
                continue
            if str(r.get("team_leader_id") or "") == leader_id:
                out.append(r)
                continue
            if str(r.get("referred_by") or "") == leader_id:
                out.append(r)
                continue
            if leader_code and str(r.get("referred_by_code") or "").strip() == leader_code:
                out.append(r)
                continue
        out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return out

    def _wallet_for_agent(agent):
        agent_id = str(agent.get("id") or "")
        email = str(agent.get("email") or "")
        auth_id = str(agent.get("auth_id") or "")

        rows = _safe_select("agent_wallet_admin_view", {"agent_id": agent_id}, "*", 1)
        if rows:
            row = rows[0]
            bal = _safe_float(row.get("wallet_balance"))
            return {"source": "agent_wallet_admin_view", "available": bal, "pending": 0.0, "lifetime": bal}

        rows = _safe_select("agent_wallets", {"agent_id": agent_id}, "*", 1)
        if rows:
            row = rows[0]
            available = _safe_float(row.get("available"))
            pending = _safe_float(row.get("pending"))
            return {"source": "agent_wallets", "available": available, "pending": pending, "lifetime": available + pending}

        rows = _safe_select("agent_wallet_ledger", {}, "*", 5000)
        available = 0.0
        pending = 0.0
        for x in rows:
            if str(x.get("agent_id") or "") == agent_id or str(x.get("agent_auth_id") or "") == auth_id or str(x.get("agent_email") or "") == email:
                amt = _safe_float(x.get("amount"))
                status = str(x.get("status") or "").upper()
                entry_type = str(x.get("entry_type") or "").lower()
                signed = amt if entry_type == "credit" else -amt
                if status in ("PENDING", "HOLD"):
                    pending += signed
                else:
                    available += signed
        if available or pending:
            return {"source": "agent_wallet_ledger", "available": round(available, 2), "pending": round(pending, 2), "lifetime": round(available + pending, 2)}

        drivers = _safe_select("drivers", {"recruiter_agent_id": agent_id}, "*", 5000)
        clients = _safe_select("clients", {"recruiter_agent_id": agent_id}, "*", 5000)
        approved_driver_count = len([x for x in drivers if _approved(x)])
        approved_client_count = len([x for x in clients if _approved(x)])

        rules = _safe_select("payment_rules", {}, "*", 200)
        driver_rate = 0.0
        client_rate = 0.0
        for r in rules:
            driver_rate = max(driver_rate, _safe_float(r.get("driver_reg")))
            client_rate = max(client_rate, _safe_float(r.get("client_reg")))

        available = round((approved_driver_count * driver_rate) + (approved_client_count * client_rate), 2)
        return {"source": "computed_approved_only", "available": available, "pending": 0.0, "lifetime": available}

    @app.get("/api/agent/hub/me")
    def hub_me():
        if _role() != "AGENT":
            return _unauth()
        me, source = _current_agent()
        if not me:
            return jsonify({"ok": False, "error": "Agent profile not found for this email."}), 404
        leader = None
        leader_id = me.get("team_leader_id") or me.get("referred_by")
        if leader_id:
            leader = _agent_profile_by_id(leader_id)
        me["referral_code"] = _ensure_agent_referral_code(me)
        return jsonify({"ok": True, "source": source, "me": me, "leader": leader})

    @app.get("/api/agent/hub/summary")
    def hub_summary():
        if _role() != "AGENT":
            return _unauth()
        me, _ = _current_agent()
        if not me:
            return jsonify({"ok": False, "error": "Agent profile not found."}), 404

        team = _team_members(me)
        ids = {str(me.get("id") or ""), str(me.get("auth_id") or "")}
        for m in team:
            ids.add(str(m.get("id") or ""))
            ids.add(str(m.get("auth_id") or ""))

        drivers = _safe_select("drivers", {}, "*", 6000)
        clients = _safe_select("clients", {}, "*", 6000)
        trips = _safe_select("agent_driver_trip_updates", {}, "*", 6000)

        team_drivers = [d for d in drivers if str(d.get("recruiter_agent_id") or "") in ids]
        team_clients = [c for c in clients if str(c.get("recruiter_agent_id") or "") in ids]
        team_trips = 0
        for t in trips:
            if str(t.get("agent_id") or t.get("agent_auth_id") or "") in ids:
                team_trips += _safe_int(t.get("trips"))

        active_team = [x for x in team if str(x.get("status") or "").upper() == "ACTIVE"]
        pending_team = [x for x in team if str(x.get("status") or "").upper() in ("PENDING", "PENDING_APPROVAL")]

        return jsonify({
            "ok": True,
            "summary": {
                "team_agents_total": len(team),
                "team_agents_active": len(active_team),
                "team_agents_pending": len(pending_team),
                "team_drivers_total": len(team_drivers),
                "team_clients_total": len(team_clients),
                "team_trip_updates_total": team_trips
            }
        })

    @app.get("/api/agent/hub/team")
    def hub_team():
        if _role() != "AGENT":
            return _unauth()
        me, _ = _current_agent()
        if not me:
            return jsonify({"ok": False, "error": "Agent profile not found."}), 404
        return jsonify({"ok": True, "rows": _team_members(me)})

    @app.get("/api/agent/hub/recent")
    def hub_recent():
        if _role() != "AGENT":
            return _unauth()
        me, _ = _current_agent()
        if not me:
            return jsonify({"ok": False, "error": "Agent profile not found."}), 404

        my_id = str(me.get("id") or "")
        drivers = _safe_select("drivers", {"recruiter_agent_id": my_id}, "*", 20, "created_at", True)
        clients = _safe_select("clients", {"recruiter_agent_id": my_id}, "*", 20, "created_at", True)

        rows = []
        for d in drivers:
            rows.append({"type": "driver", "name": d.get("full_name"), "phone": d.get("phone") or d.get("phone_number"), "status": d.get("status"), "created_at": d.get("created_at")})
        for c in clients:
            rows.append({"type": "client", "name": c.get("full_name"), "phone": c.get("phone") or c.get("phone_number"), "status": c.get("status"), "created_at": c.get("created_at")})
        rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return jsonify({"ok": True, "rows": rows[:12]})

    @app.get("/api/agent/hub/pending")
    def hub_pending():
        if _role() != "AGENT":
            return _unauth()
        me, _ = _current_agent()
        if not me:
            return jsonify({"ok": False, "error": "Agent profile not found."}), 404

        my_id = str(me.get("id") or "")
        drivers = _safe_select("drivers", {"recruiter_agent_id": my_id}, "*", 100)
        clients = _safe_select("clients", {"recruiter_agent_id": my_id}, "*", 100)
        team = _team_members(me)

        pending_drivers = [x for x in drivers if not _approved(x)]
        pending_clients = [x for x in clients if not _approved(x)]
        pending_agents = [x for x in team if str(x.get("status") or "").upper() in ("PENDING", "PENDING_APPROVAL")]

        return jsonify({
            "ok": True,
            "counts": {"drivers": len(pending_drivers), "clients": len(pending_clients), "agents": len(pending_agents)}
        })

    @app.get("/api/agent/hub/trips")
    def hub_trips():
        if _role() != "AGENT":
            return _unauth()
        me, _ = _current_agent()
        if not me:
            return jsonify({"ok": False, "error": "Agent profile not found."}), 404

        team = _team_members(me)
        ids = {str(me.get("id") or ""), str(me.get("auth_id") or "")}
        for m in team:
            ids.add(str(m.get("id") or ""))
            ids.add(str(m.get("auth_id") or ""))

        rows = _safe_select("agent_driver_trip_updates", {}, "*", 5000)
        out = []
        for r in rows:
            if str(r.get("agent_id") or r.get("agent_auth_id") or "") in ids:
                out.append(r)
        out.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)

        monday, sunday = _week_bounds()
        return jsonify({
            "ok": True,
            "week": {"start": monday.isoformat(), "end": sunday.isoformat()},
            "totals": {
                "trips": sum(_safe_int(x.get("trips")) for x in out),
                "bonus_amount": round(sum(_safe_float(x.get("bonus_amount")) for x in out), 2)
            },
            "rows": out[:200]
        })

    @app.get("/api/agent/hub/wallet")
    def hub_wallet():
        if _role() != "AGENT":
            return _unauth()
        me, _ = _current_agent()
        if not me:
            return jsonify({"ok": False, "error": "Agent profile not found."}), 404
        return jsonify({"ok": True, "wallet": _wallet_for_agent(me)})

    @app.get("/api/agent/hub/leaderboard")
    def hub_leaderboard():
        if _role() != "AGENT":
            return _unauth()

        agents = _safe_select("agent_profiles", {}, "*", 5000)
        drivers = _safe_select("drivers", {}, "*", 10000)
        clients = _safe_select("clients", {}, "*", 10000)

        by_agent_drivers = {}
        by_agent_clients = {}
        for d in drivers:
            aid = str(d.get("recruiter_agent_id") or "")
            if aid:
                by_agent_drivers[aid] = by_agent_drivers.get(aid, 0) + 1
        for c in clients:
            aid = str(c.get("recruiter_agent_id") or "")
            if aid:
                by_agent_clients[aid] = by_agent_clients.get(aid, 0) + 1

        rows = []
        for a in agents:
            aid = str(a.get("id") or "")
            dcount = by_agent_drivers.get(aid, 0)
            ccount = by_agent_clients.get(aid, 0)
            if not (dcount >= 5 or ccount >= 5):
                continue
            score = (dcount * 3) + (ccount * 2)
            rows.append({
                "agent_id": aid,
                "name": a.get("full_name") or a.get("username") or a.get("email"),
                "drivers": dcount,
                "clients": ccount,
                "score": score,
            })
        rows.sort(key=lambda x: (x["score"], x["drivers"], x["clients"]), reverse=True)
        for i, r in enumerate(rows, start=1):
            r["rank"] = i

        weekly_agent_map = {}
        for d in drivers:
            if _within_current_week(d.get("created_at")):
                aid = str(d.get("recruiter_agent_id") or "")
                if aid:
                    weekly_agent_map[aid] = weekly_agent_map.get(aid, 0) + 1
        for c in clients:
            if _within_current_week(c.get("created_at")):
                aid = str(c.get("recruiter_agent_id") or "")
                if aid:
                    weekly_agent_map[aid] = weekly_agent_map.get(aid, 0) + 1

        weekly_top_agent = None
        if weekly_agent_map:
            best_id = max(weekly_agent_map, key=lambda k: weekly_agent_map[k])
            prof = _agent_profile_by_id(best_id)
            weekly_top_agent = {
                "name": (prof or {}).get("full_name") or (prof or {}).get("username") or (prof or {}).get("email") or "Unknown",
                "registrations": weekly_agent_map[best_id]
            }

        leader_scores = {}
        for leader in agents:
            members = _team_members(leader)
            member_ids = {str(m.get("id") or "") for m in members}
            total = 0
            for d in drivers:
                if str(d.get("recruiter_agent_id") or "") in member_ids and _within_current_week(d.get("created_at")):
                    total += 1
            for c in clients:
                if str(c.get("recruiter_agent_id") or "") in member_ids and _within_current_week(c.get("created_at")):
                    total += 1
            if total > 0:
                leader_scores[str(leader.get("id") or "")] = total

        weekly_top_team_leader = None
        if leader_scores:
            best_leader_id = max(leader_scores, key=lambda k: leader_scores[k])
            prof = _agent_profile_by_id(best_leader_id)
            weekly_top_team_leader = {
                "name": (prof or {}).get("full_name") or (prof or {}).get("username") or (prof or {}).get("email") or "Unknown",
                "team_registrations": leader_scores[best_leader_id]
            }

        monday, sunday = _week_bounds()
        return jsonify({
            "ok": True,
            "week": {"start": monday.isoformat(), "end": sunday.isoformat()},
            "weekly_top_agent": weekly_top_agent,
            "weekly_top_team_leader": weekly_top_team_leader,
            "rows": rows[:100]
        })

    @app.post("/api/agent/hub/register-agent")
    def hub_register_agent():
        if _role() != "AGENT":
            return _unauth()

        leader, _ = _current_agent()
        if not leader:
            return jsonify({"ok": False, "error": "Agent profile not found."}), 404

        leader_code = _ensure_agent_referral_code(leader)
        data = request.get_json(force=True) or {}
        full_name = (data.get("full_name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        phone = (data.get("phone") or "").strip()
        username = (data.get("username") or (email.split("@")[0] if email else "")).strip().lower()
        town = (data.get("town") or "").strip() or "PENDING"
        region = (data.get("region") or "").strip() or "PENDING"
        gender = (data.get("gender") or "Not Specified").strip()
        national_id = (data.get("national_id") or email or "PENDING").strip()

        if not full_name or not email or not phone:
            return jsonify({"ok": False, "error": "full_name, email, and phone are required"}), 400

        exists = _safe_select("agent_profiles", {"email": email}, "id,email", 1)
        if not exists:
            exists = _safe_select("agents", {"email": email}, "id,email", 1)
        if exists:
            return jsonify({"ok": False, "error": "Agent email already exists"}), 400

        referral_code = _make_referral_code(full_name, phone)
        temp_password = _make_temp_password()

        auth_user = None
        auth_error = None
        try:
            auth_res = sb_admin.auth.admin.create_user({
                "email": email,
                "password": temp_password,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name}
            })
            auth_user = getattr(auth_res, "user", None)
        except Exception as e:
            auth_error = str(e)

        auth_id = getattr(auth_user, "id", None)
        new_id = str(uuid.uuid4())

        payload = {
            "id": new_id,
            "auth_id": auth_id,
            "user_id": auth_id,
            "full_name": full_name,
            "username": username,
            "email": email,
            "phone": phone,
            "phone_number": phone,
            "gender": gender,
            "national_id": national_id,
            "address": "PENDING",
            "town": town,
            "region": region,
            "profile_pic_path": "none",
            "id_document_path": "none",
            "role": "AGENT",
            "status": "PENDING_APPROVAL",
            "auth_method": "password",
            "team_leader_id": leader.get("id"),
            "team_leader_name": leader.get("full_name") or leader.get("username") or leader.get("email"),
            "referred_by": leader.get("id"),
            "referred_by_code": leader_code,
            "referral_code": referral_code,
            "created_at": _now_iso(),
        }

        res = _safe_insert("agent_profiles", payload)
        if isinstance(res, Exception):
            fallback = dict(payload)
            for k in ["team_leader_id", "team_leader_name", "referred_by_code", "referral_code", "auth_id", "user_id", "created_at"]:
                fallback.pop(k, None)
            res = _safe_insert("agent_profiles", fallback)
            if isinstance(res, Exception):
                return jsonify({"ok": False, "error": f"agent_profiles insert failed: {str(res)}", "auth_error": auth_error}), 500

        try:
            sb_admin.table("agents").insert({
                "id": new_id,
                "auth_id": auth_id,
                "full_name": full_name,
                "username": username,
                "phone": phone,
                "national_id": national_id,
                "region": region,
                "town": town,
                "status": "PENDING_APPROVAL",
                "role": "AGENT",
                "gender": gender,
                "address": "PENDING",
                "profile_pic_path": "none",
                "id_document_path": "none",
                "email": email,
                "referred_by_id": leader.get("id"),
                "created_at": _now_iso(),
            }).execute()
        except Exception:
            pass

        try:
            sb_admin.table("agent_referrals").insert({
                "parent_agent_id": leader.get("id"),
                "parent_agent_email": leader.get("email"),
                "child_agent_id": new_id,
                "child_agent_email": email,
                "child_agent_name": full_name
            }).execute()
        except Exception:
            pass

        return jsonify({"ok": True, "temp_password": temp_password, "referral_code": referral_code, "auth_error": auth_error})

    @app.post("/api/agent/hub/register-driver")
    def hub_register_driver():
        if _role() != "AGENT":
            return _unauth()
        me, _ = _current_agent()
        if not me:
            return jsonify({"ok": False, "error": "Agent profile not found."}), 404

        data = request.get_json(force=True) or {}
        full_name = (data.get("full_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        car_type = (data.get("car_type") or "").strip()
        app_code = (data.get("app_code") or "").strip()
        region = (data.get("region") or "").strip()
        town = (data.get("town") or "").strip() or region or "PENDING"

        if not full_name or not phone:
            return jsonify({"ok": False, "error": "full_name and phone are required"}), 400

        exists = _safe_select("drivers", {"phone": phone}, "id,phone", 1)
        if not exists:
            exists = _safe_select("drivers", {"phone_number": phone}, "id,phone_number", 1)
        if exists:
            return jsonify({"ok": False, "error": "Driver phone already exists"}), 400

        payload = {
            "full_name": full_name,
            "phone": phone,
            "phone_number": phone,
            "town": town,
            "status": "PENDING_APPROVAL",
            "recruiter_agent_id": me.get("id"),
            "recruiter_name": me.get("full_name") or me.get("username") or me.get("email"),
            "external_code": app_code,
            "created_at": _now_iso(),
        }
        if car_type:
            payload["car_details"] = car_type
        if region:
            payload["region"] = region
        if app_code:
            payload["license_number"] = app_code

        res = _safe_insert("drivers", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Driver registered and saved with timestamp."})

    @app.post("/api/agent/hub/register-client")
    def hub_register_client():
        if _role() != "AGENT":
            return _unauth()
        me, _ = _current_agent()
        if not me:
            return jsonify({"ok": False, "error": "Agent profile not found."}), 404

        data = request.get_json(force=True) or {}
        full_name = (data.get("full_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        app_code = (data.get("app_code") or "").strip()
        region = (data.get("region") or "").strip()
        town = (data.get("town") or "").strip() or region or "PENDING"

        if not full_name or not phone:
            return jsonify({"ok": False, "error": "full_name and phone are required"}), 400

        exists = _safe_select("clients", {"phone": phone}, "id,phone", 1)
        if not exists:
            exists = _safe_select("clients", {"phone_number": phone}, "id,phone_number", 1)
        if exists:
            return jsonify({"ok": False, "error": "Client phone already exists"}), 400

        payload = {
            "full_name": full_name,
            "phone": phone,
            "phone_number": phone,
            "yene_code": app_code or "PENDING",
            "status": "PENDING_APPROVAL",
            "recruiter_agent_id": me.get("id"),
            "recruiter_name": me.get("full_name") or me.get("username") or me.get("email"),
            "external_code": app_code,
            "created_at": _now_iso(),
        }
        if region:
            payload["region"] = region
        if town:
            payload["town"] = town

        res = _safe_insert("clients", payload)
        if isinstance(res, Exception):
            return jsonify({"ok": False, "error": str(res)}), 500

        return jsonify({"ok": True, "message": "Client registered and saved with timestamp."})
