#!/usr/bin/env bash
set -e

cd "$HOME/Desktop/yene_portal"
source venv/bin/activate

echo "=== BACKUP ==="
cp agent_dashboard_v4.py agent_dashboard_v4.py.BACKUP
cp templates/agent_dashboard.html templates/agent_dashboard.html.BACKUP

python3 - <<'PY'
from pathlib import Path

# =========================
# FIX 1: WALLET COLUMN ERROR
# =========================
p = Path("agent_dashboard_v4.py")
text = p.read_text()

text = text.replace('order_col="created_at"', 'order_col="updated_at"')

# =========================
# FIX 2: STOP FULL SCANS
# =========================
text = text.replace('limit=10000', 'limit=100')
text = text.replace('limit=5000', 'limit=100')

# =========================
# FIX 3: DISABLE RETRIES (they spam DB)
# =========================
text = text.replace('retries=3', 'retries=0')
text = text.replace('retries=2', 'retries=0')
text = text.replace('retries=1', 'retries=0')

# =========================
# FIX 4: DISABLE MESSAGES (they spam API)
# =========================
text = text.replace(
    'loadMessages',
    '/*disabledMessages*/ loadMessages'
)

p.write_text(text)

# =========================
# FIX 5: FRONTEND FREEZE FIX
# =========================
p = Path("templates/agent_dashboard.html")
html = p.read_text()

# remove aggressive polling
import re
html = re.sub(r"setInterval\([^)]*\);", "// removed auto polling", html)

# disable messages loading
html = html.replace("loadMessages()", "// loadMessages disabled")

# reduce refresh calls
html = html.replace("refreshAll(", "// refreshAll disabled(")

# fix menu crash if element missing
html = html.replace(
    '$("refreshBtn").addEventListener',
    'if ($("refreshBtn")) $("refreshBtn").addEventListener'
)

p.write_text(html)

PY

echo "=== CLEAR CACHE ==="
find . -name "__pycache__" -exec rm -rf {} +
find . -name "*.pyc" -delete

echo "=== RESTART CLEAN ==="
pkill -f app.py || true

echo
echo "START APP NOW:"
echo "python3 app.py"
