"""
Shared address utility functions.

Provides address cleaning and normalization used by multiple Cloud Functions:
- contact_extraction
- address_geocoding
- address_verification
- report_generator_origination
- report_generator_skiptrace
"""

import re


def clean_address_for_geocoding(address: str) -> str:
    """
    Clean address string to improve geocoding accuracy.
    Removes copyright text, years, company names, and other junk
    that appears before the actual civic address.
    """
    # Remove common prefixes
    patterns_to_remove = [
        r"^.*?©.*?Reserved\.\s*",  # Copyright text
        r"^.*?\d{4}\s+.*?Reserved\.\s*",  # Year + Reserved
        r"^.*?HEAD OFFICE\.\s*",  # HEAD OFFICE label
        r"^.*?OFFICE\.\s*",  # OFFICE label
        r"^.*?Contact:\s*",  # Contact: prefix
    ]

    for pattern in patterns_to_remove:
        address = re.sub(pattern, "", address, flags=re.IGNORECASE)

    # Trim and clean up
    address = address.strip()

    return address
