"""Tests for gcp/shared/retry_utils.py"""

import sys
from unittest.mock import MagicMock, patch

import pytest
import requests
from retry_utils import (
    EmptyLLMResponseError,
    RateLimitExhaustedError,
    RetryConfig,
    extract_retry_after,
    is_retryable_error,
    retry_with_backoff,
)


class TestRetryConfig:
    """Tests for the RetryConfig dataclass."""

    def test_defaults(self):
        config = RetryConfig()
        assert config.max_attempts == 3
        assert config.base_delay_seconds == 1.0
        assert config.max_delay_seconds == 30.0
        assert config.jitter is True

    def test_custom_values(self):
        config = RetryConfig(max_attempts=5, base_delay_seconds=2.0, max_delay_seconds=60.0, jitter=False)
        assert config.max_attempts == 5
        assert config.base_delay_seconds == 2.0
        assert config.max_delay_seconds == 60.0
        assert config.jitter is False


class TestCustomExceptions:
    """Tests for custom exception classes."""

    def test_empty_llm_response_error_is_exception(self):
        assert issubclass(EmptyLLMResponseError, Exception)

    def test_rate_limit_exhausted_error_is_exception(self):
        assert issubclass(RateLimitExhaustedError, Exception)

    def test_empty_llm_response_error_message(self):
        err = EmptyLLMResponseError("Empty response from Vertex AI")
        assert str(err) == "Empty response from Vertex AI"


class TestIsRetryableError:
    """Tests for the is_retryable_error function."""

    def test_empty_llm_response_is_retryable(self):
        assert is_retryable_error(EmptyLLMResponseError("empty")) is True

    def test_connection_error_is_retryable(self):
        assert is_retryable_error(requests.exceptions.ConnectionError()) is True

    def test_timeout_is_retryable(self):
        assert is_retryable_error(requests.exceptions.Timeout()) is True

    def test_http_429_is_retryable(self):
        response = MagicMock()
        response.status_code = 429
        err = requests.exceptions.HTTPError(response=response)
        assert is_retryable_error(err) is True

    def test_http_500_is_retryable(self):
        response = MagicMock()
        response.status_code = 500
        err = requests.exceptions.HTTPError(response=response)
        assert is_retryable_error(err) is True

    def test_http_502_is_retryable(self):
        response = MagicMock()
        response.status_code = 502
        err = requests.exceptions.HTTPError(response=response)
        assert is_retryable_error(err) is True

    def test_http_503_is_retryable(self):
        response = MagicMock()
        response.status_code = 503
        err = requests.exceptions.HTTPError(response=response)
        assert is_retryable_error(err) is True

    def test_http_400_is_not_retryable(self):
        response = MagicMock()
        response.status_code = 400
        err = requests.exceptions.HTTPError(response=response)
        assert is_retryable_error(err) is False

    def test_http_401_is_not_retryable(self):
        response = MagicMock()
        response.status_code = 401
        err = requests.exceptions.HTTPError(response=response)
        assert is_retryable_error(err) is False

    def test_http_404_is_not_retryable(self):
        response = MagicMock()
        response.status_code = 404
        err = requests.exceptions.HTTPError(response=response)
        assert is_retryable_error(err) is False

    def test_error_string_429_fallback(self):
        err = Exception("got 429 too many requests")
        assert is_retryable_error(err) is True

    def test_error_string_timeout_fallback(self):
        err = Exception("request timeout after 60s")
        assert is_retryable_error(err) is True

    def test_error_string_resource_exhausted_fallback(self):
        err = Exception("resource exhausted")
        assert is_retryable_error(err) is True

    def test_generic_value_error_is_not_retryable(self):
        assert is_retryable_error(ValueError("bad value")) is False

    def test_generic_key_error_is_not_retryable(self):
        assert is_retryable_error(KeyError("missing")) is False


