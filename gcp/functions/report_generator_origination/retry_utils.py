"""
Retry utility with exponential backoff for external API calls.

Provides retry logic for transient failures with configurable exponential backoff,
jitter, and error classification.
"""

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar, Any
import requests

T = TypeVar('T')


class EmptyLLMResponseError(Exception):
    """Exception raised when LLM returns an empty response.
    
    This is considered a retryable error as empty responses from LLM services
    are often transient service issues rather than permanent failures.
    """
    pass


class RateLimitExhaustedError(Exception):
    """Exception raised when all retry attempts are exhausted due to rate limiting (429).
    
    This is NOT retried internally (retries are already exhausted).
    Used to signal to the caller (e.g. Cloud Workflow) that a 429 response
    should be returned so the workflow can retry at a higher level.
    """
    pass


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    jitter: bool = True  # adds randomness to prevent thundering herd


def extract_retry_after(exception: Exception) -> float | None:
    """
    Extract Retry-After header value from HTTPError exception.
    
    Returns:
        Retry-After value in seconds, or None if not present/invalid
    """
    if isinstance(exception, requests.exceptions.HTTPError):
        response = getattr(exception, 'response', None)
        if response is not None:
            retry_after = response.headers.get('Retry-After')
            if retry_after:
                try:
                    # Retry-After can be either seconds (int) or HTTP date
                    # Try parsing as integer first
                    return float(retry_after)
                except (ValueError, TypeError):
                    # If not a number, try parsing as HTTP date
                    try:
                        from email.utils import parsedate_to_datetime
                        retry_date = parsedate_to_datetime(retry_after)
                        if retry_date:
                            import time
                            delay = (retry_date.timestamp() - time.time())
                            return max(0, delay)  # Ensure non-negative
                    except Exception:
                        pass
    return None


def is_retryable_error(exception: Exception) -> bool:
    """
    Determine if an exception is retryable.
    
    Retries on:
        - ConnectionError (network issues)
        - Timeout errors
        - HTTP 429 (rate limit)
        - HTTP 500-599 (server errors)
        - EmptyLLMResponseError (empty LLM responses - often transient)
        - Google API Core exceptions (429, 500-599, and other retryable errors)
    
    Does NOT retry on:
        - HTTP 400-499 (client errors, except 429)
        - Validation errors
        - Other non-retryable exceptions
    """
    # Empty LLM responses (often transient service issues)
    if isinstance(exception, EmptyLLMResponseError):
        return True
    
    # Google API Core exceptions (used by Vertex AI)
    try:
        from google.api_core import exceptions as google_exceptions
        # Check for retryable Google API exceptions
        if isinstance(exception, (
            google_exceptions.TooManyRequests,  # 429
            google_exceptions.InternalServerError,  # 500
            google_exceptions.BadGateway,  # 502
            google_exceptions.ServiceUnavailable,  # 503
            google_exceptions.GatewayTimeout,  # 504
            google_exceptions.DeadlineExceeded,  # Timeout
            google_exceptions.ResourceExhausted,  # 429 (alternative)
        )):
            return True
        
        # Check for other Google API exceptions with retryable status codes
        if hasattr(exception, 'code'):
            code = exception.code
            if code == 429:  # Too Many Requests
                return True
            if 500 <= code <= 599:  # Server errors
                return True
    except ImportError:
        pass  # google.api_core not available, skip
    
    # Network/connection errors
    if isinstance(exception, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return True
    
    # HTTP errors
    if isinstance(exception, requests.exceptions.HTTPError):
        status_code = getattr(exception.response, 'status_code', None)
        if status_code is None:
            return False
        
        # Retry on rate limits and server errors
        if status_code == 429:
            return True
        if 500 <= status_code <= 599:
            return True
        
        # Don't retry on client errors (except 429)
        if 400 <= status_code <= 499:
            return False
    
    # Request exceptions that might be transient
    if isinstance(exception, requests.exceptions.RequestException):
        # Check if it's a connection/timeout issue
        if isinstance(exception, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True
    
    # For urllib exceptions (used by geocoding), treat as retryable
    from urllib.error import URLError, HTTPError
    if isinstance(exception, (URLError, HTTPError)):
        # Check if it's a timeout or connection error
        if hasattr(exception, 'reason'):
            # Connection errors are retryable
            return True
        # HTTP errors: retry on 5xx, 429
        if isinstance(exception, HTTPError):
            if exception.code == 429:
                return True
            if 500 <= exception.code <= 599:
                return True
    
    # Fallback: check exception message/string for retryable indicators
    # This handles cases where exception type isn't recognized but error is retryable
    error_str = str(exception).lower()
    if "429" in error_str or "too many requests" in error_str or "resource exhausted" in error_str:
        return True
    if "500" in error_str or "503" in error_str or "502" in error_str or "504" in error_str:
        return True
    if "deadline exceeded" in error_str or "timeout" in error_str:
        return True
    
    # Default: don't retry unknown exceptions
    return False


def retry_with_backoff(
    fn: Callable[[], T],
    config: RetryConfig = RetryConfig(),
    operation_name: str = "operation"
) -> T:
    """
    Retry wrapper for external API calls with exponential backoff.
    
    Delay calculation:
        delay = min(base_delay * (2 ** attempt), max_delay)
        if jitter: delay = delay * random(0.5, 1.5)
    
    Args:
        fn: Function to retry (must be callable with no arguments)
        config: Retry configuration
        operation_name: Name of operation for logging
    
    Returns:
        Result of calling fn()
    
    Raises:
        Last exception if all retries are exhausted
    """
    last_exception = None
    
    for attempt in range(config.max_attempts):
        try:
            return fn()
        except Exception as e:
            last_exception = e
            
            # Check if error is retryable
            if not is_retryable_error(e):
                print(f"[Retry] {operation_name}: Non-retryable error: {type(e).__name__}: {e}")
                raise
            
            # If this was the last attempt, re-raise
            if attempt == config.max_attempts - 1:
                print(f"[Retry] {operation_name}: All {config.max_attempts} attempts exhausted. Last error: {type(e).__name__}: {e}")
                raise
            
            # Calculate delay with exponential backoff
            delay = min(
                config.base_delay_seconds * (2 ** attempt),
                config.max_delay_seconds
            )
            
            # Check for Retry-After header on 429 errors
            retry_after_value = None
            if isinstance(e, requests.exceptions.HTTPError):
                status_code = getattr(e.response, 'status_code', None)
                if status_code == 429:
                    retry_after_value = extract_retry_after(e)
                    if retry_after_value is not None:
                        # Use Retry-After value, but cap at max_delay
                        delay = min(retry_after_value, config.max_delay_seconds)
                        print(f"[Retry] {operation_name}: Using Retry-After header: {delay:.2f}s")
            
            # Add jitter if enabled (but not if using Retry-After)
            if config.jitter and retry_after_value is None:
                jitter_factor = random.uniform(0.5, 1.5)
                delay = delay * jitter_factor
            
            print(f"[Retry] {operation_name}: Attempt {attempt + 1}/{config.max_attempts} failed ({type(e).__name__}: {e}). Retrying in {delay:.2f}s...")
            time.sleep(delay)
    
    # Should never reach here, but just in case
    if last_exception:
        raise last_exception
    raise RuntimeError(f"{operation_name}: Unexpected retry failure")
