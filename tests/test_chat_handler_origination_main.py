"""Tests for the chat_handler_origination Cloud Function.

Same pattern as test_chat_handler_main.py but for the origination variant.
Tests the origination-specific build_prompt (5 markdown sections), config
values, and delegation to handle_chat_request.
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

_mock_google = MagicMock()
_mock_genai = MagicMock()
_mock_genai_types = MagicMock()
sys.modules.setdefault("google", _mock_google)
sys.modules.setdefault("google.genai", _mock_genai)
sys.modules.setdefault("google.genai.types", _mock_genai_types)

# ---------------------------------------------------------------------------
# Load chat_handler_origination/main.py
# ---------------------------------------------------------------------------
from conftest import load_function_module

cho_main = load_function_module("chat_handler_origination", "chat_handler_origination_main")
build_prompt = cho_main.build_prompt
main_handler = cho_main.main
SYSTEM_PROMPT = cho_main.SYSTEM_PROMPT
_config = cho_main._config


# ===========================================================================
# Config
# ===========================================================================
class TestConfig:
    """Verify handler configuration."""

    def test_temperature(self):
        assert _config.temperature == 0.6

    def test_log_prefix(self):
        assert _config.log_prefix == "[ChatHandlerOrigination]"

    def test_build_prompt_fn(self):
        assert _config.build_prompt_fn is build_prompt


# ===========================================================================
# build_prompt
# ===========================================================================
class TestBuildPrompt:
    """Tests for origination-specific prompt construction."""

    def test_first_message_with_all_five_sections(self):
        context = {
            "summary": "Executive summary here",
            "identity": "Identity details here",
            "corporate": "Corporate info here",
            "litigation": "Litigation info here",
            "regulator": "Regulator info here",
        }
        prompt = build_prompt(
            message="Analyze this borrower",
            conversation_history=[],
            markdown_context=context,
        )
        assert SYSTEM_PROMPT in prompt
        assert "Borrower Summary" in prompt
        assert "Executive summary here" in prompt
        assert "Identity Report" in prompt
        assert "Corporate Report" in prompt
        assert "Adverse Media Report" in prompt
        assert "Regulator Report" in prompt
        assert "Analyze this borrower" in prompt

    def test_partial_markdown_context(self):
        """Only provided sections get their own ### header in the prompt."""
        context = {
            "summary": "Summary only",
            "identity": "Identity only",
        }
        prompt = build_prompt(
            message="Question",
            conversation_history=[],
            markdown_context=context,
        )
        assert "### Borrower Summary" in prompt
        assert "### Identity Report" in prompt
        # These section headers should NOT appear (data not provided)
        assert "### Corporate Report" not in prompt
        assert "### Adverse Media Report" not in prompt
        assert "### Regulator Report" not in prompt

    def test_no_context(self):
        prompt = build_prompt(
            message="Hello",
            conversation_history=[],
            markdown_context=None,
        )
        assert SYSTEM_PROMPT in prompt
        assert "Investigation Reports" not in prompt

    def test_with_conversation_history(self):
        """History is included; markdown context is skipped on follow-ups."""
        history = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
        ]
        prompt = build_prompt(
            message="Follow-up",
            conversation_history=history,
            markdown_context={"summary": "should not appear"},
        )
        assert "Previous Conversation" in prompt
        assert "Investigation Reports" not in prompt
        assert "Follow-up" in prompt

    def test_empty_markdown_context_skipped(self):
        prompt = build_prompt(
            message="Hi",
            conversation_history=[],
            markdown_context={},
        )
        assert "Investigation Reports" not in prompt


# ===========================================================================
# main handler delegation
# ===========================================================================
class TestMainHandler:
    """Verify that main() delegates to handle_chat_request."""

    def test_delegates_to_handle_chat_request(self):
        mock_request = MagicMock()
        with patch.object(cho_main, "handle_chat_request", return_value=("ok", 200, {})) as mock_handle:
            result = main_handler(mock_request)

        mock_handle.assert_called_once_with(mock_request, _config)
        assert result == ("ok", 200, {})