class TestExtractRetryAfter:
    """Tests for the extract_retry_after function."""

    def test_returns_none_for_non_http_error(self):
        assert extract_retry_after(ValueError("test")) is None

    def test_extracts_integer_retry_after(self):
        response = MagicMock()
        response.headers = {"Retry-After": "30"}
        err = requests.exceptions.HTTPError(response=response)
        assert extract_retry_after(err) == 30.0

    def test_extracts_float_retry_after(self):
        response = MagicMock()
        response.headers = {"Retry-After": "1.5"}
        err = requests.exceptions.HTTPError(response=response)
        assert extract_retry_after(err) == 1.5

    def test_returns_none_when_no_retry_after_header(self):
        response = MagicMock()
        response.headers = {}
        err = requests.exceptions.HTTPError(response=response)
        assert extract_retry_after(err) is None

    def test_returns_none_for_no_response(self):
        err = requests.exceptions.HTTPError()
        err.response = None
        assert extract_retry_after(err) is None


class TestRetryWithBackoff:
    """Tests for the retry_with_backoff function."""

    def test_succeeds_on_first_attempt(self):
        fn = MagicMock(return_value="success")
        result = retry_with_backoff(fn, RetryConfig(max_attempts=3))
        assert result == "success"
        assert fn.call_count == 1

    @patch("retry_utils.time.sleep")
    def test_retries_on_retryable_error(self, mock_sleep):
        fn = MagicMock(side_effect=[requests.exceptions.ConnectionError(), "success"])
        result = retry_with_backoff(fn, RetryConfig(max_attempts=3, jitter=False))
        assert result == "success"
        assert fn.call_count == 2

    @patch("retry_utils.time.sleep")
    def test_raises_after_max_attempts(self, mock_sleep):
        fn = MagicMock(side_effect=requests.exceptions.ConnectionError("fail"))
        with pytest.raises(requests.exceptions.ConnectionError):
            retry_with_backoff(fn, RetryConfig(max_attempts=3, jitter=False))
        assert fn.call_count == 3

    def test_raises_immediately_on_non_retryable_error(self):
        response = MagicMock()
        response.status_code = 400
        fn = MagicMock(side_effect=requests.exceptions.HTTPError(response=response))
        with pytest.raises(requests.exceptions.HTTPError):
            retry_with_backoff(fn, RetryConfig(max_attempts=3))
        assert fn.call_count == 1

    @patch("retry_utils.time.sleep")
    def test_exponential_backoff_delays(self, mock_sleep):
        fn = MagicMock(
            side_effect=[
                requests.exceptions.ConnectionError(),
                requests.exceptions.ConnectionError(),
                "success",
            ]
        )
        retry_with_backoff(fn, RetryConfig(max_attempts=3, base_delay_seconds=1.0, jitter=False))
        # Attempt 0: delay = min(1.0 * 2^0, 30) = 1.0
        # Attempt 1: delay = min(1.0 * 2^1, 30) = 2.0
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0][0][0] == 1.0
        assert mock_sleep.call_args_list[1][0][0] == 2.0

    @patch("retry_utils.time.sleep")
    def test_max_delay_cap(self, mock_sleep):
        fn = MagicMock(
            side_effect=[
                requests.exceptions.ConnectionError(),
                "success",
            ]
        )
        retry_with_backoff(
            fn,
            RetryConfig(max_attempts=2, base_delay_seconds=100.0, max_delay_seconds=5.0, jitter=False),
        )
        # delay = min(100 * 2^0, 5.0) = 5.0
        assert mock_sleep.call_args_list[0][0][0] == 5.0


