"""Tests for chat_handler_base.py.

The module depends on google.genai (Google Gen AI SDK) and flask.
We mock google.genai in sys.modules before import. Flask must be
installed (add to requirements-test.txt).

Covers:
- format_conversation_history (pure function)
- extract_grounding_metadata (pure function over mock objects)
- _truncate_history_if_needed (pure function)
- handle_chat_request (integration tests with mocked Gemini client)
"""

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock Google Gen AI SDK BEFORE importing chat_handler_base.
# The module does:
#   from google import genai
#   from google.genai.types import GenerateContentConfig, Tool, GoogleSearch
# ---------------------------------------------------------------------------
_mock_genai = MagicMock()
_mock_genai_types = MagicMock()

# google may already exist as a namespace package (from google-cloud-*).
# Preserve it if present, but ensure google.genai is mocked.
if "google" not in sys.modules:
    _mock_google = MagicMock()
    sys.modules["google"] = _mock_google
else:
    _mock_google = sys.modules["google"]
_mock_google.genai = _mock_genai

sys.modules["google.genai"] = _mock_genai
sys.modules["google.genai.types"] = _mock_genai_types

import os

os.environ.setdefault("CORS_ALLOWED_ORIGINS", "*")

# Now safe to import
import chat_handler_base
from chat_handler_base import (
    CHAT_RESPONSE_FALLBACK_TEXT,
    MAX_PROMPT_CHARS,
    ChatHandlerConfig,
    _truncate_history_if_needed,
    extract_grounding_metadata,
    format_conversation_history,
    handle_chat_request,
    parse_chat_json_response,
)


def _llm_json(text: str) -> str:
    """Gemini JSON-mode style body: one object with string response."""
    return json.dumps({"response": text})


# ===========================================================================
# Tests for parse_chat_json_response
# ===========================================================================
class TestParseChatJsonResponse:
    """Pure tests for JSON envelope parsing."""

    def test_valid_object(self):
        assert parse_chat_json_response('{"response": "hello"}') == "hello"

    def test_code_fenced(self):
        raw = '```json\n{"response": "x"}\n```'
        assert parse_chat_json_response(raw) == "x"

    def test_wrong_response_type(self):
        assert parse_chat_json_response('{"response": 1}') is None

    def test_missing_key(self):
        assert parse_chat_json_response("{}") is None

    def test_not_json(self):
        assert parse_chat_json_response("not json") is None

    def test_empty_response_string(self):
        assert parse_chat_json_response('{"response": "   "}') is None


# ===========================================================================
# Tests for format_conversation_history
# ===========================================================================
class TestFormatConversationHistory:
    """Tests for the format_conversation_history function (pure)."""

    def test_empty_history_returns_empty_string(self):
        assert format_conversation_history([]) == ""

    def test_single_user_message(self):
        history = [{"role": "user", "content": "Hello"}]
        assert format_conversation_history(history) == "User: Hello"

    def test_single_assistant_message(self):
        history = [{"role": "assistant", "content": "Hi there"}]
        assert format_conversation_history(history) == "Assistant: Hi there"

    def test_user_and_assistant_turn(self):
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        result = format_conversation_history(history)
        assert result == "User: Hello\nAssistant: Hi there"

    def test_multiple_turns(self):
        history = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        lines = format_conversation_history(history).split("\n")
        assert len(lines) == 4
        assert lines[0] == "User: Q1"
        assert lines[3] == "Assistant: A2"

    def test_missing_role_defaults_to_user(self):
        history = [{"content": "Hello"}]
        result = format_conversation_history(history)
        assert result == "User: Hello"

    def test_missing_content_defaults_to_empty(self):
        history = [{"role": "user"}]
        result = format_conversation_history(history)
        assert result == "User: "

    def test_unknown_role_skipped(self):
        """Messages with non-user/assistant roles produce no output."""
        history = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
        ]
        result = format_conversation_history(history)
        # "system" role doesn't match user or assistant, so it's skipped
        assert "System" not in result
        assert "User: Hello" in result


