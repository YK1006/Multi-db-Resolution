import re


NULL_VALUES = {"", "null", "n/a", "none", "nan"}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(value):
    text = normalize_text(value)
    if text is None:
        return None
    text = text.lower()
    return text if EMAIL_RE.fullmatch(text) else None


def normalize_phone(value):
    text = normalize_text(value)
    if text is None:
        return None
    digits = re.sub(r"\D", "", text)
    if digits.startswith("91") and len(digits) > 10:
        digits = digits[2:]
    return digits if len(digits) >= 7 else None


def normalize_text(value):
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return None if text.lower() in NULL_VALUES else text


def normalize_record(raw_data: dict, mapping: dict[str, str]) -> dict[str, str | None]:
    result = {}
    normalizers = {"email": normalize_email, "phone": normalize_phone}
    for original_column, canonical_field in mapping.items():
        if canonical_field in {"ignore", "unknown", None}:
            continue
        raw_value = raw_data.get(original_column)
        normalizer = normalizers.get(canonical_field, normalize_text)
        normalized_value = normalizer(raw_value)
        if normalized_value is not None:
            result.setdefault(canonical_field, normalized_value)
    return result