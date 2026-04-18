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

    def debug(route, identity=None, **counts):
        try:
            app.logger.info(
                "agent_wallet route=%s identity=%s counts=%s",
                route,
                identity or {},
                counts,
            )
        except Exception:
            pass

    def identity_values(agent):
        vals = []
        for key in ("id", "auth_id", "user_id", "email"):
            val = str(agent.get(key) or "").strip()
            if val and val not in vals:
                vals.append(val)
        return vals

    def matches_identity(row, values, fields):
        return any(str(row.get(field) or "").strip() in values for field in fields)

    def safe_select(table, limit=10000, order_col="created_at", desc=True):
        try:
            q = sb_admin.table(table).select("*")
            if order_col:
                q = q.order(order_col, desc=desc)
            if limit:
                q = q.limit(limit)
            rows = q.execute().data or []
            debug("safe_select", table=table, rows=len(rows))
            return rows
        except Exception as e:
            if order_col:
                try:
                    q = sb_admin.table(table).select("*")
                    if limit:
                        q = q.limit(limit)
                    rows = q.execute().data or []
                    debug("safe_select_retry_no_order", table=table, rows=len(rows))
                    return rows
                except Exception:
                    pass
            app.logger.warning("Supabase select failed table=%s error=%s", table, e)
            return []

    def agent_rows(table, agent):
        values = identity_values(agent)
        fields = ("agent_id", "agent_auth_id", "user_id", "auth_id", "agent_email", "email")
        return [r for r in safe_select(table) if matches_identity(r, values, fields)]

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

    def wallet_from_wallets(agent):
        rows = agent_rows("agent_wallets", agent)
        if not rows:
            return None
        row = rows[0]
        available = money(row.get("available") or row.get("available_balance") or row.get("balance") or row.get("wallet_balance"))
        pending = money(row.get("pending") or row.get("pending_balance"))
        lifetime = money(row.get("lifetime") or row.get("lifetime_earnings") or row.get("total_earned") or available + pending)
        return {"source": "agent_wallets", "balance": available, "available": available, "pending": pending, "lifetime": lifetime}

    def wallet_from_ledger(agent):
        rows = agent_rows("agent_wallet_ledger", agent)
        available = 0.0
        pending = 0.0
        for r in rows:
            amt = money(r.get("amount"))
            kind = str(r.get("entry_type") or r.get("txn_type") or r.get("type") or "").lower()
            signed = -amt if kind in {"debit", "withdrawal", "payout"} else amt
            status = str(r.get("status") or "").lower()
            if status in {"pending", "hold", "requested"}:
                pending += signed
            else:
                available += signed
        return {"source": "agent_wallet_ledger", "balance": round(available, 2), "available": round(available, 2), "pending": round(pending, 2), "lifetime": round(available + pending, 2)}

    def wallet_summary(agent):
        from_wallets = wallet_from_wallets(agent)
        if from_wallets:
            return from_wallets
        return wallet_from_ledger(agent)

    @app.route("/api/agent/wallet_summary_v1", methods=["GET"], endpoint="agent_wallet_summary_v1")
    @require_login("AGENT")
    def agent_wallet_summary_v1():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        wallet = wallet_summary(agent)
        requests = agent_rows("agent_withdraw_requests", agent)
        pending_requests = [r for r in requests if str(r.get("status") or "").lower() == "pending"]
        approved_requests = [r for r in requests if str(r.get("status") or "").lower() == "approved"]
        sent_requests = [r for r in requests if str(r.get("status") or "").lower() == "sent"]
        debug(
            "agent_wallet_summary_v1",
            {"agent_id": agent.get("id"), "email": agent.get("email")},
            withdraw_requests=len(requests),
            pending=len(pending_requests),
            approved=len(approved_requests),
            sent=len(sent_requests),
        )

        return jsonify({
            "ok": True,
            "summary": wallet,
            "balance": wallet["balance"],
            "available": wallet["available"],
            "pending": wallet["pending"],
            "lifetime": wallet["lifetime"],
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

        rows = agent_rows("agent_wallet_ledger", agent)[:200]
        debug("agent_wallet_history_v1", {"agent_id": agent.get("id")}, agent_wallet_ledger=len(rows))
        return jsonify({"ok": True, "rows": rows})

    @app.route("/api/agent/withdraw_requests_v1", methods=["GET"], endpoint="agent_withdraw_requests_v1")
    @require_login("AGENT")
    def agent_withdraw_requests_v1():
        agent, err = get_agent()
        if err:
            return jsonify({"ok": False, "error": err}), 401

        rows = agent_rows("agent_withdraw_requests", agent)[:200]
        debug("agent_withdraw_requests_v1", {"agent_id": agent.get("id")}, agent_withdraw_requests=len(rows))
        return jsonify({"ok": True, "rows": rows})

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

        balance = wallet_summary(agent)["available"]
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

        rows = [
            r for r in agent_rows("agent_wallet_ledger", agent)
            if str(r.get("id") or "") == str(txn_id)
        ]
        debug("agent_wallet_invoice_v1", {"agent_id": agent.get("id")}, matches=len(rows))
        if not rows:
            return jsonify({"ok": False, "error": "Transaction not found"}), 404
        txn = rows[0]

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