# ===========================================================================
# Tests for extract_grounding_metadata
# ===========================================================================
class TestExtractGroundingMetadata:
    """Tests for the extract_grounding_metadata function."""

    def test_default_empty_metadata(self):
        """Response with no attributes returns empty defaults."""
        response = MagicMock(spec=[])  # no attributes at all
        result = extract_grounding_metadata(response)
        assert result["search_entry_point"] == ""
        assert result["web_search_queries"] == []
        assert result["grounding_chunks"] == []

    def test_response_with_empty_candidates(self):
        response = MagicMock()
        response.candidates = []
        result = extract_grounding_metadata(response)
        assert result["web_search_queries"] == []
        assert result["grounding_chunks"] == []

    def test_extracts_web_search_queries(self):
        response = MagicMock()
        candidate = MagicMock()
        candidate.grounding_metadata.web_search_queries = ["John Smith Toronto", "John Smith LinkedIn"]
        candidate.grounding_metadata.grounding_chunks = []
        # Remove search_entry_point so hasattr returns False
        del candidate.grounding_metadata.search_entry_point
        response.candidates = [candidate]

        result = extract_grounding_metadata(response)
        assert result["web_search_queries"] == ["John Smith Toronto", "John Smith LinkedIn"]

    def test_extracts_grounding_chunks_with_web_data(self):
        response = MagicMock()
        candidate = MagicMock()

        chunk1 = MagicMock()
        chunk1.web.uri = "https://linkedin.com/in/jsmith"
        chunk1.web.title = "John Smith - LinkedIn"

        chunk2 = MagicMock()
        chunk2.web.uri = "https://company.com/team"
        chunk2.web.title = "Our Team"

        candidate.grounding_metadata.web_search_queries = []
        candidate.grounding_metadata.grounding_chunks = [chunk1, chunk2]
        del candidate.grounding_metadata.search_entry_point
        response.candidates = [candidate]

        result = extract_grounding_metadata(response)
        assert len(result["grounding_chunks"]) == 2
        assert result["grounding_chunks"][0]["web"]["uri"] == "https://linkedin.com/in/jsmith"
        assert result["grounding_chunks"][1]["web"]["title"] == "Our Team"

    def test_extracts_search_entry_point_rendered_content(self):
        response = MagicMock()
        candidate = MagicMock()
        candidate.grounding_metadata.web_search_queries = []
        candidate.grounding_metadata.grounding_chunks = []
        candidate.grounding_metadata.search_entry_point.rendered_content = "<div>Search Widget</div>"
        response.candidates = [candidate]

        result = extract_grounding_metadata(response)
        assert result["search_entry_point"] == "<div>Search Widget</div>"

    def test_extracts_search_entry_point_html_fallback(self):
        """Falls back to .html if .rendered_content doesn't exist."""
        response = MagicMock()
        candidate = MagicMock()
        candidate.grounding_metadata.web_search_queries = []
        candidate.grounding_metadata.grounding_chunks = []
        entry_point = MagicMock(spec=["html"])
        entry_point.html = "<div>Fallback</div>"
        candidate.grounding_metadata.search_entry_point = entry_point
        response.candidates = [candidate]

        result = extract_grounding_metadata(response)
        assert result["search_entry_point"] == "<div>Fallback</div>"

    def test_handles_exception_gracefully(self):
        """Exceptions during extraction don't propagate; returns defaults."""
        response = MagicMock()
        # Make candidates access raise
        type(response).candidates = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

        result = extract_grounding_metadata(response)
        assert result["search_entry_point"] == ""
        assert result["web_search_queries"] == []
        assert result["grounding_chunks"] == []

    def test_logs_usage_metadata(self, capsys):
        """Usage metadata is printed when present."""
        response = MagicMock()
        response.candidates = []
        response.usage_metadata = "prompt_tokens=100, candidates_tokens=50"

        extract_grounding_metadata(response, log_prefix="[TestChat]")
        captured = capsys.readouterr()
        assert "Usage metadata" in captured.out


