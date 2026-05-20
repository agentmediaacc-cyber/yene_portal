#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/Desktop/yene_portal"
source venv/bin/activate

echo "== backup files =="
cp app.py app.py.bak_fix_$(date +%Y%m%d_%H%M%S)
cp agent_dashboard_v4.py agent_dashboard_v4.py.bak_fix_$(date +%Y%m%d_%H%M%S)
cp templates/agent_dashboard.html templates/agent_dashboard.html.bak_fix_$(date +%Y%m%d_%H%M%S)

python3 - <<'PY'
from pathlib import Path
import re

# ---------- patch agent_dashboard_v4.py ----------
p = Path("agent_dashboard_v4.py")
text = p.read_text()

# lower retry count for Supabase execute_with_retry
text = text.replace("def _execute_with_retry(label, query_factory, retries=2, delay=0.25):",
                    "def _execute_with_retry(label, query_factory, retries=1, delay=0.25):")

# reduce get_agent retries/delay
text = text.replace('_execute_with_retry("get_agent_profile", build, retries=3, delay=0.35)',
                    '_execute_with_retry("get_agent_profile", build, retries=1, delay=0.20)')

# reduce giant selects from 10000 to 1000
text = text.replace("_select_all(table, 10000)", "_select_all(table, 1000)")
text = text.replace('_select_all("agent_profiles", 10000)', '_select_all("agent_profiles", 1000)')
text = text.replace('_select_all("drivers", 10000)', '_select_all("drivers", 1000)')
text = text.replace('_select_all("clients", 10000)', '_select_all("clients", 1000)')

p.write_text(text)

# ---------- patch app.py ----------
p = Path("app.py")
text = p.read_text()

# enforce requests timeout=5 in token auth if not already smaller
text = text.replace("timeout=10,", "timeout=5,")

p.write_text(text)

# ---------- patch templates/agent_dashboard.html ----------
p = Path("templates/agent_dashboard.html")
text = p.read_text()

# slow down noisy polling intervals
text = re.sub(r"setInterval\(([^,]+),\s*1000\)", r"setInterval(\1, 15000)", text)
text = re.sub(r"setInterval\(([^,]+),\s*1500\)", r"setInterval(\1, 15000)", text)
text = re.sub(r"setInterval\(([^,]+),\s*2000\)", r"setInterval(\1, 15000)", text)
text = re.sub(r"setInterval\(([^,]+),\s*2500\)", r"setInterval(\1, 15000)", text)
text = re.sub(r"setInterval\(([^,]+),\s*3000\)", r"setInterval(\1, 15000)", text)
text = re.sub(r"setInterval\(([^,]+),\s*5000\)", r"setInterval(\1, 15000)", text)

# inject safe fetch helper once, near top of first <script>
if "async function safeFetchJson(" not in text:
    text = text.replace(
        "<script>",
        """<script>
async function safeFetchJson(url, opts = {}) {
  try {
    const res = await fetch(url, opts);
    if (!res.ok) {
      console.warn("API failed", url, res.status);
      return null;
    }
    return await res.json();
  } catch (err) {
    console.warn("Fetch error", url, err);
    return null;
  }
}
""",
        1
    )

# convert direct fetch(...).then(r=>r.json()) patterns where easy
text = re.sub(
    r'fetch\(([^)]+)\)\.then\(\s*r\s*=>\s*r\.json\(\)\s*\)',
    r'safeFetchJson(\1)',
    text
)

p.write_text(text)
PY

echo "== clean caches =="
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

echo "== restart app =="
pkill -f "python3 app.py" || true
pkill -f "gunicorn app:app" || true

echo
echo "Patch complete."
echo "Now run:"
echo "  python3 app.py"
echo
echo "Then in another terminal test:"
echo "  curl -i http://127.0.0.1:5000/agent/dashboard"
echo "  curl -i http://127.0.0.1:5000/api/agent/summary_v4"
