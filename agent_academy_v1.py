from flask import jsonify, request, session

def register_agent_academy_v1_routes(app, sb_admin, require_login):
    def get_agent():
        email = session.get("email")
        if not email:
            return None, "Missing session email"
        try:
            rows = (
                sb_admin.table("agent_profiles")
                .select("id,full_name,email")
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

    @app.route("/api/agent/academy_status_v1", methods=["GET"], endpoint="agent_academy_status_v1")
    @require_login("AGENT")
    def agent_academy_status_v1():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        try:
            rows = (
                sb_admin.table("agent_academy_progress")
                .select("*")
                .eq("agent_id", str(agent.get("id")))
                .execute()
                .data or []
            )
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e),
                "hint": "Run academy_schema.sql in Supabase SQL editor first."
            }), 500

        progress = {}
        for r in rows:
            progress[r.get("module_key")] = {
                "passed": bool(r.get("passed")),
                "score": int(r.get("score") or 0)
            }

        badges = []
        if progress.get("driver_registration", {}).get("passed"):
            badges.append("Driver Registration Certified")
        if progress.get("client_registration", {}).get("passed"):
            badges.append("Client Registration Certified")
        if progress.get("activation", {}).get("passed"):
            badges.append("Activation Specialist")
        if progress.get("marketing_growth", {}).get("passed"):
            badges.append("YENE Growth Specialist")
        if len(badges) == 4:
            badges.append("YENE Master Agent")

        return jsonify({
            "ok": True,
            "agent": agent,
            "progress": progress,
            "badges": badges
        })

    @app.route("/api/agent/academy_pass_v1", methods=["POST"], endpoint="agent_academy_pass_v1")
    @require_login("AGENT")
    def agent_academy_pass_v1():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        data = request.get_json(silent=True) or {}
        module_key = (data.get("module_key") or "").strip()
        score = int(data.get("score") or 0)

        allowed = {
            "driver_registration",
            "client_registration",
            "activation",
            "marketing_growth",
        }

        if module_key not in allowed:
            return jsonify({"ok": False, "error": "Invalid module key"}), 400

        if score < 3:
            return jsonify({"ok": False, "error": "Pass mark not reached"}), 400

        payload = {
            "agent_id": str(agent.get("id")),
            "agent_email": agent.get("email"),
            "agent_name": agent.get("full_name"),
            "module_key": module_key,
            "passed": True,
            "score": score,
        }

        try:
            sb_admin.table("agent_academy_progress").upsert(
                payload,
                on_conflict="agent_id,module_key"
            ).execute()
            return jsonify({"ok": True, "success": True})
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e),
                "hint": "Run academy_schema.sql in Supabase SQL editor first."
            }), 500
