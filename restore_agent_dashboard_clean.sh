#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/Desktop/yene_portal"
source venv/bin/activate

echo "== find latest dashboard backup =="
LATEST_HTML_BACKUP="$(ls -1t templates/agent_dashboard.html.bak_* templates/agent_dashboard.html.BACKUP 2>/dev/null | head -n 1 || true)"
LATEST_PY_BACKUP="$(ls -1t agent_dashboard_v4.py.bak_* agent_dashboard_v4.py.BACKUP 2>/dev/null | head -n 1 || true)"

if [ -z "${LATEST_HTML_BACKUP}" ]; then
  echo "No agent dashboard HTML backup found."
  exit 1
fi

echo "Using HTML backup: $LATEST_HTML_BACKUP"
cp "$LATEST_HTML_BACKUP" templates/agent_dashboard.html

if [ -n "${LATEST_PY_BACKUP}" ]; then
  echo "Using Python backup: $LATEST_PY_BACKUP"
  cp "$LATEST_PY_BACKUP" agent_dashboard_v4.py
fi

python3 - <<'PY'
from pathlib import Path
import re

# --- clean dashboard HTML carefully ---
p = Path("templates/agent_dashboard.html")
html = p.read_text()

# remove known broken injected comment patterns if present
replacements = {
    'async function // refreshAll disabled(showToast=true){': 'async function refreshAll(showToast=true){',
    'await // refreshAll disabled(false);': 'await refreshAll(false);',
    '() => // refreshAll disabled(true)': '() => refreshAll(true)',
    'if (typeof loadMessages === \'function\') await // loadMessages disabled;': 'if (typeof loadMessages === "function") await loadMessages();',
}

for old, new in replacements.items():
    html = html.replace(old, new)

# remove duplicated if wrapper caused by bad patch
html = html.replace(
    'if ($("refreshBtn")) if ($("refreshBtn")) $("refreshBtn").addEventListener("click", () => refreshAll(true));',
    'if ($("refreshBtn")) $("refreshBtn").addEventListener("click", () => refreshAll(true));'
)

# make a few event bindings safe if elements are missing
safe_ids = [
    "openCameraBtn", "capturePhotoBtn", "retakePhotoBtn",
    "uploadPhotoBtn", "profilePhotoFile"
]
for el_id in safe_ids:
    html = html.replace(
        f'$("{el_id}").addEventListener',
        f'if ($("{el_id}")) $("{el_id}").addEventListener'
    )

# remove only the duplicate truth overview loader block marker if it remains as comment clutter
html = html.replace("/* removed duplicate truth overview loader block */", "")

p.write_text(html)

# --- light backend stabilization only, without breaking JS ---
p = Path("agent_dashboard_v4.py")
text = p.read_text()

text = text.replace(
    'def _select_all(table, limit=10000, order_col="created_at", desc=True):',
    'def _select_all(table, limit=300, order_col="updated_at", desc=True):'
)
text = text.replace(
    'def _select_all(table, limit=300, order_col="created_at", desc=True):',
    'def _select_all(table, limit=300, order_col="updated_at", desc=True):'
)

# use updated_at for agent_wallets ordering problem
text = text.replace('_select_all("agent_wallets", 5000)', '_select_all("agent_wallets", 200, "updated_at", True)')
text = text.replace('_select_all("agent_wallets", 300)', '_select_all("agent_wallets", 200, "updated_at", True)')

# reduce heavy get_agent retry pressure only
text = text.replace(
    '_execute_with_retry("get_agent_profile", build, retries=3, delay=0.35)',
    '_execute_with_retry("get_agent_profile", build, retries=0, delay=0.15)'
)
text = text.replace(
    '_execute_with_retry("get_agent_profile", build, retries=1, delay=0.20)',
    '_execute_with_retry("get_agent_profile", build, retries=0, delay=0.15)'
)

p.write_text(text)

# --- app timeout tighten ---
p = Path("app.py")
app_text = p.read_text()
app_text = app_text.replace("timeout=10,", "timeout=5,")
p.write_text(app_text)
PY

echo "== clear cache =="
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f \( -name "*.pyc" -o -name "*.pyo" -o -name ".DS_Store" \) -delete

echo "== compile check =="
PYTHONPYCACHEPREFIX=/tmp/yene_pycache python3 -m py_compile \
  app.py \
  agent_dashboard_v4.py \
  yene_compat_routes.py \
  agent_wallet_v1.py \
  yene_shared.py \
  admin_compat_routes.py \
  admin_workflow_routes.py \
  admin_approval_working_routes.py

echo "== restart clean =="
pkill -f "python3 app.py" || true
pkill -f "gunicorn app:app" || true

echo
echo "Recovered."
echo "Now run:"
echo "python3 app.py 2>&1 | tee yene_live.log"
