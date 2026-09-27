import json

import requests

from app.config import AI_API_KEY, AI_API_URL, AI_MODEL
from app.mapping.rules import suggest_mapping_rules

ALLOWED = {"email", "phone", "name", "username", "member_id", "company", "address", "unknown"}


def suggest_mapping_ai(columns: list[str], sample_rows: list[dict]) -> dict[str, str]:
    fallback = suggest_mapping_rules(columns)
    if not AI_API_KEY:
        return fallback
    samples = {column: [str(row.get(column, ""))[:120] for row in sample_rows[:3]] for column in columns}
    prompt = (
        "Map each input column to exactly one allowed canonical field. Return only a JSON object. "
        "Allowed values: email, phone, name, username, member_id, company, address, unknown.\n"
        f"Columns and samples: {json.dumps(samples, ensure_ascii=True)}"
    )
    try:
        response = requests.post(
            AI_API_URL,
            headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
            json={"model": AI_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0},
            timeout=15,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return fallback
        result = dict(fallback)
        for column in columns:
            value = parsed.get(column)
            if isinstance(value, str) and value.lower() in ALLOWED:
                if fallback[column] == "unknown":
                    result[column] = value.lower()
        return result
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return fallback