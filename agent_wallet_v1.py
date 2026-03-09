from io import BytesIO
from datetime import datetime, timezone
from flask import jsonify, request, session, send_file
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

def register_agent_wallet_v1_routes(app, sb_admin, require_login):
    UTC = timezone.utc

    def now_utc():
        return datetime.now(UTC)

    def money(v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

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

    def wallet_balance(agent_id):
        try:
            rows = (
                sb_admin.table("agent_wallet_ledger")
                .select("*")
                .eq("agent_id", str(agent_id))
                .eq("status", "approved")
                .execute()
                .data or []
            )
            total = 0.0
            for r in rows:
                amt = money(r.get("amount"))
                if (r.get("txn_type") or "").lower() == "debit":
                    total -= amt
                else:
                    total += amt
            return round(total, 2)
        except Exception:
            return 0.0

    @app.route("/api/agent/wallet_summary_v1", methods=["GET"], endpoint="agent_wallet_summary_v1")
    @require_login("AGENT")
    def agent_wallet_summary_v1():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        balance = wallet_balance(agent.get("id"))

        try:
            pending_requests = (
                sb_admin.table("agent_withdraw_requests")
                .select("*")
                .eq("agent_id", str(agent.get("id")))
                .eq("status", "pending")
                .execute()
                .data or []
            )
        except Exception:
            pending_requests = []

        try:
            approved_requests = (
                sb_admin.table("agent_withdraw_requests")
                .select("*")
                .eq("agent_id", str(agent.get("id")))
                .eq("status", "approved")
                .execute()
                .data or []
            )
        except Exception:
            approved_requests = []

        try:
            sent_requests = (
                sb_admin.table("agent_withdraw_requests")
                .select("*")
                .eq("agent_id", str(agent.get("id")))
                .eq("status", "sent")
                .execute()
                .data or []
            )
        except Exception:
            sent_requests = []

        return jsonify({
            "ok": True,
            "balance": balance,
            "pending_requests": len(pending_requests),
            "approved_requests": len(approved_requests),
            "sent_requests": len(sent_requests),
        })

    @app.route("/api/agent/wallet_history_v1", methods=["GET"], endpoint="agent_wallet_history_v1")
    @require_login("AGENT")
    def agent_wallet_history_v1():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        try:
            rows = (
                sb_admin.table("agent_wallet_ledger")
                .select("*")
                .eq("agent_id", str(agent.get("id")))
                .order("created_at", desc=True)
                .limit(200)
                .execute()
                .data or []
            )
            return jsonify({"ok": True, "rows": rows})
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e),
                "hint": "Run wallet_schema.sql in Supabase SQL Editor first."
            }), 500

    @app.route("/api/agent/withdraw_requests_v1", methods=["GET"], endpoint="agent_withdraw_requests_v1")
    @require_login("AGENT")
    def agent_withdraw_requests_v1():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        try:
            rows = (
                sb_admin.table("agent_withdraw_requests")
                .select("*")
                .eq("agent_id", str(agent.get("id")))
                .order("created_at", desc=True)
                .limit(200)
                .execute()
                .data or []
            )
            return jsonify({"ok": True, "rows": rows})
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e),
                "hint": "Run wallet_schema.sql in Supabase SQL Editor first."
            }), 500

    @app.route("/api/agent/request_withdraw_v1", methods=["POST"], endpoint="agent_request_withdraw_v1")
    @require_login("AGENT")
    def agent_request_withdraw_v1():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        data = request.get_json(silent=True) or {}
        amount = money(data.get("amount"))
        note = (data.get("note") or "").strip()

        if amount <= 0:
            return jsonify({"ok": False, "error": "Enter a valid amount"}), 400

        balance = wallet_balance(agent.get("id"))
        if amount > balance:
            return jsonify({"ok": False, "error": "Requested amount is greater than available balance"}), 400

        try:
            sb_admin.table("agent_withdraw_requests").insert({
                "agent_id": str(agent.get("id")),
                "agent_email": agent.get("email"),
                "agent_name": agent.get("full_name") or agent.get("email"),
                "request_amount": amount,
                "request_note": note,
                "status": "pending",
                "admin_note": ""
            }).execute()
            return jsonify({"ok": True, "success": True})
        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e),
                "hint": "Run wallet_schema.sql in Supabase SQL Editor first."
            }), 500

    @app.route("/api/agent/wallet_invoice_v1/<txn_id>", methods=["GET"], endpoint="agent_wallet_invoice_v1")
    @require_login("AGENT")
    def agent_wallet_invoice_v1(txn_id):
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        try:
            rows = (
                sb_admin.table("agent_wallet_ledger")
                .select("*")
                .eq("id", int(txn_id))
                .eq("agent_id", str(agent.get("id")))
                .limit(1)
                .execute()
                .data or []
            )
            if not rows:
                return jsonify({"ok": False, "error": "Transaction not found"}), 404
            txn = rows[0]
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        w, h = A4

        c.setFont("Helvetica-Bold", 20)
        c.drawString(50, h - 50, "YENE")
        c.setFont("Helvetica", 11)
        c.drawString(50, h - 68, "Agent Wallet Receipt / Invoice")

        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, h - 110, "Receipt Number:")
        c.setFont("Helvetica", 12)
        receipt_no = str(txn.get("reference_no") or f"YENE-RCPT-{txn.get('id')}")
        c.drawString(170, h - 110, receipt_no)

        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, h - 135, "Date:")
        c.setFont("Helvetica", 12)
        c.drawString(170, h - 135, str(txn.get("created_at") or now_utc().isoformat()))

        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, h - 170, "Agent Name:")
        c.setFont("Helvetica", 12)
        c.drawString(170, h - 170, str(agent.get("full_name") or ""))

        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, h - 195, "Agent Email:")
        c.setFont("Helvetica", 12)
        c.drawString(170, h - 195, str(agent.get("email") or ""))

        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, h - 230, "Transaction Type:")
        c.setFont("Helvetica", 12)
        c.drawString(170, h - 230, str(txn.get("txn_type") or ""))

        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, h - 255, "Amount:")
        c.setFont("Helvetica", 12)
        c.drawString(170, h - 255, f"N$ {money(txn.get('amount')):.2f}")

        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, h - 280, "Description:")
        c.setFont("Helvetica", 12)
        c.drawString(170, h - 280, str(txn.get("description") or ""))

        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, h - 305, "Status:")
        c.setFont("Helvetica", 12)
        c.drawString(170, h - 305, str(txn.get("status") or ""))

        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, 80, "Auto-generated by YENE Wallet System")
        c.setFont("Helvetica", 10)
        c.drawString(50, 62, "Namibia remote work and affiliate platform")

        c.showPage()
        c.save()
        buf.seek(0)

        return send_file(
            buf,
            as_attachment=True,
            download_name=f"YENE_Receipt_{receipt_no}.pdf",
            mimetype="application/pdf"
        )
