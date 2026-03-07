set -euo pipefail

python3 - <<'PY'
from pathlib import Path
import re

p = Path("app.py")
s = p.read_text(encoding="utf-8")

# 1) Replace loose profile lookup with STRICT auth_id lookup
strict_fn = r'''
def _strict_find_agent_profile(uid):
    if not uid:
        return None
    q = f"/agent_profiles?select=*&auth_id=eq.{uid}&limit=1"
    r = requests.get(_fa_rest(q), headers=_fa_headers(), timeout=10)
    if r.status_code == 200 and r.json():
        return r.json()[0]
    return None
'''

# add function if missing
if "def _strict_find_agent_profile(uid):" not in s:
    insert_after = "def _fa_find_profile(uid=None, email=None):"
    idx = s.find(insert_after)
    if idx != -1:
        # place after existing function block by simple append near it
        end_idx = s.find("\n\n", idx)
        if end_idx == -1:
            end_idx = idx
        s = s[:end_idx] + "\n\n" + strict_fn + s[end_idx:]
    else:
        s += "\n\n" + strict_fn + "\n"

# 2) Force V3 routes to use strict auth_id only
repls = [
    ("prof = _fa_find_profile(uid, email)", "prof = _strict_find_agent_profile(uid)"),
    ("prof = _fa_find_profile(uid, email)\n    if not prof:\n        return jsonify({\"ok\": True, \"drivers_week\": 0, \"clients_week\": 0, \"drivers_all\": 0, \"profile\": None})",
     "prof = _strict_find_agent_profile(uid)\n    if not prof:\n        return jsonify({\"ok\": True, \"drivers_week\": 0, \"clients_week\": 0, \"drivers_all\": 0, \"profile\": None})"),
]
for old, new in repls:
    s = s.replace(old, new)

# 3) Add a strict helper route so dashboard can confirm the logged-in agent profile
if '/api/agent/whoami_strict' not in s:
    block = r'''
@app.get("/api/agent/whoami_strict")
def api_agent_whoami_strict():
    user = _fa_verify_bearer()
    if not user:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    uid = user.get("id")
    prof = _strict_find_agent_profile(uid)
    return jsonify({
        "ok": True,
        "auth_user_id": uid,
        "profile_found": True if prof else False,
        "profile": prof
    })
'''
    if "if __name__" in s:
        s = s.replace("if __name__", block + "\n\nif __name__", 1)
    else:
        s += "\n" + block + "\n"

p.write_text(s, encoding="utf-8")
print("✅ app.py patched for strict per-agent isolation")
PY

python3 - <<'PY'
from pathlib import Path

p = Path("templates/agent_dashboard.html")
s = p.read_text(encoding="utf-8")

# 4) Make dashboard use strict whoami first
if '/api/agent/whoami_strict' not in s:
    s = s.replace(
        'const me = await api("/api/agent/me_v3");',
        '''const me = await api("/api/agent/whoami_strict");
    if(!me || !me.ok){ 
      document.getElementById("welcome").textContent = "Welcome, Agent";
      alert("Could not verify your personal agent profile.");
      return;
    }
    if(!me.profile_found){
      document.getElementById("welcome").textContent = "Welcome, New Agent";
      document.getElementById("driversWeek").textContent = "0";
      document.getElementById("clientsWeek").textContent = "0";
      document.getElementById("driversAll").textContent = "0";
      document.getElementById("walletBalance").textContent = "0.00";
      document.getElementById("weekLabel").textContent = "No data yet. Waiting for your first registrations.";
      const ab = document.getElementById("activityBody");
      if(ab) ab.innerHTML = '<tr><td colspan="6" class="muted">No registrations yet.</td></tr>';
      const ib = document.getElementById("invoiceBody");
      if(ib) ib.innerHTML = '<tr><td colspan="5" class="muted">No invoices yet.</td></tr>';
      const db = document.getElementById("driversMonitorBody");
      if(db) db.innerHTML = '<tr><td colspan="6" class="muted">No drivers yet.</td></tr>';
      return;
    }

    document.getElementById("welcome").textContent = "Welcome, " + (me.profile?.full_name || me.profile?.username || me.profile?.email || "Agent");

    const profileId = me.profile?.id || "";
    const profileAuthId = me.profile?.auth_id || "";
    if(!profileAuthId){
      console.warn("Profile exists but auth_id missing.");
    }

    const me2 = await api("/api/agent/me_v3");'''
    )

# 5) Avoid overriding welcome text later with mixed data
s = s.replace(
    '''if(me && me.ok){
      const name = me.profile?.full_name || me.profile?.username || me.email || "Agent";
      document.getElementById("welcome").textContent = "Welcome, " + name;
    }''',
    '''if(me2 && me2.ok && me.profile_found){
      const name = me.profile?.full_name || me.profile?.username || me.profile?.email || "Agent";
      document.getElementById("welcome").textContent = "Welcome, " + name;
    }'''
)

p.write_text(s, encoding="utf-8")
print("✅ agent_dashboard.html patched for strict self-only profile loading")
PY

python3 -m py_compile app.py && echo "✅ app.py compiles"

git add app.py templates/agent_dashboard.html
git commit -m "Fix agent data isolation: strict auth_id-only profile loading" || true
git push origin main
