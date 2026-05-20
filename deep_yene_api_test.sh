#!/usr/bin/env bash

echo "================================="
echo " YENE LIVE API + FREEZE TEST"
echo "================================="

echo ""
echo "1) Kill old Flask servers..."
pkill -f "python3 app.py" 2>/dev/null || true
pkill -f "flask run" 2>/dev/null || true

sleep 2

echo ""
echo "2) Start fresh server..."
python3 app.py > /tmp/yene_live.log 2>&1 &
PID=$!

echo "Server PID: $PID"

sleep 8

echo ""
echo "3) Test homepage..."
curl -I http://127.0.0.1:5000/

echo ""
echo "4) Test dashboard page..."
curl -I http://127.0.0.1:5000/agent/dashboard

echo ""
echo "5) Test REAL dashboard APIs..."
for url in \
http://127.0.0.1:5000/api/agent/me_v4 \
http://127.0.0.1:5000/api/agent/summary_v4 \
http://127.0.0.1:5000/api/agent/activity_v4 \
http://127.0.0.1:5000/api/agent/team_v4 \
http://127.0.0.1:5000/api/agent/wallet_history_v4 \
http://127.0.0.1:5000/api/agent/leaderboard_v4
do
  echo ""
  echo "=============================="
  echo "TESTING:"
  echo "$url"
  echo "=============================="

  curl -i -m 15 "$url"

  echo ""
  echo ""
done

echo ""
echo "6) Search for crashing errors..."
grep -iE \
"traceback|error|exception|supabase|failed|timeout|duplicate|undefined|500|refused|invalid" \
/tmp/yene_live.log | tail -200

echo ""
echo "7) Show latest server logs..."
tail -120 /tmp/yene_live.log

echo ""
echo "8) Route conflict check..."
grep -R \"@app.route\" *.py | grep \"agent/dashboard\\|api/agent\" | wc -l

echo ""
echo "DONE"
echo ""
echo "IMPORTANT:"
echo "If you see:"
echo "- 500 INTERNAL SERVER ERROR"
echo "- timeout"
echo "- duplicate endpoint"
echo "- Supabase error"
echo "- relation does not exist"
echo "- column does not exist"
echo ""
echo "copy it here."

