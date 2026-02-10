"""
Shared domain utility functions.

Provides canonical email domain classification used by domain_enrichment,
phase1_identity, and report generators.
"""


# Canonical list of personal/consumer email domains common in Canada.
# Used to filter out non-business email domains from enrichment and analysis.
COMMON_CANADIAN_EMAIL_DOMAINS = frozenset([
    # Global free providers (extremely common in Canada)
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "yahoo.com",
    "icloud.com",

    # Canada-specific ISP / telco domains
    "bell.net",
    "sympatico.ca",
    "rogers.com",
    "rogers.ca",
    "shaw.ca",
    "telus.net",
    "videotron.ca",
    "mts.net",        # Manitoba
    "eastlink.ca",    # Atlantic provinces
    "nb.sympatico.ca",  # Older regional Sympatico domains
    "ns.sympatico.ca",
    "qc.sympatico.ca",
    "on.sympatico.ca",

    # Smaller / legacy Canadian consumer ISPs
    "primus.ca",
    "ciaccess.com",
    "execulink.com",
    "persona.ca",
    "nbnet.nb.ca",

    # French-Canada / Quebec usage
    "hotmail.ca",
    "live.ca",
    "videotron.qc.ca",

    # Apple localized
    "me.com",
    "mac.com",

    # Privacy-oriented (common among tech users)
    "proton.me",
    "protonmail.com",
    "tutanota.com",
    "pm.me",
])


def extract_email_domain(email: str) -> str:
    """Extract domain from email address."""
    try:
        if "@" in email:
            return email.split("@")[1].lower().strip()
        return ""
    except Exception:
        return ""


def is_personal_email_domain(domain: str) -> bool:
    """Check if domain is in the personal email domains list."""
    if not domain:
        return False
    return domain.lower().strip() in COMMON_CANADIAN_EMAIL_DOMAINS