class TestRetryUtilsCoverageGaps:
    """Tests covering previously-uncovered lines in retry_utils.py."""

    # ── extract_retry_after: HTTP-date format (lines 66-78) ──

    def test_extract_retry_after_http_date_format(self):
        """Retry-After with an HTTP-date string is parsed into seconds."""
        from datetime import UTC, datetime, timedelta
        from email.utils import format_datetime

        # Set the Retry-After to 60 seconds from now
        future = datetime.now(UTC) + timedelta(seconds=60)
        response = MagicMock()
        response.headers = {"Retry-After": format_datetime(future)}
        err = requests.exceptions.HTTPError(response=response)

        result = extract_retry_after(err)
        assert result is not None
        # Should be approximately 60 seconds (allow some tolerance)
        assert 55 <= result <= 65

    def test_extract_retry_after_http_date_in_past_returns_zero(self):
        """Retry-After with a past HTTP-date returns 0 (clamped by max(0, ...))."""
        from datetime import UTC, datetime, timedelta
        from email.utils import format_datetime

        past = datetime.now(UTC) - timedelta(seconds=30)
        response = MagicMock()
        response.headers = {"Retry-After": format_datetime(past)}
        err = requests.exceptions.HTTPError(response=response)

        result = extract_retry_after(err)
        assert result == 0

    def test_extract_retry_after_invalid_non_numeric_non_date(self):
        """Retry-After with a completely invalid string returns None (lines 66-78)."""
        response = MagicMock()
        response.headers = {"Retry-After": "not-a-number-or-date!!!"}
        err = requests.exceptions.HTTPError(response=response)
        assert extract_retry_after(err) is None

    # ── is_retryable_error: Google API exceptions (lines 108-129) ──
    # Other test files mock 'google' in sys.modules, so we temporarily restore
    # the real google.api_core imports for these tests.

    @staticmethod
    def _real_google_api_core():
        """Context manager that temporarily restores real google.api_core imports."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            saved = {}
            for key in list(sys.modules):
                if key == "google" or key.startswith("google."):
                    saved[key] = sys.modules.pop(key)
            try:
                yield
            finally:
                for key in list(sys.modules):
                    if key == "google" or key.startswith("google."):
                        del sys.modules[key]
                sys.modules.update(saved)

        return _ctx()

    def test_google_too_many_requests_is_retryable(self):
        with self._real_google_api_core():
            from google.api_core import exceptions as ge

            assert is_retryable_error(ge.TooManyRequests("429")) is True

    def test_google_internal_server_error_is_retryable(self):
        with self._real_google_api_core():
            from google.api_core import exceptions as ge

            assert is_retryable_error(ge.InternalServerError("500")) is True

    def test_google_bad_gateway_is_retryable(self):
        with self._real_google_api_core():
            from google.api_core import exceptions as ge

            assert is_retryable_error(ge.BadGateway("502")) is True

    def test_google_service_unavailable_is_retryable(self):
        with self._real_google_api_core():
            from google.api_core import exceptions as ge

            assert is_retryable_error(ge.ServiceUnavailable("503")) is True

    def test_google_gateway_timeout_is_retryable(self):
        with self._real_google_api_core():
            from google.api_core import exceptions as ge

            assert is_retryable_error(ge.GatewayTimeout("504")) is True

    def test_google_deadline_exceeded_is_retryable(self):
        with self._real_google_api_core():
            from google.api_core import exceptions as ge

            assert is_retryable_error(ge.DeadlineExceeded("timeout")) is True

    def test_google_resource_exhausted_is_retryable(self):
        with self._real_google_api_core():
            from google.api_core import exceptions as ge

            assert is_retryable_error(ge.ResourceExhausted("quota")) is True

    def test_google_cancelled_is_retryable(self):
        with self._real_google_api_core():
            from google.api_core import exceptions as ge

            assert is_retryable_error(ge.Cancelled("cancelled")) is True

    def test_google_exception_with_429_code_is_retryable(self):
        """Google API exception with .code == 429 but not an isinstance match (lines 124-127)."""
        with self._real_google_api_core():
            from google.api_core import exceptions as ge

            err = ge.NotFound("not found")
            err.code = 429
            assert is_retryable_error(err) is True

    def test_google_exception_with_500_code_is_retryable(self):
        """Google API exception with .code in 500-599 range (lines 128-129)."""
        with self._real_google_api_core():
            from google.api_core import exceptions as ge

            err = ge.NotFound("server error")
            err.code = 503
            assert is_retryable_error(err) is True

    # ── is_retryable_error: HTTPError with no status_code (line 141) ──

    def test_http_error_with_none_status_code_is_not_retryable(self):
        response = MagicMock()
        response.status_code = None
        err = requests.exceptions.HTTPError(response=response)
        assert is_retryable_error(err) is False

    # ── is_retryable_error: urllib HTTPError (lines 168-172) ──

    def test_urllib_http_error_429_is_retryable(self):
        from urllib.error import HTTPError

        err = HTTPError("http://example.com", 429, "Too Many Requests", {}, None)
        assert is_retryable_error(err) is True

    def test_urllib_http_error_500_is_retryable(self):
        from urllib.error import HTTPError

        err = HTTPError("http://example.com", 500, "Internal Server Error", {}, None)
        assert is_retryable_error(err) is True

    def test_urllib_http_error_503_is_retryable(self):
        from urllib.error import HTTPError

        err = HTTPError("http://example.com", 503, "Service Unavailable", {}, None)
        assert is_retryable_error(err) is True

    # ── is_retryable_error: string fallback patterns (lines 184) ──

    def test_error_string_499_cancelled_fallback(self):
        err = Exception("request failed with 499 cancelled by client")
        assert is_retryable_error(err) is True

    def test_error_string_500_fallback(self):
        err = Exception("server returned 500 internal error")
        assert is_retryable_error(err) is True

    def test_error_string_502_fallback(self):
        err = Exception("upstream returned 502 bad gateway")
        assert is_retryable_error(err) is True

    def test_error_string_503_fallback(self):
        err = Exception("service 503 unavailable")
        assert is_retryable_error(err) is True

    def test_error_string_504_fallback(self):
        err = Exception("gateway 504 timeout")
        assert is_retryable_error(err) is True

    def test_error_string_deadline_exceeded_fallback(self):
        err = Exception("deadline exceeded after 30s")
        assert is_retryable_error(err) is True

    def test_error_string_cancelled_fallback(self):
        err = Exception("operation cancelled by server")
        assert is_retryable_error(err) is True

    # ── RetryConfig partial kwargs (line 141) ──

    def test_retry_config_partial_kwargs(self):
        config = RetryConfig(max_attempts=7)
        assert config.max_attempts == 7
        assert config.base_delay_seconds == 1.0  # default
        assert config.max_delay_seconds == 30.0  # default
        assert config.jitter is True  # default

    # ── retry_with_backoff: Retry-After header on 429 (lines 237-243) ──

    @patch("retry_utils.time.sleep")
    def test_retry_with_backoff_uses_retry_after_header_on_429(self, mock_sleep):
        """When a 429 HTTPError has a Retry-After header, the delay uses that value."""
        response = MagicMock()
        response.status_code = 429
        response.headers = {"Retry-After": "5"}
        err_429 = requests.exceptions.HTTPError(response=response)

        fn = MagicMock(side_effect=[err_429, "ok"])
        result = retry_with_backoff(
            fn,
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=30.0, jitter=False),
        )
        assert result == "ok"
        # Delay should be 5.0 from Retry-After, not the computed exponential backoff of 1.0
        assert mock_sleep.call_args_list[0][0][0] == 5.0

    @patch("retry_utils.time.sleep")
    def test_retry_with_backoff_caps_retry_after_at_max_delay(self, mock_sleep):
        """Retry-After value is capped at max_delay_seconds."""
        response = MagicMock()
        response.status_code = 429
        response.headers = {"Retry-After": "120"}
        err_429 = requests.exceptions.HTTPError(response=response)

        fn = MagicMock(side_effect=[err_429, "ok"])
        result = retry_with_backoff(
            fn,
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=10.0, jitter=False),
        )
        assert result == "ok"
        # Retry-After is 120 but capped at max_delay 10.0
        assert mock_sleep.call_args_list[0][0][0] == 10.0

    @patch("retry_utils.time.sleep")
    def test_retry_with_backoff_skips_jitter_when_retry_after_present(self, mock_sleep):
        """When Retry-After is used, jitter is NOT applied (even if jitter=True)."""
        response = MagicMock()
        response.status_code = 429
        response.headers = {"Retry-After": "7"}
        err_429 = requests.exceptions.HTTPError(response=response)

        fn = MagicMock(side_effect=[err_429, "ok"])
        result = retry_with_backoff(
            fn,
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=30.0, jitter=True),
        )
        assert result == "ok"
        # Delay must be exactly 7.0 — no jitter applied
        assert mock_sleep.call_args_list[0][0][0] == 7.0

    # ── retry_with_backoff: jitter applied (lines 156-157) ──

    @patch("retry_utils.random.uniform", return_value=1.25)
    @patch("retry_utils.time.sleep")
    def test_retry_with_backoff_applies_jitter(self, mock_sleep, mock_uniform):
        """When jitter=True and no Retry-After, delay is multiplied by jitter factor."""
        fn = MagicMock(side_effect=[requests.exceptions.ConnectionError(), "ok"])
        result = retry_with_backoff(
            fn,
            RetryConfig(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=30.0, jitter=True),
        )
        assert result == "ok"
        # base delay = min(2.0 * 2^0, 30) = 2.0; with jitter factor 1.25 -> 2.5
        mock_uniform.assert_called_once_with(0.5, 1.5)
        assert mock_sleep.call_args_list[0][0][0] == 2.5

    # ── retry_with_backoff: EmptyLLMResponseError is retried (lines 256-258 via retryable path) ──

    @patch("retry_utils.time.sleep")
    def test_retry_with_backoff_retries_empty_llm_response_error(self, mock_sleep):
        fn = MagicMock(side_effect=[EmptyLLMResponseError("empty"), "result"])
        result = retry_with_backoff(fn, RetryConfig(max_attempts=3, jitter=False))
        assert result == "result"
        assert fn.call_count == 2

    @patch("retry_utils.time.sleep")
    def test_retry_with_backoff_exhausts_on_empty_llm_response_error(self, mock_sleep):
        fn = MagicMock(side_effect=EmptyLLMResponseError("empty"))
        with pytest.raises(EmptyLLMResponseError):
            retry_with_backoff(fn, RetryConfig(max_attempts=2, jitter=False))
        assert fn.call_count == 2

    # ── retry_with_backoff: kwargs forwarding (lines 237-243) ──
    # The fn passed to retry_with_backoff is a no-arg callable. kwargs forwarding
    # is tested by wrapping a lambda that captures the kwargs.

    @patch("retry_utils.time.sleep")
    def test_retry_with_backoff_429_without_retry_after_header(self, mock_sleep):
        """429 without Retry-After uses normal exponential backoff (retry_after_value stays None)."""
        response = MagicMock()
        response.status_code = 429
        response.headers = {}
        err_429 = requests.exceptions.HTTPError(response=response)

        fn = MagicMock(side_effect=[err_429, "ok"])
        result = retry_with_backoff(
            fn,
            RetryConfig(max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=30.0, jitter=False),
        )
        assert result == "ok"
        # Normal exponential: min(1.0 * 2^0, 30) = 1.0
        assert mock_sleep.call_args_list[0][0][0] == 1.0

    # ── retry_with_backoff: last_exception safety raise (lines 256-258) ──
    # Lines 256-258 are the fallback after the for-loop. Under normal execution the
    # loop either returns or raises inside the loop body. To hit these lines we
    # would need max_attempts=0, which makes the for-loop body never execute.

    def test_retry_with_backoff_zero_attempts_raises_runtime_error(self):
        """With max_attempts=0 the for-loop never runs, hitting the fallback raise."""
        fn = MagicMock(return_value="never called")
        with pytest.raises(RuntimeError, match="Unexpected retry failure"):
            retry_with_backoff(fn, RetryConfig(max_attempts=0), operation_name="test-op")
        assert fn.call_count == 0
