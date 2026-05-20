#!/usr/bin/env bash
set -u

echo "=============================="
echo " YENE DASHBOARD FREEZE SCAN"
echo "=============================="

echo ""
echo "1) Python syntax check..."
python3 -m py_compile app.py yene_compat_routes.py agent_dashboard_v4.py admin_approval_working_routes.py 2>&1 || true

echo ""
echo "2) Search for JavaScript errors / broken functions..."
grep -RInE "Uncaught|ReferenceError|TypeError|SyntaxError|TODO|placeholder|disabled|pointer-events|preventDefault|onclick|addEventListener|fetch\\(" templates static app.py *.py 2>/dev/null | head -200 || true

echo ""
echo "3) Check agent dashboard important buttons/forms..."
grep -nE "register|driver|client|wallet|withdraw|support|button|form|onclick|addEventListener|fetch" templates/agent_dashboard.html | head -250 || true

echo ""
echo "4) Check for broken API routes used by dashboard..."
grep -RInE "@app.route|@.*route|/api/agent|/api/admin|register_driver|register_client|request_withdraw|agent/me" *.py routes 2>/dev/null | head -250 || true

echo ""
echo "5) Check for duplicate helper/function definitions..."
grep -RInE "def _require_admin|def _agent_session_email|def .*register|def .*dashboard|def .*agent" *.py 2>/dev/null | sort | head -250 || true

echo ""
echo "6) Check templates exist..."
for f in templates/index.html templates/agent_dashboard.html templates/admin_dashboard.html templates/login.html templates/register.html; do
  [ -f "$f" ] && echo "OK $f" || echo "MISSING $f"
done

echo ""
echo "7) Start Flask route map check..."
python3 - <<'PY'
try:
    import app
    flask_app = getattr(app, "app", None)
    if not flask_app:
        print("ERROR: Could not find app.app")
    else:
        for r in sorted(flask_app.url_map.iter_rules(), key=lambda x: str(x)):
            s = str(r)
            if "agent" in s or "admin" in s or "api" in s or "dashboard" in s:
                print(r.endpoint, r.methods, s)
except Exception as e:
    print("ROUTE MAP ERROR:", type(e).__name__, e)
PY

echo ""
echo "8) Run app and test key endpoints locally..."
echo "If app is already running, this may show port busy. That is okay."
python3 app.py > /tmp/yene_scan_server.log 2>&1 &
PID=$!
sleep 4

echo ""
echo "--- Server log first errors ---"
cat /tmp/yene_scan_server.log | tail -80

echo ""
echo "--- HTTP tests ---"
for url in \
  http://127.0.0.1:5000/ \
  http://127.0.0.1:5000/agent/dashboard \
  http://127.0.0.1:5000/api/agent/me \
  http://127.0.0.1:5000/api/admin/overview
do
  echo ""
  echo "Testing $url"
  curl -i -s "$url" | head -40 || true
done

kill $PID 2>/dev/null || true

echo ""
echo "9) Final server log errors..."
grep -iE "error|traceback|exception|jinja|syntax|supabase|failed|missing|undefined" /tmp/yene_scan_server.log | tail -120 || true

echo ""
echo "DONE. Copy the output from any ERROR / Traceback / ReferenceError lines."
