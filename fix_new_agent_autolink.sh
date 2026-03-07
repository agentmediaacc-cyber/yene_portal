set -euo pipefail

python3 - <<'PY'
from pathlib import Path

p = Path("app.py")
s = p.read_text(encoding="utf-8")

marker = "### === NEW AGENT AUTOLINK FIX ==="
if marker not in s:
    block = r'''

### === NEW AGENT AUTOLINK FIX ===

def _autolink_profile_by_email(uid, email):
    """
    If a newly signed-up agent has an agent_profiles row with matching email
    but missing auth_id, link it automatically.
    """
    if not uid or not email:
        return None

    # 1) already linked?
    q1 = f"/agent_profiles?select=*&auth_id=eq.{uid}&limit=1"
    r1 = requests.get(_fa_rest(q1), headers=_fa_headers(), timeout=10)
    if r1.status_code == 200 and r1.json():
        return r1.json()[0]

    # 2) find by email
    q2 = f"/agent_profiles?select=*&email=eq.{email}&limit=1"
    r2 = requests.get(_fa_rest(q2), headers=_fa_headers(), timeout=10)
    if r2.status_code == 200 and r2.json():
        prof = r2.json()[0]
        pid = prof.get("id")
        if pid:
            patch_payload = {"auth_id": uid, "user_id": uid}
            requests.patch(
                _fa_rest(f"/agent_profiles?id=eq.{pid}"),
                headers=_fa_headers(),
                json=patch_payload,
                timeout=10
            )

            # re-read linked row
            r3 = requests.get(_fa_rest(q1), headers=_fa_headers(), timeout=10)
            if r3.status_code == 200 and r3.json():
                return r3.json()[0]
        return prof

    return None
'''
    s += "\n" + block + "\n"

# Replace strict whoami route to autolink by email on first load
old = """    prof = _strict_find_agent_profile(uid)"""
new = """    prof = _strict_find_agent_profile(uid)
    if not prof:
        prof = _autolink_profile_by_email(uid, user.get("email"))"""
s = s.replace(old, new)

# Also update me_v3 / summary_v3 / activity_v3 / register routes to autolink
s = s.replace(
    'prof = _fa_find_profile(uid, email)',
    'prof = _strict_find_agent_profile(uid)\n    if not prof:\n        prof = _autolink_profile_by_email(uid, email)'
)

p.write_text(s, encoding="utf-8")
print("✅ app.py patched: new agents will auto-link by email on first dashboard load")
PY

python3 -m py_compile app.py && echo "✅ app.py compiles"

git add app.py
git commit -m "Fix new agent signup redirect loop by auto-linking profile with auth_id" || true
git push origin main
