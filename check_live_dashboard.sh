#!/usr/bin/env bash

echo "======================================"
echo " YENE LIVE DASHBOARD DETECTOR"
echo "======================================"

echo ""
echo "1) Active dashboard files..."
find . -iname "*agent_dashboard*" | sort

echo ""
echo "2) Which template contains current text..."
grep -R "Welcome, Agent" templates 2>/dev/null
grep -R "Update profile" templates 2>/dev/null
grep -R "Jobs paused for stability" templates 2>/dev/null
grep -R "Messages paused for stability" templates 2>/dev/null
grep -R "Alerts paused for stability" templates 2>/dev/null

echo ""
echo "3) Which file renders /agent/dashboard ..."
grep -R "agent/dashboard" *.py routes 2>/dev/null

echo ""
echo "4) Which template is returned by Flask..."
grep -R "render_template.*agent_dashboard" *.py routes 2>/dev/null

echo ""
echo "5) Check if old dashboard files still exist..."
find . -iname "*dashboard_v2*" -o -iname "*dashboard_full*" -o -iname "*backup*" | head -100

echo ""
echo "6) Start server fresh..."
pkill -f "python3 app.py" 2>/dev/null || true
sleep 2

python3 app.py > /tmp/yene_live_check.log 2>&1 &
PID=$!

sleep 8

echo ""
echo "7) Test live dashboard response..."
curl -s http://127.0.0.1:5000/agent/dashboard | head -80

echo ""
echo ""
echo "8) Test key APIs..."
for url in \
http://127.0.0.1:5000/api/agent/me_v4 \
http://127.0.0.1:5000/api/agent/summary_v4 \
http://127.0.0.1:5000/api/agent/activity_v4 \
http://127.0.0.1:5000/api/agent/team_v4 \
http://127.0.0.1:5000/api/agent/jobs \
http://127.0.0.1:5000/api/agent/messages
do
  echo ""
  echo "=============================="
  echo "$url"
  echo "=============================="
  curl -s "$url"
  echo ""
done

echo ""
echo "9) Flask errors..."
grep -iE "error|traceback|exception|jinja|failed|timeout" /tmp/yene_live_check.log | tail -100

echo ""
echo "10) Running route map..."
python3 - <<'PY'
import app

flask_app = app.app

for rule in sorted(flask_app.url_map.iter_rules(), key=lambda x: str(x)):
    r = str(rule)

    if "agent/dashboard" in r:
        print("DASHBOARD ROUTE:", r, "->", rule.endpoint)

    if "/api/agent/" in r:
        print("API:", r, "->", rule.endpoint)
PY

echo ""
echo "DONE"
echo ""
echo "IMPORTANT:"
echo "If you still see:"
echo "- Welcome, Agent"
echo "- paused for stability"
echo "- Loading activity"
echo ""
echo "then Flask is still using old template/code."
echo ""
echo "Copy the output here."

kill $PID 2>/dev/null || true

