#!/usr/bin/env bash
set -euo pipefail
set +H 2>/dev/null || true

echo "==> Patch templates/login.html to ALWAYS use sbReady and redirect after session is stable..."
python3 - <<'PY'
from pathlib import Path
import re

p = Path("templates/login.html")
s = p.read_text(encoding="utf-8")

# 1) ensure we await sbReady at click
# Replace any direct use of window.supabase.auth.signInWithPassword(...) with sb.auth.signInWithPassword(...)
s = re.sub(r'window\.supabase\.auth\.signInWithPassword', 'sb.auth.signInWithPassword', s)

# 2) Make sure handler creates sb = await window.sbReady before signIn
# If already exists, we won't duplicate.
if "const sb = await window.sbReady" not in s:
    # Try to inject near where it reads email/password before sign-in
    # Find signInWithPassword call line, insert sb init just before it.
    m = re.search(r'await\s+sb\.auth\.signInWithPassword\s*\(', s)
    if not m:
        # Maybe it still says window.supabase (if above didn't match due formatting)
        m = re.search(r'await\s+window\.supabase\.auth\.signInWithPassword\s*\(', s)
    if not m:
        raise SystemExit("❌ Can't find signInWithPassword call in templates/login.html. Paste the file if different.")
    insert_at = m.start()
    s = s[:insert_at] + 'const sb = await window.sbReady;\n      if(!sb){ alert("Supabase client not ready. Refresh."); return; }\n      ' + s[insert_at:]

# 3) After successful login, WAIT until session exists before redirect
# We inject a waitForSession helper once.
if "async function waitForSession" not in s:
    helper = r"""
    async function waitForSession(sb, ms=2000){
      const start = Date.now();
      while(Date.now() - start < ms){
        const { data } = await sb.auth.getSession();
        if(data && data.session) return data.session;

        // also wait for auth event
        await new Promise(res=>{
          let done = false;
          const { data: sub } = sb.auth.onAuthStateChange((_e,_s)=>{
            if(!done){ done=true; sub?.subscription?.unsubscribe?.(); res(); }
          });
          setTimeout(()=>{ if(!done){ done=true; sub?.subscription?.unsubscribe?.(); res(); } }, 250);
        });
      }
      return null;
    }
"""
    # Put helper near top of main <script> by inserting after first <script> tag
    s = re.sub(r'(<script[^>]*>\s*)', r'\1' + helper + "\n", s, count=1)

# 4) Replace any immediate redirect after sign-in with role-based redirect that waits session
# Remove old hardcoded window.location.href lines
s = re.sub(r'window\.location\.href\s*=\s*"[^"]+"\s*;\s*', '', s)

# Inject role redirect block right after signInWithPassword result handling:
# We'll search for "if (error)" block then insert after it ends, otherwise after signIn call.
anchor = re.search(r'if\s*\(\s*error\s*\)\s*\{[\s\S]*?\}\s*', s)
if anchor:
    pos = anchor.end()
else:
    # fallback: insert after signIn call
    m = re.search(r'await\s+sb\.auth\.signInWithPassword\s*\([\s\S]*?\)\s*;', s)
    if not m:
        raise SystemExit("❌ Can't locate signInWithPassword statement for injection.")
    pos = m.end()

inject = r"""
      // Wait until session is actually available (prevents dashboard flash->login loop)
      const sess = await waitForSession(sb, 2500);
      if(!sess){ alert("Login succeeded but session not ready yet. Refresh and try again."); return; }

      const { data: u1 } = await sb.auth.getUser();
      const uid = u1?.user?.id;
      const uemail = u1?.user?.email;

      let role = null;
      if(uid){
        const r1 = await sb.from("agent_profiles").select("role,full_name,username,email,auth_id,user_id")
          .or(`auth_id.eq.${uid},user_id.eq.${uid}`).maybeSingle();
        role = r1?.data?.role || null;
      }
      if(!role && uemail){
        const r2 = await sb.from("agent_profiles").select("role").eq("email", uemail).maybeSingle();
        role = r2?.data?.role || null;
      }
      role = String(role || "AGENT").toUpperCase().trim();
      window.location.href = (role === "ADMIN") ? "/dashboard/admin" : "/agent/dashboard";
      return;
"""
# Avoid double-inject if already present
if "prevents dashboard flash->login loop" not in s:
    s = s[:pos] + inject + s[pos:]

p.write_text(s, encoding="utf-8")
print("✅ login.html patched (sbReady + waitForSession + role redirect)")
PY

echo "==> Patch templates/agent_dashboard.html to WAIT for session before redirecting to /login..."
python3 - <<'PY'
from pathlib import Path
import re

p = Path("templates/agent_dashboard.html")
s = p.read_text(encoding="utf-8")

# Replace "if(!session){ window.location.href='/login'; return; }" with a wait loop
pattern = r'if\s*\(\s*!\s*session\s*\)\s*\{\s*window\.location\.href\s*=\s*["\']/login["\']\s*;\s*return;\s*\}'
replacement = r"""
  if(!session){
    // Session can be delayed right after redirect from /login -> wait before kicking user out
    const start = Date.now();
    while(Date.now() - start < 2500){
      const { data: d2 } = await sb.auth.getSession();
      session = d2?.session || null;
      if(session) break;
      await new Promise(r => setTimeout(r, 150));
    }
    if(!session){
      window.location.href = "/login";
      return;
    }
  }
"""
s2, n = re.subn(pattern, replacement, s, flags=re.MULTILINE)
if n == 0:
    print("⚠️ Did not find the exact session redirect block to patch. (Still ok if your dashboard differs.)")
else:
    p.write_text(s2, encoding="utf-8")
    print("✅ agent_dashboard.html patched (session wait)")
PY

echo "==> Compile check..."
python3 -m py_compile app.py

echo "==> Commit + push..."
git add templates/login.html templates/agent_dashboard.html
git commit -m "Fix agent login loop: wait for Supabase session + enforce sbReady client" || true
git push origin main

echo "✅ DONE. Wait for Render to deploy, then test again."
