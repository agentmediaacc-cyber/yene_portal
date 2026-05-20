#!/usr/bin/env bash

echo "=================================="
echo " YENE AGENT AUTH DEBUG"
echo "=================================="

echo ""
echo "1) Find login/session logic..."
grep -RIn \
"session\\[\\|agent_email\\|agent_id\\|logged_in\\|require_login\\|auth_id\\|current_agent\\|current_user" \
app.py *.py | head -300

echo ""
echo "2) Find require_login function..."
grep -RIn "def require_login" *.py

echo ""
echo "3) Find agent dashboard auth checks..."
grep -RIn \
"agent_me_v4\\|agent_summary_v4\\|agent_activity_v4\\|Agent login required" \
*.py

echo ""
echo "4) Start app..."
pkill -f "python3 app.py" 2>/dev/null || true

python3 app.py > /tmp/yene_auth.log 2>&1 &
PID=$!

sleep 8

echo ""
echo "5) Open login route..."
curl -I http://127.0.0.1:5000/login

echo ""
echo "6) Flask auth errors..."
grep -iE \
"login|required|session|auth|traceback|exception|supabase|error" \
/tmp/yene_auth.log | tail -120

echo ""
echo "7) Current live dashboard route..."
python3 - <<'PY'
import app

flask_app = app.app

for rule in flask_app.url_map.iter_rules():
    r = str(rule)

    if "/agent/dashboard" in r:
        print("DASHBOARD:", r, "->", rule.endpoint)

    if "/login" in r:
        print("LOGIN:", r, "->", rule.endpoint)
PY

echo ""
echo "DONE"
echo ""
echo "IMPORTANT:"
echo "We now know dashboard code is loading."
echo "The remaining issue is agent session/auth."
echo ""

kill $PID 2>/dev/null || true

