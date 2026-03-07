set -euo pipefail

python3 - <<'PY'
from pathlib import Path

p = Path("app.py")
s = p.read_text(encoding="utf-8")

if "### === AGENT V4 UPGRADE ===" not in s:
    block = r'''

### === AGENT V4 UPGRADE ===
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from flask import send_file
import tempfile

def _v4_monday_from_string(s):
    return datetime.date.fromisoformat(s)

def _v4_group_week_rows(rows):
    grouped = {}
    for x in rows:
        ws = x.get("week_start") or "NO_WEEK"
        we = x.get("week_end") or "NO_WEEK"
        key = f"{ws}|{we}"
        grouped.setdefault(key, {
            "week_start": ws,
            "week_end": we,
            "credits": 0.0,
            "debits": 0.0,
            "net": 0.0,
            "items": []
        })
        amt = float(x.get("amount") or 0)
        if x.get("entry_type") == "credit":
            grouped[key]["credits"] += amt
            grouped[key]["net"] += amt
        else:
            grouped[key]["debits"] += amt
            grouped[key]["net"] -= amt
        grouped[key]["items"].append(x)
    out = list(grouped.values())
    out.sort(key=lambda z: z["week_start"] or "", reverse=True)
    return out

@app.get("/api/agent/weekly_breakdown_v4")
def api_agent_weekly_breakdown_v4():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")

    r = requests.get(
        _fa_rest(f"/agent_registrations?select=created_at,subject_type&agent_auth_id=eq.{uid}&order=created_at.desc&limit=1000"),
        headers=_fa_headers(),
        timeout=15
    )
    rows = r.json() if r.status_code == 200 else []

    weeks = {}
    for x in rows:
        created = x.get("created_at")
        if not created:
            continue
        d = datetime.date.fromisoformat(created[:10])
        monday = d - datetime.timedelta(days=d.weekday())
        sunday = monday + datetime.timedelta(days=6)
        key = monday.isoformat()
        weeks.setdefault(key, {
            "week_start": monday.isoformat(),
            "week_end": sunday.isoformat(),
            "drivers": 0,
            "clients": 0
        })
        if x.get("subject_type") == "driver":
            weeks[key]["drivers"] += 1
        elif x.get("subject_type") == "client":
            weeks[key]["clients"] += 1

    out = list(weeks.values())
    out.sort(key=lambda x: x["week_start"], reverse=True)
    return jsonify({"ok": True, "rows": out})

@app.get("/api/agent/invoices_v4")
def api_agent_invoices_v4():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    uid = user.get("id")

    r = requests.get(
        _fa_rest(f"/agent_wallet_ledger?select=week_start,week_end,entry_type,amount,reference,note,created_at&agent_auth_id=eq.{uid}&order=week_start.desc,created_at.desc&limit=500"),
        headers=_fa_headers(),
        timeout=15
    )
    rows = r.json() if r.status_code == 200 else []
    return jsonify({"ok": True, "rows": _v4_group_week_rows(rows)})

@app.get("/api/agent/invoice_pdf_v4")
def api_agent_invoice_pdf_v4():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _fa_find_profile(uid, email)
    week_start = (request.args.get("week_start") or "").strip()

    if not week_start:
        return jsonify({"ok": False, "error": "Missing week_start"}), 400

    r = requests.get(
        _fa_rest(f"/agent_wallet_ledger?select=created_at,week_start,week_end,entry_type,amount,reference,note&agent_auth_id=eq.{uid}&week_start=eq.{week_start}&order=created_at.asc"),
        headers=_fa_headers(),
        timeout=15
    )
    rows = r.json() if r.status_code == 200 else []

    if rows:
        week_end = rows[0].get("week_end") or ""
    else:
        monday = _v4_monday_from_string(week_start)
        week_end = (monday + datetime.timedelta(days=6)).isoformat()

    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    agent_name = (prof or {}).get("full_name") or (prof or {}).get("username") or email or "Agent"
    group_name = (prof or {}).get("town") or "Single Agent"
    referral_code = (prof or {}).get("referral_code") or "-"

    credits = 0.0
    debits = 0.0
    net = 0.0
    for x in rows:
        amt = float(x.get("amount") or 0)
        if x.get("entry_type") == "credit":
            credits += amt
            net += amt
        else:
            debits += amt
            net -= amt

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    c = canvas.Canvas(tmp.name, pagesize=A4)
    width, height = A4

    y = height - 50
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, y, "YENE INVOICE")
    y -= 22

    c.setFont("Helvetica", 10)
    c.drawString(40, y, f"Generated: {generated_at}")
    y -= 18
    c.drawString(40, y, f"Agent Name: {agent_name}")
    y -= 18
    c.drawString(40, y, f"Group: {group_name}")
    y -= 18
    c.drawString(40, y, f"Referral Code: {referral_code}")
    y -= 18
    c.drawString(40, y, f"Week: {week_start} -> {week_end}")
    y -= 30

    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, "Created")
    c.drawString(150, y, "Type")
    c.drawString(240, y, "Amount")
    c.drawString(330, y, "Reference")
    c.drawString(470, y, "Note")
    y -= 16

    c.setFont("Helvetica", 10)
    for x in rows[:40]:
        if y < 70:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica", 10)
        c.drawString(40, y, str(x.get("created_at") or "")[:16])
        c.drawString(150, y, str(x.get("entry_type") or ""))
        c.drawString(240, y, f"{float(x.get('amount') or 0):.2f}")
        c.drawString(330, y, str(x.get("reference") or "")[:20])
        c.drawString(470, y, str(x.get("note") or "")[:18])
        y -= 14

    y -= 18
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, f"Credits: {credits:.2f}")
    y -= 16
    c.drawString(40, y, f"Debits: {debits:.2f}")
    y -= 16
    c.drawString(40, y, f"Net: {net:.2f}")

    c.save()

    filename = f"yene_invoice_{agent_name.replace(' ','_')}_{week_start}.pdf"
    return send_file(tmp.name, as_attachment=True, download_name=filename, mimetype="application/pdf")

@app.get("/api/agent/team_v4")
def api_agent_team_v4():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    uid = user.get("id")
    email = user.get("email")
    prof = _fa_find_profile(uid, email)
    if not prof:
        return jsonify({"ok": True, "rows": [], "referral_code": None})

    referral_code = (prof.get("referral_code") or "").strip()
    if not referral_code:
        return jsonify({"ok": True, "rows": [], "referral_code": ""})

    r = requests.get(
        _fa_rest(f"/agent_profiles?select=full_name,email,phone,role,created_at,referred_by_code&referred_by_code=eq.{referral_code}&order=created_at.desc&limit=200"),
        headers=_fa_headers(),
        timeout=15
    )
    rows = r.json() if r.status_code == 200 else []
    return jsonify({"ok": True, "referral_code": referral_code, "rows": rows})
'''
    if "if __name__" in s:
        s = s.replace("if __name__", block + "\n\nif __name__", 1)
    else:
        s += "\n" + block

