from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app


ROUTES = [
    "/",
    "/login",
    "/agent/dashboard",
    "/dashboard/admin",
    "/api/agent/me_v4",
    "/api/agent/summary_v4",
    "/api/agent/activity_v4",
    "/api/agent/register_driver_v4",
    "/api/agent/register_client_v4",
    "/api/admin/approve_driver",
    "/api/admin/approve_client",
    "/api/admin/reject_driver",
    "/api/admin/reject_client",
    "/api/admin/payment_rules",
    "/api/admin/payment_rules/upsert",
    "/api/admin/region_settings",
    "/api/admin/region_settings/upsert",
    "/api/admin/payout_workflow",
    "/api/admin/finance/summary",
    "/api/admin/agents",
    "/api/admin/drivers",
    "/api/admin/clients",
]

LIVE_TEMPLATES = [
    ROOT / "templates" / "agent_dashboard.html",
    ROOT / "templates" / "admin_dashboard.html",
    ROOT / "templates" / "index.html",
]

JS_TEMPLATES = [
    ROOT / "templates" / "agent_dashboard.html",
    ROOT / "templates" / "admin_dashboard.html",
]

REQUIRED_SECTIONS = {
    "templates/agent_dashboard.html": [
        "Register Driver",
        "Register Client",
        "Wallet",
        "My Registration Log",
    ],
    "templates/admin_dashboard.html": [
        "Region Access",
        "Finance",
        "Approvals",
    ],
}

BANNED_PATTERNS = [
    r"developer",
    r"debug",
    r"traceback",
    r"internal server error",
    r"placeholder",
    r"fake",
    r"lorem",
    r"ai generated",
    r"paused for stability",
    r"todo",
]


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def fail(msg: str, failures: list[str]) -> None:
    failures.append(msg)
    print("FAIL", msg)


def pass_msg(msg: str) -> None:
    print("PASS", msg)


def check_routes(failures: list[str]) -> None:
    for path in ROUTES:
        rules = [r for r in app.app.url_map.iter_rules() if str(r) == path]
        endpoints = [r.endpoint for r in rules]
        print("ROUTE", path, len(rules), endpoints)
        if len(rules) != 1:
            fail(f"{path} has {len(rules)} handlers", failures)


def check_no_legacy_approval_key(failures: list[str]) -> None:
    banned = "approval" + "_state"
    targets = list(ROOT.glob("*.py")) + list((ROOT / "templates").glob("*.html")) + list((ROOT / "sql").glob("*.sql"))
    for path in targets:
        text = path.read_text(errors="ignore")
        if banned in text:
            fail(f"{banned} found in {rel(path)}", failures)


def check_js(failures: list[str]) -> None:
    for path in JS_TEMPLATES:
        html = path.read_text()
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
        out = Path("/tmp") / f"{path.stem}.js"
        out.write_text("\n".join(scripts))
        print("JS", rel(path), "scripts", len(scripts))
        result = subprocess.run(["node", "--check", str(out)], capture_output=True, text=True)
        if result.returncode != 0:
            fail(f"node --check failed for {rel(path)}", failures)
            print(result.stdout)
            print(result.stderr)


def check_required_sections(failures: list[str]) -> None:
    for path_str, labels in REQUIRED_SECTIONS.items():
        path = ROOT / path_str
        text = path.read_text()
        for label in labels:
            if label not in text:
                fail(f"{label} missing from {path_str}", failures)
            else:
                pass_msg(f"{label} present in {path_str}")


def check_banned_phrases(failures: list[str]) -> None:
    scan_paths = LIVE_TEMPLATES
    static_dir = ROOT / "static"
    if static_dir.exists():
        scan_paths.extend([p for p in static_dir.rglob("*") if p.is_file()])
    for path in scan_paths:
        text = path.read_text(errors="ignore")
        lowered = text.lower()
        for pattern in BANNED_PATTERNS:
            if re.search(pattern, lowered):
                fail(f"banned phrase /{pattern}/ found in {rel(path)}", failures)


def main() -> None:
    failures: list[str] = []
    check_routes(failures)
    check_no_legacy_approval_key(failures)
    check_js(failures)
    check_required_sections(failures)
    check_banned_phrases(failures)
    print("SUMMARY", "PASS" if not failures else "FAIL")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
