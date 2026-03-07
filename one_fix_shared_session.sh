set -euo pipefail

# 1) Shared base_public.html
cat > templates/base_public.html <<'EOF'
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>YENE Portal</title>
  <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;margin:0;background:#0b1220;color:#e8f0ff}
    .wrap{max-width:1100px;margin:0 auto;padding:14px}
    .card{background:#101a33;border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:14px;margin:10px 0}
    .muted{opacity:.8}
    input,button{width:100%;padding:12px;border-radius:12px;border:1px solid rgba(255,255,255,.12);background:#0b1220;color:#e8f0ff}
    button{background:#2c6cff;border:none;font-weight:700;cursor:pointer}
    table{width:100%;border-collapse:collapse}
    th,td{padding:10px;border-bottom:1px solid rgba(255,255,255,.08);text-align:left;font-size:14px}
    .row{display:flex;gap:10px;flex-wrap:wrap}
    .topbar{display:flex;justify-content:space-between;align-items:center;gap:10px}
    a{color:#9ec0ff}
  </style>
  <script>
    window.sbReady = (async () => {
      try {
        const r = await fetch("/api/public-config", { credentials: "same-origin" });
        const cfg = await r.json();
        if (!cfg.ok) throw new Error("Missing public config");

        const sb = supabase.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY, {
          auth: {
            persistSession: true,
            autoRefreshToken: true,
            detectSessionInUrl: true,
            storageKey: "yene-auth"
          }
        });

        window.supabaseClient = sb;
        return sb;
      } catch (e) {
        console.error("sbReady failed", e);
        return null;
      }
    })();
  </script>
</head>
<body>
  {% block content %}{% endblock %}
</body>
</html>
EOF

# 2) Shared base.html
cat > templates/base.html <<'EOF'
{% extends "base_public.html" %}
{% block content %}
  {% block inner %}{% endblock %}
{% endblock %}
EOF

# 3) Patch login.html to explicitly save session after login
python3 - <<'PY'
from pathlib import Path
import re

p = Path("templates/login.html")
s = p.read_text(encoding="utf-8")

# Add helper once
if "async function __yeneWaitSession" not in s:
    helper = """
async function __yeneWaitSession(sb, ms=7000){
  const start = Date.now();
  while(Date.now() - start < ms){
    const { data } = await sb.auth.getSession();
    if(data && data.session) return data.session;
    await new Promise(r => setTimeout(r, 200));
  }
  return null;
}
""".strip()
    s = re.sub(r'(<script[^>]*>)', r'\1\n' + helper + "\n", s, count=1)

# Normalize signIn to shared client
s = s.replace("window.supabase.auth.signInWithPassword", "sb.auth.signInWithPassword")

# Ensure sb is created before signIn
target = 'const { data, error } = await sb.auth.signInWithPassword({ email, password });'
if target in s and 'const sb = await window.sbReady;' not in s:
    s = s.replace(
        target,
        'const sb = await window.sbReady;\n      if(!sb){ alert("Supabase client not ready"); return; }\n      ' + target
    )

# After signIn, explicitly save session and wait for restore
if "__yeneWaitSession(sb, 7000)" not in s:
    s = s.replace(
        target,
        target + """
      if (error) {
        alert(error.message || "Login failed");
        return;
      }

      if (data && data.session && data.session.access_token && data.session.refresh_token) {
        await sb.auth.setSession({
          access_token: data.session.access_token,
          refresh_token: data.session.refresh_token
        });
      }

      const savedSession = await __yeneWaitSession(sb, 7000);
      if (!savedSession) {
        alert("Login succeeded but session was not restored yet. Please try again.");
        return;
      }
"""
    )

p.write_text(s, encoding="utf-8")
print("✅ login.html patched")
PY

# 4) Replace agent dashboard with session-stable version
cat > templates/agent_dashboard.html <<'EOF'
{% extends "base.html" %}
{% block inner %}
<div class="wrap">
  <div class="topbar">
    <div>
      <div class="muted">🌐 YENE Portal</div>
      <h2 id="welcome" style="margin:6px 0">Welcome, Agent</h2>
      <div class="muted" id="weekLabel">Loading…</div>
    </div>
    <div style="display:flex;gap:10px">
      <button id="retryBtn" type="button">Retry</button>
      <button id="signOutBtn" type="button">Sign out</button>
    </div>
  </div>

  <div id="statusCard" class="card">
    <div id="statusText">Checking your session…</div>
  </div>

  <div id="dashboardArea" style="display:none">
    <div class="card">
      <div class="row">
        <div style="flex:1;min-width:180px">
          <div class="muted">Drivers registered (this week)</div>
          <div id="driversWeek" style="font-size:28px;font-weight:800">0</div>
        </div>
        <div style="flex:1;min-width:180px">
          <div class="muted">Clients registered (this week)</div>
          <div id="clientsWeek" style="font-size:28px;font-weight:800">0</div>
        </div>
        <div style="flex:1;min-width:180px">
          <div class="muted">Drivers (all-time)</div>
          <div id="driversAll" style="font-size:28px;font-weight:800">0</div>
        </div>
        <div style="flex:1;min-width:180px">
          <div class="muted">Wallet balance</div>
          <div id="walletBalance" style="font-size:28px;font-weight:800">0.00</div>
        </div>
      </div>
    </div>

    <div class="card">
      <h3 style="margin-top:0">Recent activity</h3>
      <table>
        <thead><tr><th>Type</th><th>Name</th><th>Phone</th><th>Town</th><th>Code</th><th>Date</th></tr></thead>
        <tbody id="activityBody"><tr><td colspan="6" class="muted">No data yet.</td></tr></tbody>
      </table>
    </div>
  </div>
</div>

<script>
(async () => {
  const statusText = document.getElementById("statusText");
  const statusCard = document.getElementById("statusCard");
  const dashboardArea = document.getElementById("dashboardArea");

  async function waitForSession(sb, ms=8000){
    const start = Date.now();
    while(Date.now() - start < ms){
      const { data } = await sb.auth.getSession();
      if (data && data.session) return data.session;
      await new Promise(r => setTimeout(r, 200));
    }
    return null;
  }

  async function bearer(sb){
    const { data } = await sb.auth.getSession();
    return data?.session?.access_token ? ("Bearer " + data.session.access_token) : null;
  }

  async function api(sb, path){
    const b = await bearer(sb);
    if(!b) return { ok:false, error:"No session token" };
    const r = await fetch(path, { headers: { "Authorization": b } });
    const txt = await r.text();
    try { return JSON.parse(txt); }
    catch(e){ return { ok:false, error:"bad json", raw: txt }; }
  }

  async function loadDashboard(){
    statusCard.style.display = "block";
    dashboardArea.style.display = "none";
    statusText.textContent = "Checking your session…";

    const sb = await window.sbReady;
    if(!sb){
      statusText.textContent = "Supabase client not ready.";
      return;
    }

    const session = await waitForSession(sb, 8000);
    if(!session){
      statusText.innerHTML = 'No active session found. Please <a href="/login">login again</a>.';
      return;
    }

    const me = await api(sb, "/api/agent/me_v3");
    if(!me || !me.ok){
      statusText.textContent = "Could not load your profile.";
      return;
    }

    const profile = me.profile || null;
    if(!profile){
      document.getElementById("welcome").textContent = "Welcome, New Agent";
      document.getElementById("weekLabel").textContent = "No data yet. Waiting for your first registration.";
      statusText.textContent = "Your account is active but no profile data is linked yet.";
      dashboardArea.style.display = "block";
      return;
    }

    document.getElementById("welcome").textContent =
      "Welcome, " + (profile.full_name || profile.username || profile.email || "Agent");

    const summary = await api(sb, "/api/agent/summary_v3");
    if(summary && summary.ok){
      document.getElementById("weekLabel").textContent =
        "Week: " + (summary.week_start || "") + " → " + (summary.week_end || "");
      document.getElementById("driversWeek").textContent = summary.drivers_week || 0;
      document.getElementById("clientsWeek").textContent = summary.clients_week || 0;
      document.getElementById("driversAll").textContent = summary.drivers_all || 0;
    } else {
      document.getElementById("weekLabel").textContent = "No weekly summary yet.";
    }

    const wallet = await api(sb, "/api/agent/wallet_v3");
    if(wallet && wallet.ok){
      document.getElementById("walletBalance").textContent = Number(wallet.balance || 0).toFixed(2);
    }

    const activity = await api(sb, "/api/agent/activity_v3");
    const body = document.getElementById("activityBody");
    body.innerHTML = "";
    const rows = (activity && activity.ok) ? (activity.rows || []) : [];
    if(rows.length === 0){
      body.innerHTML = '<tr><td colspan="6" class="muted">No registrations yet.</td></tr>';
    } else {
      for(const r of rows){
        body.insertAdjacentHTML("beforeend", `
          <tr>
            <td>${r.subject_type || ""}</td>
            <td>${r.full_name || ""}</td>
            <td>${r.phone || ""}</td>
            <td>${r.town || ""}</td>
            <td>${r.external_code || ""}</td>
            <td>${r.created_at ? new Date(r.created_at).toLocaleString() : ""}</td>
          </tr>
        `);
      }
    }

    statusCard.style.display = "none";
    dashboardArea.style.display = "block";
  }

  document.getElementById("retryBtn").onclick = () => loadDashboard();
  document.getElementById("signOutBtn").onclick = async () => {
    const sb = await window.sbReady;
    if (sb) await sb.auth.signOut();
    location.href = "/login";
  };

  await loadDashboard();
})();
</script>
{% endblock %}
EOF

python3 -m py_compile app.py && echo "✅ app.py compiles"

git add templates/base_public.html templates/base.html templates/login.html templates/agent_dashboard.html
git commit -m "Fix shared Supabase session between login and agent dashboard" || true
git push origin main
