#!/usr/bin/env bash
set -euo pipefail

# Disable zsh history expansion issues if running via zsh
set +H 2>/dev/null || true

echo "==> Patching base_public.html (no redirect, just init supabase) ..."
cat > templates/base_public.html <<'EOF'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>🌐 YENE Portal</title>
  <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>

  <script>
  // ONE global client for the whole site (public pages too)
  window.sbReady = (async () => {
    try {
      const r = await fetch("/api/public-config", { credentials: "same-origin" });
      const cfg = await r.json();
      if (!cfg.ok || !cfg.SUPABASE_URL || !cfg.SUPABASE_ANON_KEY) {
        console.error("Missing public config", cfg);
        window.supabase = null;
        return null;
      }

      window.supabase = supabase.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: true
        }
      });

      return window.supabase;
    } catch (e) {
      console.error("sbReady init failed", e);
      window.supabase = null;
      return null;
    }
  })();

  // helper: get bearer token for API calls
  window.getBearer = async () => {
    const sb = await window.sbReady;
    if (!sb) return null;
    const { data } = await sb.auth.getSession();
    const token = data?.session?.access_token || null;
    return token ? ("Bearer " + token) : null;
  };
  </script>
</head>

<body>
  {% block content %}{% endblock %}
</body>
</html>
EOF

echo "==> Patching base.html (NO auto redirect/signout here) ..."
cat > templates/base.html <<'EOF'
{% extends "base_public.html" %}
{% block content %}
  {% block inner %}{% endblock %}
{% endblock %}
EOF

# IMPORTANT: your templates currently use "{% extends 'base.html' %}"
# This new base.html extends base_public.html and does NOT force redirects.

echo "==> Patching login.html redirect logic (ADMIN->/dashboard/admin, AGENT->/agent/dashboard) ..."
python3 - <<'PY'
from pathlib import Path
import re

p = Path("templates/login.html")
s = p.read_text(encoding="utf-8")

# We will patch ONLY the redirect line if we find it.
# Your file already contains signInWithPassword usage; we replace redirect to be role-based.
if 'signInWithPassword' not in s:
    raise SystemExit("login.html does not contain signInWithPassword - paste your login.html if different.")

# Replace any hardcoded window.location.href = "..."
s = re.sub(r'window\.location\.href\s*=\s*["\'][^"\']+["\']\s*;', '/*redirect_patched*/', s)

# Inject role-based redirect after successful login.
# Find the line with signInWithPassword call and add logic after success.
needle = r'await\s+window\.supabase\.auth\.signInWithPassword\s*\(\s*\{\s*email\s*,\s*password\s*\}\s*\)\s*;'
m = re.search(needle, s)
if not m:
    needle2 = r'await\s+window\.supabase\.auth\.signInWithPassword\s*\(\s*\{\s*email:\s*email\s*,\s*password:\s*password\s*\}\s*\)\s*;'
    m = re.search(needle2, s)
if not m:
    raise SystemExit("Could not find signInWithPassword(...) call in login.html. Paste login.html and I’ll patch exactly.")

