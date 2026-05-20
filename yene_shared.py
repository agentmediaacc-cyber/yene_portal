import re
from datetime import datetime, timedelta, timezone


APPROVED_STATUSES = {"ACTIVE", "APPROVED", "VERIFIED", "ADMIN_APPROVED"}
PENDING_STATUSES = {"PENDING", "PENDING_APPROVAL", "UNDER_REVIEW"}
ONBOARDING_STATUSES = PENDING_STATUSES | {"ONBOARDING"}
BLOCKED_STATUSES = {"BLOCKED", "REJECTED", "SUSPENDED", "DISABLED", "BANNED"}
WORKING_AGENT_STATUSES = APPROVED_STATUSES | ONBOARDING_STATUSES

NAMIBIA_REGIONS = [
    "Erongo",
    "Hardap",
    "//Kharas",
    "Kavango East",
    "Kavango West",
    "Khomas",
    "Kunene",
    "Ohangwena",
    "Omaheke",
    "Omusati",
    "Oshana",
    "Oshikoto",
    "Otjozondjupa",
    "Zambezi"
]


def clean(value):
    return str(value or "").strip()


def clean_lower(value):
    return clean(value).lower()


def clean_status(value):
    return clean(value).upper()


def safe_float(value, default=0.0):
    try:
        return float(value or 0)
    except Exception:
        return default


def is_approved(row_or_status):
    status = row_or_status.get("status") if isinstance(row_or_status, dict) else row_or_status
    return clean_status(status) in APPROVED_STATUSES


def is_pending(row_or_status):
    status = row_or_status.get("status") if isinstance(row_or_status, dict) else row_or_status
    return clean_status(status) in PENDING_STATUSES


def is_working_agent(row_or_status):
    status = row_or_status.get("status") if isinstance(row_or_status, dict) else row_or_status
    cleaned = clean_status(status)
    return not cleaned or cleaned in WORKING_AGENT_STATUSES


def is_blocked(row_or_status):
    status = row_or_status.get("status") if isinstance(row_or_status, dict) else row_or_status
    return clean_status(status) in BLOCKED_STATUSES


def profile_photo(agent):
    agent = agent or {}
    return (
        agent.get("profile_picture_url")
        or agent.get("profile_photo_url")
        or agent.get("profile_pic_path")
        or agent.get("avatar_url")
    )


def profile_missing(agent):
    agent = agent or {}
    checks = [
        ("profile picture", profile_photo(agent)),
        ("full name", agent.get("full_name")),
        ("username", agent.get("username")),
        ("phone number", agent.get("phone") or agent.get("phone_number")),
        ("town", agent.get("town")),
        ("region of operation", agent.get("operation_region") or agent.get("region")),
        ("residential address", agent.get("residential_address") or agent.get("address")),
    ]
    missing = []
    for label, value in checks:
        normalized = clean(value).lower()
        if not normalized or normalized in {"none", "pending", "n/a"}:
            missing.append(label)
    return missing


def profile_completion(agent):
    required = [
        "profile picture",
        "full name",
        "username",
        "phone number",
        "town",
        "region of operation",
        "residential address",
    ]
    missing = profile_missing(agent)
    complete_count = len([label for label in required if label not in missing])
    return round((complete_count / len(required)) * 100) if required else 100


def normalize_phone(phone):
    raw = clean(phone)
    if not raw:
        return None, "Phone number is required"
    if re.search(r"[A-Za-z]", raw):
        return None, "Phone number cannot contain letters"
    digits = re.sub(r"\D+", "", raw)
    if raw.startswith("+"):
        normalized = "+" + digits
    elif digits.startswith("264"):
        normalized = "+" + digits
    elif digits.startswith("0") and len(digits) >= 9:
        normalized = "+264" + digits[1:]
    else:
        normalized = "+" + digits
    normalized_digits = re.sub(r"\D+", "", normalized)
    if len(normalized_digits) < 8 or len(normalized_digits) > 15:
        return None, "Enter a valid phone number"
    if len(set(digits)) <= 1:
        return None, "Enter a real phone number"
    return normalized, None


def normalize_na_phone(value):
    raw = clean(value)
    if not raw:
        return None, "Phone number is required"
    if re.search(r"[A-Za-z]", raw):
        return None, "Phone number cannot contain letters"

    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return None, "Phone number is required"
    if len(set(digits)) <= 1:
        return None, "Enter a real Namibia phone number"

    if raw.startswith("+264"):
        local = digits[3:]
    elif digits.startswith("264"):
        local = digits[3:]
    elif digits.startswith("0"):
        local = digits[1:]
    else:
        local = digits

    if not local.isdigit():
        return None, "Enter a valid Namibia phone number"
    if len(local) != 9:
        return None, "Enter a valid Namibia phone number in 081... or +264... format"
    if not local.startswith("8"):
        return None, "Namibia mobile numbers must start with 08 or +2648"

    return f"+264{local}", None


def identity_values(agent):
    vals = []
    for key in ("id", "auth_id", "user_id", "email"):
        val = clean((agent or {}).get(key))
        if val and val not in vals:
            vals.append(val)
    return vals


def matches_identity(row, values, fields):
    normalized_values = {clean(v) for v in values if clean(v)}
    lower_values = {v.lower() for v in normalized_values}
    for field in fields:
        val = clean((row or {}).get(field))
        if val in normalized_values or val.lower() in lower_values:
            return True
    return False


def parse_datetime(value):
    raw = clean(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def current_week_bounds():
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=now.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return start, end


def pick_region_access_rule(rows, region="", town=""):
    region = clean(region)
    town = clean(town)
    candidates = []
    for row in rows or []:
        row_region = clean(row.get("region") or "Namibia")
        row_town = clean(row.get("town") or "All")
        score = 0
        if region and town and row_region.lower() == region.lower() and row_town.lower() == town.lower():
            score = 300
        elif region and row_region.lower() == region.lower() and row_town.lower() == "all":
            score = 200
        elif row_region.lower() == "namibia" and row_town.lower() == "all":
            score = 100
        if score:
            candidates.append((score, clean(row.get("updated_at") or row.get("created_at")), row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def region_registration_open(rows, region="", town=""):
    rule = pick_region_access_rule(rows, region=region, town=town)
    if not rule:
        return True, None
    is_active = rule.get("is_active")
    if is_active is None:
        is_active = True
    registration_open = rule.get("registration_open")
    if registration_open is None:
        registration_open = True
    allowed = bool(is_active) and bool(registration_open)
    return allowed, rule


def agent_quality_score(agent, drivers=None, clients=None, team=None):
    drivers = drivers or []
    clients = clients or []
    team = team or []
    score = 0
    score += min(30, profile_completion(agent) * 0.3)
    if is_approved(agent):
        score += 20
    score += min(20, len([r for r in drivers if is_approved(r)]) * 2)
    score += min(15, len([r for r in clients if is_approved(r)]))
    score += min(10, len(team))
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    recent = 0
    for row in drivers + clients:
        created = parse_datetime(row.get("created_at"))
        if created and created >= recent_cutoff:
            recent += 1
    score += min(5, recent)
    return round(min(100, score))