# ===========================================================================
# Tests for _truncate_history_if_needed
# ===========================================================================
class TestTruncateHistoryIfNeeded:
    """Tests for the _truncate_history_if_needed function."""

    def test_short_prompt_unchanged(self):
        """Prompts within limit are returned unchanged."""
        short_prompt = "A short prompt"
        result = _truncate_history_if_needed(
            prompt=short_prompt,
            message="hello",
            conversation_history=[],
            markdown_context=None,
            build_prompt_fn=lambda m, h, c: "rebuilt",
            log_prefix="[Test]",
        )
        assert result == short_prompt

    def test_long_prompt_triggers_truncation(self):
        """Prompts over MAX_PROMPT_CHARS trigger progressive history truncation."""
        long_content = "x" * (MAX_PROMPT_CHARS + 1000)
        history = [
            {"role": "user", "content": "old question 1"},
            {"role": "assistant", "content": "old answer 1"},
            {"role": "user", "content": "old question 2"},
            {"role": "assistant", "content": "old answer 2"},
            {"role": "user", "content": "recent question"},
            {"role": "assistant", "content": "recent answer"},
        ]

        def mock_build_prompt(message, hist, ctx):
            # Simulate: prompt gets shorter as history is dropped
            if len(hist) <= 2:
                return "short enough now"
            return long_content

        result = _truncate_history_if_needed(
            prompt=long_content,
            message="new msg",
            conversation_history=history,
            markdown_context=None,
            build_prompt_fn=mock_build_prompt,
            log_prefix="[Test]",
        )
        assert len(result) <= MAX_PROMPT_CHARS

    def test_empty_history_cannot_truncate_further(self):
        """If history is already empty, returns whatever build_prompt_fn gives."""
        long_prompt = "x" * (MAX_PROMPT_CHARS + 100)
        result = _truncate_history_if_needed(
            prompt=long_prompt,
            message="hello",
            conversation_history=[],
            markdown_context=None,
            build_prompt_fn=lambda m, h, c: long_prompt,
            log_prefix="[Test]",
        )
        # Can't truncate further — returns the long prompt as-is
        assert result == long_prompt

    def test_drops_user_assistant_pairs(self, capsys):
        """Truncation drops the oldest user+assistant pair together."""
        over_limit = "x" * (MAX_PROMPT_CHARS + 100)
        history = [
            {"role": "user", "content": "old Q"},
            {"role": "assistant", "content": "old A"},
        ]

        call_args = []

        def mock_build_prompt(message, hist, ctx):
            call_args.append(list(hist))
            if len(hist) == 0:
                return "short"
            return over_limit

        _truncate_history_if_needed(
            prompt=over_limit,
            message="msg",
            conversation_history=history,
            markdown_context=None,
            build_prompt_fn=mock_build_prompt,
            log_prefix="[Test]",
        )
        # build_prompt_fn was called with empty history (both entries dropped)
        assert call_args[-1] == []
        captured = capsys.readouterr()
        assert "Truncated 2 oldest history entries" in captured.out


