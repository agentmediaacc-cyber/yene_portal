from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app


ROUTES = [
    "/api/admin/approve_driver",
    "/api/admin/approve_client",
    "/api/admin/payout_workflow",
    "/api/admin/region_settings",
    "/api/admin/region_settings/upsert",
    "/api/admin/payment_rules",
    "/api/admin/payment_rules/upsert",
    "/api/agent/register_driver_v4",
    "/api/agent/register_client_v4",
    "/api/agent/summary_v4",
    "/api/agent/activity_v4",
    "/api/agent/me_v4",
]

TEMPLATES = [
    "templates/agent_dashboard.html",
    "templates/admin_dashboard.html",
]


def check_routes():
    ok = True
    print("Route audit")
    for path in ROUTES:
        rules = [r for r in app.app.url_map.iter_rules() if str(r) == path]
        endpoints = [r.endpoint for r in rules]
        print(path, len(rules), endpoints)
        if len(rules) != 1:
            ok = False
    return ok


def check_template_js():
    ok = True
    print("Template JS")
    for file in TEMPLATES:
        html = Path(file).read_text()
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
        out = f"/tmp/{Path(file).stem}.js"
        Path(out).write_text("\n".join(scripts))
        print(file, "scripts", len(scripts))
        result = subprocess.run(["node", "--check", out], capture_output=True, text=True)
        if result.returncode != 0:
            ok = False
            print(result.stdout)
            print(result.stderr)
    return ok


def main():
    routes_ok = check_routes()
    js_ok = check_template_js()
    overall = routes_ok and js_ok
    print("SUMMARY", "OK" if overall else "FAIL")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