p.write_text(s, encoding="utf-8")
print("✅ app.py upgraded to V4")
PY

python3 - <<'PY'
from pathlib import Path

p = Path("templates/agent_dashboard.html")
s = p.read_text(encoding="utf-8")

# replace v3 invoice route names with v4
s = s.replace("/api/agent/invoices_v3", "/api/agent/invoices_v4")
s = s.replace("/api/agent/invoice_csv_v3?week_start=", "/api/agent/invoice_pdf_v4?week_start=")

# add team + weekly history sections if not already there
if 'id="weeklyHistoryBody"' not in s:
    insert_html = """
  <div class="card">
    <h3 style="margin:0 0 10px">Weekly history</h3>
    <table>
      <thead><tr><th>Week</th><th>Drivers</th><th>Clients</th></tr></thead>
      <tbody id="weeklyHistoryBody"><tr><td colspan="3" class="muted">Loading…</td></tr></tbody>
    </table>
  </div>

  <div class="card">
    <h3 style="margin:0 0 10px">Referral team</h3>
    <div class="muted" id="referralCodeText">Referral code: -</div>
    <table>
      <thead><tr><th>Name</th><th>Email</th><th>Phone</th><th>Role</th><th>Joined</th></tr></thead>
      <tbody id="teamBody"><tr><td colspan="5" class="muted">Loading…</td></tr></tbody>
    </table>
  </div>
"""
    s = s.replace("</div>\n\n<script>", insert_html + "\n\n<script>", 1)

# patch invoice download text
s = s.replace("Download CSV", "Download PDF")

# inject loaders if missing
if "weekly_breakdown_v4" not in s:
    inject = """
    const weeks = await api("/api/agent/weekly_breakdown_v4");
    const wh = document.getElementById("weeklyHistoryBody");
    if(wh){
      wh.innerHTML = "";
      const rows = (weeks && weeks.ok) ? weeks.rows || [] : [];
      if(rows.length === 0){
        wh.innerHTML = '<tr><td colspan="3" class="muted">No week history yet.</td></tr>';
      } else {
        for(const x of rows){
          wh.insertAdjacentHTML("beforeend",
            `<tr><td>${x.week_start} → ${x.week_end}</td><td>${x.drivers}</td><td>${x.clients}</td></tr>`
          );
        }
      }
    }

    const team = await api("/api/agent/team_v4");
    const teamBody = document.getElementById("teamBody");
    const refText = document.getElementById("referralCodeText");
    if(refText && team && team.ok){
      refText.textContent = "Referral code: " + (team.referral_code || "-");
    }
    if(teamBody){
      teamBody.innerHTML = "";
      const rows = (team && team.ok) ? team.rows || [] : [];
      if(rows.length === 0){
        teamBody.innerHTML = '<tr><td colspan="5" class="muted">No team members yet.</td></tr>';
      } else {
        for(const x of rows){
          teamBody.insertAdjacentHTML("beforeend",
            `<tr>
              <td>${x.full_name||""}</td>
              <td>${x.email||""}</td>
              <td>${x.phone||""}</td>
              <td>${x.role||""}</td>
              <td>${x.created_at ? new Date(x.created_at).toLocaleString() : ""}</td>
            </tr>`
          );
        }
      }
    }
"""
    s = s.replace("  await loadAll();", inject + "\n  await loadAll();")

p.write_text(s, encoding="utf-8")
print("✅ agent_dashboard.html upgraded to V4")
PY

python3 -m py_compile app.py && echo "✅ app.py compiles"

git add app.py templates/agent_dashboard.html
git commit -m "Agent V4: weekly history, PDF invoices, referral team view" || true
git push origin main
