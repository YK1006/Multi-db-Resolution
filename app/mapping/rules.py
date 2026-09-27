import re
from difflib import get_close_matches


SYNONYM_MAP = {
    "email": "email", "email_id": "email", "email_address": "email", "e_mail": "email",
    "mobile_number": "phone", "contact_no": "phone", "contact_number": "phone", "phone": "phone",
    "phone_number": "phone", "mobile": "phone", "telephone": "phone",
    "full_name": "name", "name": "name", "member_id": "member_id", "user_id": "member_id",
    "customer_id": "member_id", "username": "username", "company": "company",
    "company_name": "company", "address": "address",
}
CANONICAL = {"email", "phone", "name", "username", "member_id", "company", "address"}


def _key(column: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", column.strip().lower()).strip("_")
    return re.sub(r"_+", "_", value)


def suggest_mapping_rules(columns: list[str]) -> dict[str, str]:
    normalized = {_key(key): value for key, value in SYNONYM_MAP.items()}
    suggestions = {}
    for column in columns:
        key = _key(column)
        if key in normalized:
            suggestions[column] = normalized[key]
            continue
        closest = get_close_matches(key, normalized.keys(), n=1, cutoff=0.86)
        suggestions[column] = normalized[closest[0]] if closest else "unknown"
    return suggestions