"""Tests for gcp/shared/domain_utils.py"""

from domain_utils import (
    COMMON_CANADIAN_EMAIL_DOMAINS,
    extract_email_domain,
    is_personal_email_domain,
)


class TestExtractEmailDomain:
    """Tests for the extract_email_domain function."""

    def test_standard_email(self):
        assert extract_email_domain("user@example.com") == "example.com"

    def test_gmail(self):
        assert extract_email_domain("john.doe@gmail.com") == "gmail.com"

    def test_uppercase_email(self):
        assert extract_email_domain("USER@COMPANY.CA") == "company.ca"

    def test_email_with_whitespace(self):
        assert extract_email_domain("  user@example.com  ") == "example.com"

    def test_no_at_sign(self):
        assert extract_email_domain("not-an-email") == ""

    def test_empty_string(self):
        assert extract_email_domain("") == ""

    def test_multiple_at_signs(self):
        # Takes domain after first @
        result = extract_email_domain("user@host@domain.com")
        assert result == "host"

    def test_subdomain_email(self):
        assert extract_email_domain("user@mail.company.ca") == "mail.company.ca"


class TestIsPersonalEmailDomain:
    """Tests for the is_personal_email_domain function."""

    # Global free providers
    def test_gmail_is_personal(self):
        assert is_personal_email_domain("gmail.com") is True

    def test_hotmail_is_personal(self):
        assert is_personal_email_domain("hotmail.com") is True

    def test_outlook_is_personal(self):
        assert is_personal_email_domain("outlook.com") is True

    def test_yahoo_is_personal(self):
        assert is_personal_email_domain("yahoo.com") is True

    def test_icloud_is_personal(self):
        assert is_personal_email_domain("icloud.com") is True

    # Canadian ISP domains
    def test_bell_is_personal(self):
        assert is_personal_email_domain("bell.net") is True

    def test_rogers_is_personal(self):
        assert is_personal_email_domain("rogers.com") is True

    def test_shaw_is_personal(self):
        assert is_personal_email_domain("shaw.ca") is True

    def test_telus_is_personal(self):
        assert is_personal_email_domain("telus.net") is True

    def test_videotron_is_personal(self):
        assert is_personal_email_domain("videotron.ca") is True

    # Privacy-oriented
    def test_protonmail_is_personal(self):
        assert is_personal_email_domain("protonmail.com") is True

    def test_proton_me_is_personal(self):
        assert is_personal_email_domain("proton.me") is True

    # Business domains should NOT be personal
    def test_corporate_domain_is_not_personal(self):
        assert is_personal_email_domain("acmecorp.com") is False

    def test_government_domain_is_not_personal(self):
        assert is_personal_email_domain("canada.gc.ca") is False

    def test_custom_domain_is_not_personal(self):
        assert is_personal_email_domain("mycompany.ca") is False

    # Edge cases
    def test_empty_string(self):
        assert is_personal_email_domain("") is False

    def test_case_insensitive(self):
        assert is_personal_email_domain("GMAIL.COM") is True
        assert is_personal_email_domain("Gmail.Com") is True

    def test_whitespace_handling(self):
        assert is_personal_email_domain("  gmail.com  ") is True


class TestCommonCanadianEmailDomains:
    """Tests for the COMMON_CANADIAN_EMAIL_DOMAINS constant."""

    def test_is_frozenset(self):
        assert isinstance(COMMON_CANADIAN_EMAIL_DOMAINS, frozenset)

    def test_contains_major_providers(self):
        expected = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com"}
        assert expected.issubset(COMMON_CANADIAN_EMAIL_DOMAINS)

    def test_contains_canadian_isps(self):
        expected = {"bell.net", "rogers.com", "shaw.ca", "telus.net", "videotron.ca"}
        assert expected.issubset(COMMON_CANADIAN_EMAIL_DOMAINS)

    def test_all_lowercase(self):
        for domain in COMMON_CANADIAN_EMAIL_DOMAINS:
            assert domain == domain.lower(), f"Domain {domain} is not lowercase"
