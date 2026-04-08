"""
Shared LLM-facing input validation: NFKC normalization, whitespace collapse,
Unicode letter allow-list, and Canadian province token parsing.

Used by api_gateway and query_constructor (copied into each function bundle).
"""

import unicodedata

# Max lengths after NFKC normalize + whitespace collapse (security / LLM prompt bounds)
MAX_FULL_NAME_LEN = 200
MAX_CITY_LEN = 120
MAX_PROVINCE_LEN = 40

PROVINCE_NAMES = {
    "ON": "Ontario",
    "BC": "British Columbia",
    "AB": "Alberta",
    "QC": "Quebec",
    "MB": "Manitoba",
    "SK": "Saskatchewan",
    "NS": "Nova Scotia",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "PE": "Prince Edward Island",
    "NT": "Northwest Territories",
    "YT": "Yukon",
    "NU": "Nunavut",
}


def is_allowed_llm_input_char(c: str) -> bool:
    """Allow Unicode letters (L*), whitespace, hyphen, apostrophe, period."""
    if c in " '-.":
        return True
    if c.isspace():
        return True
    cat = unicodedata.category(c)
    return cat.startswith("L")


def normalize_and_validate_allowlist_text(raw: str, max_len: int) -> str | None:
    """
    NFKC-normalize, collapse whitespace, enforce per-character allow-list and max length.
    Returns normalized string or None if invalid.
    """
    if not raw:
        return None
    t = unicodedata.normalize("NFKC", raw).strip()
    if not t:
        return None
    collapsed = " ".join(t.split())
    if len(collapsed) > max_len:
        return None
    for ch in collapsed:
        if not is_allowed_llm_input_char(ch):
            return None
    return collapsed


def normalize_province_for_query(province: str) -> tuple[str | None, str | None]:
    """
    Returns (normalized_province_token, error_message).
    Accepts a 2-letter code in PROVINCE_NAMES or a full-name string matching the allow-list.
    """
    if not province:
        return "", None
    p = unicodedata.normalize("NFKC", province).strip()
    if not p:
        return "", None
    if len(p) == 2 and p.isalpha():
        code = p.upper()
        if code in PROVINCE_NAMES:
            return code, None
        return None, "Invalid province code"
    validated = normalize_and_validate_allowlist_text(p, MAX_PROVINCE_LEN)
    if validated is None:
        return None, "Invalid province"
    return validated, None
