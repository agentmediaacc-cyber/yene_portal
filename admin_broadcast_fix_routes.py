from datetime import datetime
from flask import jsonify, request


def register_admin_broadcast_fix_routes(app, sb_admin):
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

    def _clean(v):
        return str(v or "").strip()

    def _now():
        return datetime.utcnow().isoformat() + "Z"

    @app.route("/api/admin/broadcasts", methods=["GET", "POST"])
    def admin_broadcasts_fix():
        if request.method == "GET":
            rows = _safe_select("broadcasts", {}, "*", 200, "created_at", True)
            return jsonify({"ok": True, "rows": rows})

        data = request.get_json(force=True) or {}
        title = _clean(data.get("title")) or "Admin Broadcast"
        audience = _clean(data.get("audience")) or "all"
        message = _clean(data.get("message"))

        if not message:
            return jsonify({"ok": False, "error": "message required"}), 400

        payload = {
            "title": title,
            "audience": audience,
            "message": message,
            "created_at": _now(),
        }

        res = _safe_insert("broadcasts", payload)
        if not isinstance(res, Exception):
            return jsonify({"ok": True, "message": "Broadcast sent"})

        # fallback: try smaller schema
        fallback1 = {
            "message": message,
            "created_at": payload["created_at"],
        }
        res2 = _safe_insert("broadcasts", fallback1)
        if not isinstance(res2, Exception):
            return jsonify({"ok": True, "message": "Broadcast sent with fallback schema"})

        # fallback to system_logs
        fallback2 = {
            "event_type": "broadcast",
            "details": f"{title} | {audience} | {message}",
            "created_at": payload["created_at"],
        }
        res3 = _safe_insert("system_logs", fallback2)
        if not isinstance(res3, Exception):
            return jsonify({"ok": True, "message": "Broadcast saved to system logs fallback"})

        return jsonify({"ok": False, "error": f"{res}; fallback1: {res2}; fallback2: {res3}"}), 500

    @app.post("/api/admin/broadcast/town")
    def admin_broadcast_town_fix2():
        data = request.get_json(force=True) or {}
        town = _clean(data.get("town"))
        title = _clean(data.get("title")) or "Town Broadcast"
        message = _clean(data.get("message"))

        if not town:
            return jsonify({"ok": False, "error": "town required"}), 400
        if not message:
            return jsonify({"ok": False, "error": "message required"}), 400

        created_at = _now()

        payload = {
            "title": title,
            "audience": f"town:{town}",
            "message": message,
            "created_at": created_at,
        }

        res = _safe_insert("broadcasts", payload)
        if not isinstance(res, Exception):
            return jsonify({"ok": True, "message": "Town broadcast sent"})

        fallback1 = {
            "message": f"[{town}] {title} - {message}",
            "created_at": created_at,
        }
        res2 = _safe_insert("broadcasts", fallback1)
        if not isinstance(res2, Exception):
            return jsonify({"ok": True, "message": "Town broadcast saved with fallback schema"})

        fallback2 = {
            "event_type": "broadcast_town",
            "details": f"{town} | {title} | {message}",
            "created_at": created_at,
        }
        res3 = _safe_insert("system_logs", fallback2)
        if not isinstance(res3, Exception):
            return jsonify({"ok": True, "message": "Town broadcast saved to system logs fallback"})

        return jsonify({"ok": False, "error": f"{res}; fallback1: {res2}; fallback2: {res3}"}), 500
