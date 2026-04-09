"""Tests for the chat_handler Cloud Function (gcp/functions/chat_handler/main.py).

The base orchestration (CORS, validation, Gemini call, retries) is tested
in test_chat_handler_base.py.  These tests cover the skip-trace-specific
build_prompt function, config values, and delegation to handle_chat_request.
"""

import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("CORS_ALLOWED_ORIGINS", "*")

# ---------------------------------------------------------------------------
# Mock heavy dependencies before loading the module
# ---------------------------------------------------------------------------
_mock_ff = MagicMock()
_mock_ff.http = lambda f: f
sys.modules.setdefault("functions_framework", _mock_ff)

# google.genai (used by chat_handler_base)
_mock_google = MagicMock()
_mock_genai = MagicMock()
_mock_genai_types = MagicMock()
sys.modules.setdefault("google", _mock_google)
sys.modules.setdefault("google.genai", _mock_genai)
sys.modules.setdefault("google.genai.types", _mock_genai_types)

# ---------------------------------------------------------------------------
# Load chat_handler/main.py
# ---------------------------------------------------------------------------
from conftest import load_function_module

ch_main = load_function_module("chat_handler", "chat_handler_main")
build_prompt = ch_main.build_prompt
main_handler = ch_main.main
SYSTEM_PROMPT = ch_main.SYSTEM_PROMPT
_config = ch_main._config


# ===========================================================================
# Config
# ===========================================================================
class TestConfig:
    """Verify handler configuration."""

    def test_temperature(self):
        assert _config.temperature == 0.7

    def test_log_prefix(self):
        assert _config.log_prefix == "[ChatHandler]"

    def test_build_prompt_fn(self):
        assert _config.build_prompt_fn is build_prompt


# ===========================================================================
# build_prompt
# ===========================================================================
class TestBuildPrompt:
    """Tests for skip-trace prompt construction."""

    def test_first_message_with_identity_context(self):
        prompt = build_prompt(
            message="Where does John live?",
            conversation_history=[],
            markdown_context={"identity": "# Identity Report\nJohn Smith, Toronto"},
        )
        assert SYSTEM_PROMPT in prompt
        assert "Identity Report" in prompt
        assert "John Smith, Toronto" in prompt
        assert "Where does John live?" in prompt

    def test_first_message_no_context(self):
        prompt = build_prompt(
            message="Hello",
            conversation_history=[],
            markdown_context=None,
        )
        assert SYSTEM_PROMPT in prompt
        assert "Hello" in prompt
        assert "Investigation Reports" not in prompt

    def test_subsequent_message_with_history(self):
        """On follow-up messages, history is included but markdown context is not."""
        history = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
        ]
        prompt = build_prompt(
            message="Follow-up question",
            conversation_history=history,
            markdown_context={"identity": "should not appear"},
        )
        assert SYSTEM_PROMPT in prompt
        assert "Previous Conversation" in prompt
        # markdown_context is skipped when history is present
        assert "Investigation Reports" not in prompt
        assert "Follow-up question" in prompt

    def test_empty_markdown_context_skipped(self):
        prompt = build_prompt(
            message="Hi",
            conversation_history=[],
            markdown_context={},
        )
        assert "Investigation Reports" not in prompt

    def test_markdown_context_missing_identity_key(self):
        """If markdown_context has no 'identity' key, the section is skipped."""
        prompt = build_prompt(
            message="Hi",
            conversation_history=[],
            markdown_context={"other_key": "data"},
        )
        assert "Identity Report" not in prompt


# ===========================================================================
# main handler delegation
# ===========================================================================
class TestMainHandler:
    """Verify that main() delegates to handle_chat_request."""

    def test_delegates_to_handle_chat_request(self):
        mock_request = MagicMock()
        with patch.object(ch_main, "handle_chat_request", return_value=("ok", 200, {})) as mock_handle:
            result = main_handler(mock_request)

        mock_handle.assert_called_once_with(mock_request, _config)
        assert result == ("ok", 200, {})