insert = r"""
      // ---- ROLE BASED REDIRECT (single source of truth) ----
      const sb = await window.sbReady;
      if(!sb){ alert("Supabase client not ready. Refresh."); return; }

      const { data: u1 } = await sb.auth.getUser();
      const uid = u1?.user?.id;
      const uemail = u1?.user?.email;

      // Try by auth_id -> user_id -> email (covers old accounts)
      let role = null;

      if(uid){
        const r1 = await sb.from("agent_profiles").select("role").or(`auth_id.eq.${uid},user_id.eq.${uid}`).maybeSingle();
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

# Insert after the signInWithPassword call line
pos = m.end()
s2 = s[:pos] + insert + s[pos:]

p.write_text(s2, encoding="utf-8")
print("✅ login.html patched")
PY

echo "==> Patching agent_dashboard.html to require session & load agent profile safely ..."
cat > templates/agent_dashboard.html <<'EOF'
{% extends "base.html" %}
{% block inner %}

<div style="padding:16px">
  <div style="font-weight:900;color:#4aa8ff">YENE PROMOTER</div>
  <div id="status" style="margin-top:10px;color:#a9b7d6">Loading…</div>

  <div id="panel" style="display:none;margin-top:14px">
    <div style="font-size:18px;font-weight:900">
      Agent: <span id="agent_name">—</span>
    </div>
    <div style="color:#a9b7d6;margin-top:6px">
      This Week (Mon–Sun)
    </div>

    <div style="display:flex;gap:10px;margin-top:10px;flex-wrap:wrap">
      <div style="padding:10px;border:1px solid rgba(255,255,255,.12);border-radius:12px;min-width:160px">
        Drivers registered: <b id="wk_drivers">0</b>
      </div>
      <div style="padding:10px;border:1px solid rgba(255,255,255,.12);border-radius:12px;min-width:160px">
        Clients registered: <b id="wk_clients">0</b>
      </div>
      <div style="padding:10px;border:1px solid rgba(255,255,255,.12);border-radius:12px;min-width:160px">
        Drivers (all-time): <b id="all_drivers">0</b>
      </div>
    </div>

    <div style="margin-top:18px">
      <div style="font-weight:900;margin-bottom:8px">Recent activity (this week)</div>
      <div style="overflow:auto;border:1px solid rgba(255,255,255,.12);border-radius:12px">
        <table style="width:100%;border-collapse:collapse;min-width:520px">
          <thead>
            <tr style="background:rgba(74,168,255,.08);color:#4aa8ff">
              <th style="text-align:left;padding:10px">Type</th>
              <th style="text-align:left;padding:10px">Name</th>
              <th style="text-align:left;padding:10px">Phone</th>
              <th style="text-align:left;padding:10px">Date</th>
            </tr>
          </thead>
          <tbody id="activity">
            <tr><td colspan="4" style="padding:10px;color:#a9b7d6">Loading…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div style="margin-top:16px;color:#a9b7d6;font-size:12px">
      Debug: /api/public-config ✅ (Supabase keys OK)
    </div>
  </div>
</div>

<script>
(async () => {
  const sb = await window.sbReady;
  if(!sb){
    document.getElementById("status").innerText = "Supabase client not ready. Refresh.";
    return;
  }

  // Require session ONLY on dashboard pages (not in base.html)
  const { data: sessData } = await sb.auth.getSession();
  const session = sessData?.session;
  if(!session){
    window.location.href = "/login";
    return;
  }

  const { data: userData } = await sb.auth.getUser();
  const uid = userData?.user?.id;
  const email = userData?.user?.email;

  document.getElementById("status").innerText = "Authenticated. Loading your profile…";

  // Find agent profile by auth_id/user_id/email
  let prof = null;
  if(uid){
    const r1 = await sb.from("agent_profiles")
      .select("*")
      .or(`auth_id.eq.${uid},user_id.eq.${uid}`)
      .maybeSingle();
    prof = r1.data || null;
  }
  if(!prof && email){
    const r2 = await sb.from("agent_profiles").select("*").eq("email", email).maybeSingle();
    prof = r2.data || null;
  }

  if(!prof){
    document.getElementById("status").innerText =
      "Logged in, but your agent profile is not linked. Run the SQL linking script (Step B).";
    return;
  }

  document.getElementById("agent_name").innerText = prof.full_name || prof.username || prof.email || "Agent";
  document.getElementById("panel").style.display = "block";
  document.getElementById("status").style.display = "none";

  // Load weekly summary using your existing tables (drivers/clients)
  // We compute week window client-side (Mon 00:00 -> Sun 23:59) in UTC-ish; adjust if needed.
  const now = new Date();
  const day = (now.getDay() + 6) % 7; // Mon=0
  const monday = new Date(now);
  monday.setDate(now.getDate() - day);
  monday.setHours(0,0,0,0);

  const sundayEnd = new Date(monday);
  sundayEnd.setDate(monday.getDate() + 7);

  const agentKey = prof.id || prof.auth_id || prof.user_id || uid;

  // Drivers this week
  const d = await sb.from("drivers")
    .select("id,created_at", { count: "exact" })
    .eq("recruiter_agent_id", agentKey)
    .gte("created_at", monday.toISOString())
    .lt("created_at", sundayEnd.toISOString());

  // Clients this week
  const c = await sb.from("clients")
    .select("id,created_at", { count: "exact" })
    .eq("recruiter_agent_id", agentKey)
    .gte("created_at", monday.toISOString())
    .lt("created_at", sundayEnd.toISOString());

  // Drivers all time
  const dt = await sb.from("drivers")
    .select("id", { count: "exact", head: true })
    .eq("recruiter_agent_id", agentKey);

  document.getElementById("wk_drivers").innerText = d.count || 0;
  document.getElementById("wk_clients").innerText = c.count || 0;
  document.getElementById("all_drivers").innerText = dt.count || 0;

  // Recent activity: show last 10 from drivers + clients (simple merge)
  const lastDrivers = await sb.from("drivers")
    .select("full_name,phone,created_at")
    .eq("recruiter_agent_id", agentKey)
    .order("created_at", { ascending: false })
    .limit(5);

  const lastClients = await sb.from("clients")
    .select("full_name,phone,created_at")
    .eq("recruiter_agent_id", agentKey)
    .order("created_at", { ascending: false })
    .limit(5);

  const rows = [];
  (lastDrivers.data||[]).forEach(x => rows.push({type:"Driver", name:x.full_name, phone:x.phone, created_at:x.created_at}));
  (lastClients.data||[]).forEach(x => rows.push({type:"Client", name:x.full_name, phone:x.phone, created_at:x.created_at}));
  rows.sort((a,b)=> new Date(b.created_at)-new Date(a.created_at));

  const tbody = document.getElementById("activity");
  if(rows.length === 0){
    tbody.innerHTML = `<tr><td colspan="4" style="padding:10px;color:#a9b7d6">No activity yet.</td></tr>`;
  } else {
    tbody.innerHTML = rows.slice(0,10).map(r => `
      <tr>
        <td style="padding:10px;border-top:1px solid rgba(255,255,255,.06)">${r.type}</td>
        <td style="padding:10px;border-top:1px solid rgba(255,255,255,.06)">${(r.name||"").replaceAll("<","&lt;")}</td>
        <td style="padding:10px;border-top:1px solid rgba(255,255,255,.06)">${(r.phone||"").replaceAll("<","&lt;")}</td>
        <td style="padding:10px;border-top:1px solid rgba(255,255,255,.06)">${new Date(r.created_at).toLocaleString()}</td>
      </tr>
    `).join("");
  }
})();
</script>

{% endblock %}
EOF

echo "==> Ensuring admin dashboard also requires session ONLY on that page (not base) ..."
# We won't overwrite your admin_dashboard.html fully — just add a guard at top if missing
python3 - <<'PY'
from pathlib import Path
p = Path("templates/admin_dashboard.html")
s = p.read_text(encoding="utf-8")

guard = """
<script>
(async()=>{
  const sb = await window.sbReady;
  if(!sb){ location.href="/login"; return; }
  const { data } = await sb.auth.getSession();
  if(!data?.session){ location.href="/login"; return; }

  // Optional: enforce role ADMIN (if your table has role)
  const { data: u1 } = await sb.auth.getUser();
  const uid = u1?.user?.id;
  const email = u1?.user?.email;

  let role = null;
  if(uid){
    const r1 = await sb.from("agent_profiles").select("role").or(`auth_id.eq.${uid},user_id.eq.${uid}`).maybeSingle();
    role = r1?.data?.role || null;
  }
  if(!role && email){
    const r2 = await sb.from("agent_profiles").select("role").eq("email", email).maybeSingle();
    role = r2?.data?.role || null;
  }
  role = String(role||"").toUpperCase().trim();
  if(role !== "ADMIN"){ location.href="/agent/dashboard"; return; }
})();
</script>
"""

if guard.strip() not in s:
    # insert after <body if possible, else prepend
    if "<body" in s:
        idx = s.find(">", s.find("<body"))
        s = s[:idx+1] + "\n" + guard + "\n" + s[idx+1:]
    else:
        s = guard + "\n" + s
    p.write_text(s, encoding="utf-8")
    print("✅ admin_dashboard.html guarded")
else:
    print("✅ admin_dashboard.html already guarded")
PY

echo "==> Compile check ..."
python3 -m py_compile app.py

echo "==> Git commit/push ..."
git add templates/base.html templates/base_public.html templates/login.html templates/agent_dashboard.html templates/admin_dashboard.html
git commit -m "Hard reset auth flow: single sbReady, page-level guards, agent profile load fix" || true
git push origin main

echo "✅ DONE. Deploy will rebuild on Render."