# ===========================================================================
# Tests for handle_chat_request
# ===========================================================================
class TestHandleChatRequest:
    """Tests for the main handle_chat_request orchestrator."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Reset client singleton and mock jsonify for each test."""
        chat_handler_base._client = None
        with patch.object(chat_handler_base, "jsonify", side_effect=lambda x: x):
            yield

    def _make_config(self):
        """Create a minimal ChatHandlerConfig for testing."""
        return ChatHandlerConfig(
            build_prompt_fn=lambda msg, hist, ctx: f"Prompt: {msg}",
            temperature=0.7,
            log_prefix="[TestChat]",
        )

    def _make_request(self, method="POST", json_data=None, json_error=False):
        """Create a mock Flask request."""
        request = MagicMock()
        request.method = method
        if json_error:
            request.get_json.side_effect = Exception("bad json")
        else:
            request.get_json.return_value = json_data
        return request

    # ----- CORS -----

    def test_cors_preflight_returns_204(self):
        request = self._make_request(method="OPTIONS")
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 204
        assert "Access-Control-Allow-Origin" in headers
        assert "Access-Control-Allow-Methods" in headers
        assert headers["Access-Control-Max-Age"] == "3600"

    def test_method_not_allowed(self):
        request = self._make_request(method="GET")
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 405

    def test_all_responses_include_cors_headers(self):
        """Non-preflight responses include Access-Control-Allow-Origin."""
        request = self._make_request(json_data={"message": ""})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert "Access-Control-Allow-Origin" in headers

    # ----- Request validation -----

    def test_invalid_json_returns_400(self):
        request = self._make_request(json_error=True)
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 400
        assert "Invalid JSON" in body["error"]

    def test_missing_message_returns_400(self):
        request = self._make_request(json_data={})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 400
        assert "message" in body["error"].lower()

    def test_empty_message_returns_400(self):
        request = self._make_request(json_data={"message": ""})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 400

    def test_whitespace_only_message_returns_400(self):
        request = self._make_request(json_data={"message": "   "})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 400

    def test_message_too_long_returns_400(self):
        request = self._make_request(json_data={"message": "x" * 10_001})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 400
        assert "too long" in body["error"].lower()

    def test_message_at_limit_accepted(self):
        """Message exactly at 10,000 chars should pass validation."""
        request = self._make_request(json_data={"message": "x" * 10_000})
        with (
            patch.object(chat_handler_base, "GCP_PROJECT", "test-project"),
            patch.object(chat_handler_base, "_get_client") as mock_client,
        ):
            mock_resp = MagicMock()
            mock_resp.text = _llm_json("ok")
            mock_resp.candidates = []
            mock_client.return_value.models.generate_content.return_value = mock_resp
            body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 200

    def test_conversation_history_not_a_list_returns_400(self):
        request = self._make_request(json_data={"message": "hi", "conversation_history": "not a list"})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 400
        assert "list" in body["error"].lower()

    def test_conversation_history_too_long_returns_400(self):
        long_history = [{"role": "user", "content": f"msg {i}"} for i in range(51)]
        request = self._make_request(json_data={"message": "hi", "conversation_history": long_history})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 400
        assert "too long" in body["error"].lower()

    def test_conversation_history_at_limit_accepted(self):
        """History with exactly 50 entries should pass validation."""
        history = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
        request = self._make_request(json_data={"message": "hi", "conversation_history": history})
        with (
            patch.object(chat_handler_base, "GCP_PROJECT", "test-project"),
            patch.object(chat_handler_base, "_get_client") as mock_client,
        ):
            mock_resp = MagicMock()
            mock_resp.text = _llm_json("ok")
            mock_resp.candidates = []
            mock_client.return_value.models.generate_content.return_value = mock_resp
            body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 200

    def test_invalid_history_entry_missing_role(self):
        request = self._make_request(
            json_data={
                "message": "hi",
                "conversation_history": [{"content": "no role key"}],
            }
        )
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 400
        assert "format" in body["error"].lower()

    def test_invalid_history_entry_missing_content(self):
        request = self._make_request(
            json_data={
                "message": "hi",
                "conversation_history": [{"role": "user"}],
            }
        )
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 400

    def test_invalid_history_entry_not_dict(self):
        request = self._make_request(
            json_data={
                "message": "hi",
                "conversation_history": ["not a dict"],
            }
        )
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 400

    # ----- GCP configuration -----

    def test_no_gcp_project_returns_500(self):
        request = self._make_request(json_data={"message": "hi"})
        with patch.object(chat_handler_base, "GCP_PROJECT", ""):
            body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 500
        assert "GCP_PROJECT" in body["error"]

    # ----- Client initialization -----

    def test_client_init_failure_returns_500(self):
        request = self._make_request(json_data={"message": "hi"})
        with (
            patch.object(chat_handler_base, "GCP_PROJECT", "test-project"),
            patch.object(chat_handler_base, "_get_client", side_effect=Exception("Auth failed")),
        ):
            body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 500
        assert "initialization" in body["error"].lower()

    # ----- Prompt building -----

    def test_build_prompt_failure_returns_500(self):
        def _failing_prompt(msg, hist, ctx):
            raise ValueError("Bad prompt template")

        config = ChatHandlerConfig(
            build_prompt_fn=_failing_prompt,
            temperature=0.7,
            log_prefix="[Test]",
        )
        request = self._make_request(json_data={"message": "hi"})
        with (
            patch.object(chat_handler_base, "GCP_PROJECT", "test-project"),
            patch.object(chat_handler_base, "_get_client") as mock_get,
        ):
            mock_get.return_value = MagicMock()
            body, status, headers = handle_chat_request(request, config)
        assert status == 500
        assert "prompt" in body["error"].lower()

    # ----- Successful flow -----

    @patch.object(chat_handler_base, "_get_client")
    @patch.object(chat_handler_base, "GCP_PROJECT", "test-project")
    def test_successful_response(self, mock_get_client):
        """Full successful flow with mocked Gemini client."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_response = MagicMock()
        mock_response.text = _llm_json("Here's what I found about John Smith.")
        mock_response.candidates = []
        mock_client.models.generate_content.return_value = mock_response

        request = self._make_request(
            json_data={
                "message": "Tell me about this person",
                "conversation_history": [],
            }
        )

        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 200
        assert body["response"] == "Here's what I found about John Smith."
        mock_client.models.generate_content.assert_called_once()

    @patch.object(chat_handler_base, "_get_client")
    @patch.object(chat_handler_base, "GCP_PROJECT", "test-project")
    def test_response_includes_updated_history(self, mock_get_client):
        """Response includes the original history plus the new user/assistant turns."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_response = MagicMock()
        mock_response.text = _llm_json("The answer is 42.")
        mock_response.candidates = []
        mock_client.models.generate_content.return_value = mock_response

        existing_history = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ]
        request = self._make_request(
            json_data={
                "message": "new question",
                "conversation_history": existing_history,
            }
        )

        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 200
        hist = body["conversation_history"]
        assert len(hist) == 4
        assert hist[2] == {"role": "user", "content": "new question"}
        assert hist[3] == {"role": "assistant", "content": "The answer is 42."}

    @patch.object(chat_handler_base, "_get_client")
    @patch.object(chat_handler_base, "GCP_PROJECT", "test-project")
    def test_response_includes_grounding_metadata(self, mock_get_client):
        """Response includes grounding metadata from the Gemini response."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_response = MagicMock()
        mock_response.text = _llm_json("Found info.")
        mock_response.candidates = []
        mock_client.models.generate_content.return_value = mock_response

        request = self._make_request(json_data={"message": "search for John"})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 200
        assert "grounding_metadata" in body

    @patch.object(chat_handler_base, "_get_client")
    @patch.object(chat_handler_base, "GCP_PROJECT", "test-project")
    def test_original_history_not_mutated(self, mock_get_client):
        """The original conversation_history list is not modified."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_response = MagicMock()
        mock_response.text = _llm_json("response")
        mock_response.candidates = []
        mock_client.models.generate_content.return_value = mock_response

        original_history = [{"role": "user", "content": "q1"}]
        request = self._make_request(
            json_data={
                "message": "q2",
                "conversation_history": original_history,
            }
        )

        handle_chat_request(request, self._make_config())
        # Original list should be unchanged (the function uses .copy())
        assert len(original_history) == 1

    # ----- LLM error paths -----

    @patch("retry_utils.time.sleep")
    @patch.object(chat_handler_base, "_get_client")
    @patch.object(chat_handler_base, "GCP_PROJECT", "test-project")
    def test_empty_llm_response_returns_500(self, mock_get_client, mock_sleep):
        """Empty LLM responses after retries return 500 with helpful message."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Response with no text triggers EmptyLLMResponseError inside _call_gemini
        mock_response = MagicMock()
        mock_response.text = None
        mock_client.models.generate_content.return_value = mock_response

        request = self._make_request(json_data={"message": "hi"})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 500
        assert "response" in body  # includes a user-facing fallback message

    @patch.object(chat_handler_base, "_get_client")
    @patch.object(chat_handler_base, "GCP_PROJECT", "test-project")
    def test_gemini_api_exception_returns_500(self, mock_get_client):
        """Unexpected Gemini API exceptions return 500."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.models.generate_content.side_effect = RuntimeError("API down")

        request = self._make_request(json_data={"message": "hi"})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 500
        assert "response" in body  # includes a user-facing fallback message

    # ----- Markdown context -----

    @patch.object(chat_handler_base, "_get_client")
    @patch.object(chat_handler_base, "GCP_PROJECT", "test-project")
    def test_markdown_context_passed_to_build_prompt(self, mock_get_client):
        """markdown_context from request is forwarded to build_prompt_fn."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.text = _llm_json("response")
        mock_response.candidates = []
        mock_client.models.generate_content.return_value = mock_response

        received_ctx = []

        def capture_prompt(msg, hist, ctx):
            received_ctx.append(ctx)
            return f"Prompt: {msg}"

        config = ChatHandlerConfig(
            build_prompt_fn=capture_prompt,
            temperature=0.7,
            log_prefix="[Test]",
        )

        md_context = {"report": "# Skip Trace Report\n..."}
        request = self._make_request(
            json_data={
                "message": "summarize the report",
                "markdown_context": md_context,
            }
        )

        handle_chat_request(request, config)
        assert received_ctx[0] == md_context

    def test_markdown_context_not_object_returns_400(self):
        request = self._make_request(
            json_data={"message": "hi", "markdown_context": "not a dict"},
        )
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 400
        assert "object" in body["error"].lower()

    @patch.object(chat_handler_base, "_get_client")
    @patch.object(chat_handler_base, "GCP_PROJECT", "test-project")
    def test_json_schema_mismatch_silent_retry_then_success(self, mock_get_client):
        """Invalid JSON shape on first model response triggers one silent retry."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        bad = MagicMock()
        bad.text = '{"foo": "bar"}'
        bad.candidates = []
        good = MagicMock()
        good.text = _llm_json("Recovered")
        good.candidates = []
        mock_client.models.generate_content.side_effect = [bad, good]
        request = self._make_request(json_data={"message": "hi"})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 200
        assert body["response"] == "Recovered"
        assert mock_client.models.generate_content.call_count == 2

    @patch.object(chat_handler_base, "_get_client")
    @patch.object(chat_handler_base, "GCP_PROJECT", "test-project")
    def test_json_schema_mismatch_twice_uses_fallback(self, mock_get_client):
        """Two invalid model JSON payloads yield friendly fallback text (HTTP 200)."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        bad = MagicMock()
        bad.text = "{}"
        bad.candidates = []
        bad2 = MagicMock()
        bad2.text = '{"response": 123}'
        bad2.candidates = []
        mock_client.models.generate_content.side_effect = [bad, bad2]
        request = self._make_request(json_data={"message": "hi"})
        body, status, headers = handle_chat_request(request, self._make_config())
        assert status == 200
        assert body["response"] == CHAT_RESPONSE_FALLBACK_TEXT
        assert mock_client.models.generate_content.call_count == 2
