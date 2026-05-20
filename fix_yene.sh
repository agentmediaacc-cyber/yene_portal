#!/usr/bin/env bash
set -Eeuo pipefail

cd "$HOME/Desktop/yene_portal"

echo "== activate venv =="
source venv/bin/activate

echo "== clean cache and backups =="
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f \( -name "*.pyc" -o -name "*.pyo" -o -name "*.bak_*" -o -name ".DS_Store" \) -delete

echo "== compile check =="
PYTHONPYCACHEPREFIX=/tmp/yene_pycache python -m py_compile \
  app.py \
  agent_dashboard_v4.py \
  yene_compat_routes.py \
  agent_wallet_v1.py \
  yene_shared.py \
  admin_compat_routes.py \
  admin_workflow_routes.py \
  admin_approval_working_routes.py

echo "== route check =="
PYTHONPYCACHEPREFIX=/tmp/yene_pycache python - <<'PY'
import app
wanted = {
    "/register",
    "/login",
    "/agent/dashboard",
    "/api/agent/me_v4",
    "/api/agent/summary_v4",
    "/api/agent/debug_links_v4",
    "/api/agent/activity_v4",
    "/api/agent/leaderboard_v4",
}
rules = {r.rule for r in app.app.url_map.iter_rules()}
missing = sorted(wanted - rules)
print("total routes:", len(rules))
if missing:
    print("missing routes:")
    for x in missing:
        print(" -", x)
    raise SystemExit(1)
print("all critical routes exist")
PY

echo "== scan agent dashboard bindings =="
rg -n 'fetch\(|summary_v4|me_v4|debug_links_v4|activity_v4|leaderboard_v4|menu|tab|sidebar|hamburger' \
  templates/agent_dashboard.html \
  app.py \
  agent_dashboard_v4.py \
  yene_compat_routes.py || true

echo "== done =="
