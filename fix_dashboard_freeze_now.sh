#!/usr/bin/env bash
set -e

echo "== backup =="
cp templates/agent_dashboard.html templates/agent_dashboard.html.bak_$(date +%Y%m%d_%H%M%S)
cp app.py app.py.bak_$(date +%Y%m%d_%H%M%S)
cp yene_compat_routes.py yene_compat_routes.py.bak_$(date +%Y%m%d_%H%M%S)

python3 - <<'PY'
from pathlib import Path
import re

# ---------------------------
# 1) Fix dashboard template
# ---------------------------
p = Path("templates/agent_dashboard.html")
text = p.read_text(encoding="utf-8", errors="ignore")

# remove 2-second UI spam
text = text.replace("setInterval(forceRenderCompetitionCards, 2000);", "")

# remove duplicate truth loader boot block near the bottom
text = re.sub(
    r"""\n\s*document\.addEventListener\('DOMContentLoaded', \(\) => \{\s*
\s*setTimeout\(loadTruthOverviewAndLeaderboard, 300\);\s*
\s*setInterval\(\(\) => \{ try \{ loadTruthOverviewAndLeaderboard\(\); \} catch \(e\) \{\} \}, 30000\);\s*
\s*\}\);\s*""",
    "\n",
    text,
    flags=re.DOTALL,
)

# remove any remaining loadTruthOverview interval
text = re.sub(
    r"""^\s*setInterval\(\(\)\s*=>\s*\{\s*try\s*\{\s*loadTruthOverviewAndLeaderboard\(\);\s*\}\s*catch\s*\(e\)\s*\{\s*\}\s*\},\s*30000\);\s*$""",
    "",
    text,
    flags=re.MULTILINE,
)

# if refreshSupport timer exists, slow it down
text = text.replace("setInterval(refreshSupport, 15000);", "setInterval(refreshSupport, 60000);")
text = text.replace("setInterval(refreshSupport, 30000);", "setInterval(refreshSupport, 60000);")

# if hydrate timer exists, slow it down
text = text.replace("setInterval(hydrate, 15000);", "setInterval(hydrate, 60000);")
text = text.replace("setInterval(hydrate, 30000);", "setInterval(hydrate, 60000);")

# make weekBadge safe
text = text.replace(
    '$("weekBadge").textContent = state.leaderboardPeriod === \'month\' ? \'Current month\' : (state.leaderboardPeriod === \'all\' ? \'All time\' : \'Current week\');',
    'if ($("weekBadge")) $("weekBadge").textContent = state.leaderboardPeriod === \'month\' ? \'Current month\' : (state.leaderboardPeriod === \'all\' ? \'All time\' : \'Current week\');'
)

p.write_text(text, encoding="utf-8")

# ---------------------------
# 2) Make /api/agent/messages fail soft
# ---------------------------
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

# ---------------------------
# 3) Lighten profile lookup retries
# ---------------------------
p3 = Path("app.py")
t3 = p3.read_text(encoding="utf-8", errors="ignore")
t3 = t3.replace("for attempt in range(4):", "for attempt in range(2):", 1)
t3 = t3.replace("time.sleep(0.2 * (attempt + 1))", "time.sleep(0.1 * (attempt + 1))", 1)
p3.write_text(t3, encoding="utf-8")

print("Patched dashboard template, yene_compat_routes.py, and app.py")
PY

echo
echo "== compile =="
python3 -m py_compile app.py yene_compat_routes.py agent_dashboard_v4.py agent_wallet_v1.py

echo
echo "== stop old server =="
pkill -f "python3 app.py" || true

echo
echo "== start server =="
python3 app.py
