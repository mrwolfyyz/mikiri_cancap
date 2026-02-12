"""Tests for gcp/shared/retry_utils.py"""

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
