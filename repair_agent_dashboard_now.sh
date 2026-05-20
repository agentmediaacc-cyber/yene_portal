#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/Desktop/yene_portal"
source venv/bin/activate

echo "== backup =="
ts="$(date +%Y%m%d_%H%M%S)"
cp templates/agent_dashboard.html "templates/agent_dashboard.html.bak_${ts}"
cp agent_dashboard_v4.py "agent_dashboard_v4.py.bak_${ts}"
cp app.py "app.py.bak_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

# ----------------------------
# 1) Fix backend pressure
# ----------------------------
p = Path("agent_dashboard_v4.py")
text = p.read_text()

text = text.replace(
    "def _execute_with_retry(label, query_factory, retries=1, delay=0.25):",
    "def _execute_with_retry(label, query_factory, retries=0, delay=0.20):"
)
text = text.replace(
    "def _execute_with_retry(label, query_factory, retries=2, delay=0.25):",
    "def _execute_with_retry(label, query_factory, retries=0, delay=0.20):"
)
text = text.replace(
    '_execute_with_retry("get_agent_profile", build, retries=3, delay=0.35)',
    '_execute_with_retry("get_agent_profile", build, retries=0, delay=0.15)'
)
text = text.replace(
    '_execute_with_retry("get_agent_profile", build, retries=1, delay=0.20)',
    '_execute_with_retry("get_agent_profile", build, retries=0, delay=0.15)'
)

# reduce default table scans
text = text.replace(
    'def _select_all(table, limit=10000, order_col="created_at", desc=True):',
    'def _select_all(table, limit=300, order_col="created_at", desc=True):'
)

# reduce hardcoded 10000 scans if still present
text = text.replace("_select_all(table, 10000)", "_select_all(table, 300)")
text = text.replace('_select_all("agent_profiles", 10000)', '_select_all("agent_profiles", 300)')
text = text.replace('_select_all("drivers", 10000)', '_select_all("drivers", 300)')
text = text.replace('_select_all("clients", 10000)', '_select_all("clients", 300)')
text = text.replace('_select_all("agent_wallets", 5000)', '_select_all("agent_wallets", 200)')

p.write_text(text)

# ----------------------------
# 2) Tighten request timeout
# ----------------------------
p = Path("app.py")
text = p.read_text()
text = text.replace("timeout=10,", "timeout=5,")
p.write_text(text)

# ----------------------------
# 3) Remove broken duplicate JS loader block
# ----------------------------
p = Path("templates/agent_dashboard.html")
html = p.read_text()

# remove duplicate high-cost timers
html = html.replace("setInterval(forceRenderCompetitionCards, 15000);", "")
html = html.replace("setInterval(() => { try { loadTruthOverviewAndLeaderboard(); } catch (e) {} }, 30000);", "")

# remove the extra truth overview block entirely if present
start = html.find("async function loadTruthOverviewAndLeaderboard()")
if start != -1:
    tail = html[start:]
    marker = "document.addEventListener('DOMContentLoaded', () => {"
    mpos = tail.find(marker)
    if mpos != -1:
        # find end of that DOMContentLoaded block by matching braces crudely
        block_start = start + mpos
        i = block_start + len(marker)
        depth = 1
        while i < len(html) and depth > 0:
            ch = html[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        # trim possible closing ");"
        j = i
        while j < len(html) and html[j] in "); \n\r\t":
            j += 1
        html = html[:start] + "\n/* removed duplicate truth overview loader block */\n" + html[j:]

# kill very aggressive polling if still present
html = re.sub(r"setInterval\(([^,]+),\s*1000\);", r"/* removed noisy polling: setInterval(\1, 1000); */", html)
html = re.sub(r"setInterval\(([^,]+),\s*1500\);", r"/* removed noisy polling: setInterval(\1, 1500); */", html)
html = re.sub(r"setInterval\(([^,]+),\s*2000\);", r"/* removed noisy polling: setInterval(\1, 2000); */", html)
html = re.sub(r"setInterval\(([^,]+),\s*2500\);", r"/* removed noisy polling: setInterval(\1, 2500); */", html)
html = re.sub(r"setInterval\(([^,]+),\s*3000\);", r"/* removed noisy polling: setInterval(\1, 3000); */", html)
html = re.sub(r"setInterval\(([^,]+),\s*5000\);", r"/* removed noisy polling: setInterval(\1, 5000); */", html)

# make refresh button safe if element exists
html = html.replace(
    '$("refreshBtn").addEventListener(\'click\', () => refreshAll(true));',
    'if ($("refreshBtn")) $("refreshBtn").addEventListener("click", () => refreshAll(true));'
)
html = html.replace(
    '$("driverForm").addEventListener(\'submit\', submitDriver);',
    'if ($("driverForm")) $("driverForm").addEventListener("submit", submitDriver);'
)
html = html.replace(
    '$("clientForm").addEventListener(\'submit\', submitClient);',
    'if ($("clientForm")) $("clientForm").addEventListener("submit", submitClient);'
)
html = html.replace(
    '$("withdrawForm").addEventListener(\'submit\', submitWithdraw);',
    'if ($("withdrawForm")) $("withdrawForm").addEventListener("submit", submitWithdraw);'
)
html = html.replace(
    '$("settingsForm").addEventListener(\'submit\', saveSettings);',
    'if ($("settingsForm")) $("settingsForm").addEventListener("submit", saveSettings);'
)
html = html.replace(
    '$("copyCodeBtn").addEventListener(\'click\', copyReferral);',
    'if ($("copyCodeBtn")) $("copyCodeBtn").addEventListener("click", copyReferral);'
)

p.write_text(html)
PY

echo "== clear cache =="
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f \( -name "*.pyc" -o -name "*.pyo" -o -name ".DS_Store" \) -delete

echo "== python compile =="
PYTHONPYCACHEPREFIX=/tmp/yene_pycache python3 -m py_compile \
  app.py \
  agent_dashboard_v4.py \
  yene_compat_routes.py \
  agent_wallet_v1.py \
  yene_shared.py \
  admin_compat_routes.py \
  admin_workflow_routes.py \
  admin_approval_working_routes.py

echo "== stop old server =="
pkill -f "python3 app.py" || true
pkill -f "gunicorn app:app" || true

echo
echo "Patch applied."
echo "Start server with:"
echo "  python3 app.py 2>&1 | tee yene_live.log"
echo
echo "Then open:"
echo "  http://127.0.0.1:5000/login"
echo "  http://127.0.0.1:5000/agent/dashboard"
