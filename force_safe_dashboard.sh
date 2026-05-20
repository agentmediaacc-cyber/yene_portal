#!/usr/bin/env bash
set -e

echo "== backup =="
cp templates/agent_dashboard.html templates/agent_dashboard.html.bak_$(date +%Y%m%d_%H%M%S)

python3 - <<'PY'
from pathlib import Path
import re

p = Path("templates/agent_dashboard.html")
t = p.read_text(encoding="utf-8", errors="ignore")

# 1) remove the last remaining auto interval in the DOMContentLoaded boot block
t = re.sub(
    r"""
document\.addEventListener\('DOMContentLoaded', async \(\) => \{
(.*?)
setInterval\(\(\) => \{
(.*?)
\},\s*60000\);
(.*?)
\}\);
""",
    r"""document.addEventListener('DOMContentLoaded', async () => {
\1
\3
});""",
    t,
    flags=re.DOTALL | re.VERBOSE
)

# 2) make refreshAll lighter: remove messages and jobs from initial Promise.allSettled
t = t.replace(
    '        loadAndRender("messages", loadMessages, null),\n',
    ''
)
t = t.replace(
    '        loadAndRender("jobs", async () => { await loadJobs(); await loadGroupMessages(); }, () => { renderJobs(); renderGroupMessages(); }),\n',
    ''
)

# 3) make initial load only do the important sections
t = t.replace(
    "      await refreshAll(false);",
    """      await loadAndRender("profile", loadMe, updateHero);
      await loadAndRender("summary", loadSummary, updateHero);
      await loadAndRender("activity", () => loadActivity(state.currentPeriod), renderActivity);
      await loadAndRender("wallet", loadWallet, () => { updateHero(); renderWallet(); });
      await loadAndRender("team", loadTeam, renderTeam);
      await loadAndRender("leaderboard", () => loadLeaderboard(state.leaderboardPeriod), () => {
        renderLeaderboard();
        if (window.loadLeaderboardOverride) window.loadLeaderboardOverride();
        forceRenderCompetitionCards();
      });"""
)

# 4) stop refreshSupport from auto-trigger chains
t = t.replace("      await refreshSupport();", "      // refreshSupport disabled in safe mode")
t = t.replace("    setTimeout(refreshSupport, 400);", "")

# 5) if messages section exists, show static safe text until manual refresh
t = t.replace(
    'Loading messages...',
    'Messages paused for stability. Use Refresh to reload.'
)
t = t.replace(
    'Loading active jobs...',
    'Jobs paused for stability.'
)
t = t.replace(
    'Loading alerts...',
    'Alerts paused for stability.'
)

p.write_text(t, encoding="utf-8")
print("Safe mode dashboard patch written")
PY

echo
echo "== verify remaining auto hooks =="
grep -n "DOMContentLoaded\|setInterval\|refreshSupport\|loadTruthOverviewAndLeaderboard" templates/agent_dashboard.html || true

echo
echo "== restart app =="
pkill -f "python3 app.py" || true
python3 app.py
