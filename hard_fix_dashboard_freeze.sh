#!/usr/bin/env bash
set -e

cp templates/agent_dashboard.html templates/agent_dashboard.html.bak_$(date +%Y%m%d_%H%M%S)
cp yene_compat_routes.py yene_compat_routes.py.bak_$(date +%Y%m%d_%H%M%S)

python3 - <<'PY'
from pathlib import Path
import re

p = Path("templates/agent_dashboard.html")
t = p.read_text(encoding="utf-8", errors="ignore")

# 1) remove duplicate truth-loader boot block
t = re.sub(
    r"""
\s*document\.addEventListener\('DOMContentLoaded',\s*\(\)\s*=>\s*\{
\s*setTimeout\(loadTruthOverviewAndLeaderboard,\s*300\);
.*?
\s*\}\);
""",
    "\n",
    t,
    flags=re.DOTALL | re.VERBOSE
)

# 2) remove old support boot block at bottom
t = re.sub(
    r"""
\s*document\.addEventListener\('DOMContentLoaded',\s*function\(\)\{
.*?refreshSupport.*?
\s*\}\);
""",
    "\n",
    t,
    flags=re.DOTALL | re.VERBOSE
)

# 3) remove spam timers
for s in [
    "setInterval(forceRenderCompetitionCards, 2000);",
    "setInterval(refreshSupport, 15000);",
    "setInterval(refreshSupport, 60000);",
    "setInterval(hydrate, 15000);",
    "setInterval(hydrate, 60000);",
]:
    t = t.replace(s, "")

t = re.sub(
    r"setInterval\(\(\)\s*=>\s*\{\s*try\s*\{\s*loadTruthOverviewAndLeaderboard\(\);\s*\}\s*catch\s*\(e\)\s*\{\s*\}\s*\},\s*30000\);",
    "",
    t
)

# 4) keep only one clean dashboard boot
t = re.sub(
    r"""
document\.addEventListener\('DOMContentLoaded',\s*async\s*\(\)\s*=>\s*\{
.*?await refreshAll\(false\);
.*?setTimeout\(forceRenderCompetitionCards,\s*800\);
.*?\}\);
""",
    """document.addEventListener('DOMContentLoaded', async () => {
      bindAgentMenu();
      const hash = location.hash.replace('#','').trim();
      if (hash && $(hash)) openSection(hash);
      await refreshAll(false);
      forceRenderCompetitionCards();
      setTimeout(forceRenderCompetitionCards, 200);
      setTimeout(forceRenderCompetitionCards, 800);
    });""",
    t,
    flags=re.DOTALL | re.VERBOSE,
    count=1
)

# 5) make weekBadge safe
t = t.replace(
    '$("weekBadge").textContent = state.leaderboardPeriod === \'month\' ? \'Current month\' : (state.leaderboardPeriod === \'all\' ? \'All time\' : \'Current week\');',
    'if ($("weekBadge")) $("weekBadge").textContent = state.leaderboardPeriod === \'month\' ? \'Current month\' : (state.leaderboardPeriod === \'all\' ? \'All time\' : \'Current week\');'
)

p.write_text(t, encoding="utf-8")

p2 = Path("yene_compat_routes.py")
t2 = p2.read_text(encoding="utf-8", errors="ignore")
t2 = t2.replace(
    'return jsonify({"ok": False, "error": "Agent not found"}), 404',
    'return jsonify({"ok": True, "messages": [], "unread": 0})'
)
t2 = t2.replace(
    'return jsonify({"ok": False, "error": "Messages unavailable"}), 404',
    'return jsonify({"ok": True, "messages": [], "unread": 0})'
)
p2.write_text(t2, encoding="utf-8")

print("dashboard and messages route patched")
PY

python3 -m py_compile app.py yene_compat_routes.py agent_dashboard_v4.py agent_wallet_v1.py

pkill -f "python3 app.py" || true
python3 app.py
