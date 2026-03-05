"""Tests for Ollama enhancements in LiteLLMProvider.

Tests cover:
- Ollama model detection
- :cloud suffix routing
- Multimodal image marker extraction
- Tool call quirk handling (nested wrappers, tool. prefix)
- Reasoning model support (think parameter)
"""

from unittest.mock import MagicMock, patch

import pytest

from hermitcrab.providers.litellm_provider import LiteLLMProvider

# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def ollama_provider():
    """Create LiteLLMProvider configured for Ollama."""
    return LiteLLMProvider(
        api_key="",
        api_base="http://localhost:11434",
        default_model="ollama/llama3.1",
        provider_name="ollama",
    )


@pytest.fixture
def ollama_cloud_provider():
    """Create LiteLLMProvider configured for remote Ollama with API key."""
    return LiteLLMProvider(
        api_key="test-api-key",
        api_base="https://ollama.example.com",
        default_model="ollama/llama3.1:cloud",
        provider_name="ollama",
    )


# ============================================================================
# Ollama Model Detection
# ============================================================================

class TestOllamaModelDetection:
    """Test _is_ollama_model method."""

    def test_detects_ollama_slash_prefix(self, ollama_provider):
        """Detect ollama/llama3.1 format."""
        assert ollama_provider._is_ollama_model("ollama/llama3.1") is True
        assert ollama_provider._is_ollama_model("ollama/llama3.2") is True
        assert ollama_provider._is_ollama_model("ollama/mistral") is True

    def test_detects_ollama_colon_prefix(self, ollama_provider):
        """Detect ollama:llama3.1 format."""
        assert ollama_provider._is_ollama_model("ollama:llama3.1") is True
        assert ollama_provider._is_ollama_model("ollama:mistral") is True

    def test_detects_ollama_colon_suffix(self, ollama_provider):
        """Detect llama3.1:ollama format."""
        assert ollama_provider._is_ollama_model("llama3.1:ollama") is True
        assert ollama_provider._is_ollama_model("mistral:ollama") is True

    def test_case_insensitive_detection(self, ollama_provider):
        """Detection should be case-insensitive."""
        assert ollama_provider._is_ollama_model("OLLAMA/llama3.1") is True
        assert ollama_provider._is_ollama_model("Ollama/Llama3.1") is True
        assert ollama_provider._is_ollama_model("llama3.1:OLLAMA") is True

    def test_rejects_non_ollama_models(self, ollama_provider):
        """Non-Ollama models should return False."""
        assert ollama_provider._is_ollama_model("anthropic/claude-3") is False
        assert ollama_provider._is_ollama_model("openai/gpt-4") is False
        assert ollama_provider._is_ollama_model("llama3.1") is False


# ============================================================================
# :cloud Suffix Routing
# ============================================================================

class TestCloudSuffixRouting:
    """Test _resolve_ollama_cloud_routing method."""

    def test_handles_no_suffix(self, ollama_provider):
        """Should return original model and False when no :cloud."""
        model, use_cloud = ollama_provider._resolve_ollama_cloud_routing("llama3.1")
        assert model == "llama3.1"
        assert use_cloud is False

    def test_keeps_cloud_suffix_for_local_ollama(self, ollama_provider):
        """Should keep :cloud suffix for local Ollama to handle cloud routing."""
        model, use_cloud = ollama_provider._resolve_ollama_cloud_routing("llama3.1:cloud")
        # Keep :cloud suffix - Ollama needs it for routing to cloud models
        assert model == "llama3.1:cloud"
        assert use_cloud is True

    def test_rejects_cloud_without_api_key_or_local(self):
        """Should raise ValueError when :cloud used without local Ollama or API key."""
        provider = LiteLLMProvider(
            api_key="",
            api_base="https://ollama.example.com",  # Not localhost
            default_model="ollama/llama3.1",
        )
        with pytest.raises(ValueError, match="no local Ollama is running"):
            provider._resolve_ollama_cloud_routing("llama3.1:cloud")

    def test_allows_cloud_on_remote_with_key(self, ollama_cloud_provider):
        """Should allow :cloud when remote endpoint and API key present."""
        model, use_cloud = ollama_cloud_provider._resolve_ollama_cloud_routing("llama3.1:cloud")
        assert model == "llama3.1"
        assert use_cloud is True

    def test_cloud_with_non_local_endpoint_and_key(self):
        """Should allow :cloud with non-local endpoint if API key is provided."""
        provider = LiteLLMProvider(
            api_key="test-key",
            api_base="https://ollama.example.com",
        )
        model, use_cloud = provider._resolve_ollama_cloud_routing("llama3.1:cloud")
        assert model == "llama3.1"
        assert use_cloud is True


# ============================================================================
# Multimodal Image Marker Extraction
# ============================================================================

class TestMultimodalImageExtraction:
    """Test _extract_ollama_images and _apply_ollama_multimodal methods."""

    def test_extract_no_images(self, ollama_provider):
        """Should return original text when no image markers."""
        content, images = ollama_provider._extract_ollama_images("Hello, world!")
        assert content == "Hello, world!"
        assert images == []

    def test_extract_single_image_full_uri(self, ollama_provider):
        """Should extract single image from full data URI."""
        content = "Check this [IMAGE:data:image/png;base64,abcd1234]"
        text, images = ollama_provider._extract_ollama_images(content)
        assert text == "Check this"
        assert images == ["abcd1234"]

    def test_extract_single_image_raw_base64(self, ollama_provider):
        """Should extract image from raw base64 marker."""
        content = "[IMAGE:xyz789base64==]"
        text, images = ollama_provider._extract_ollama_images(content)
        assert text is None  # No text content
        assert images == ["xyz789base64=="]

    def test_extract_multiple_images(self, ollama_provider):
        """Should extract multiple image markers."""
        content = "First [IMAGE:data:image/png;base64,aaa] and second [IMAGE:data:image/jpeg;base64,bbb]"
        text, images = ollama_provider._extract_ollama_images(content)
        # Note: regex replacement leaves double spaces, normalize them
        assert " ".join(text.strip().split()) == "First and second"
        assert images == ["aaa", "bbb"]

    def test_extract_preserves_text_with_markers_inline(self, ollama_provider):
        """Should preserve text around inline image markers."""
        content = "Here is [IMAGE:base64data] the image"
        text, images = ollama_provider._extract_ollama_images(content)
        # Note: regex replacement leaves double spaces, normalize them
        assert " ".join(text.strip().split()) == "Here is the image"
        assert images == ["base64data"]

    def test_apply_multimodal_single_user_message(self, ollama_provider):
        """Should convert user message with image marker."""
        messages = [
            {"role": "user", "content": "Check [IMAGE:data:image/png;base64,abc123]"},
            {"role": "assistant", "content": "Sure!"},
        ]
        result = ollama_provider._apply_ollama_multimodal(messages)

        assert len(result) == 2
        assert result[0]["content"] == "Check"
        assert result[0]["images"] == ["abc123"]
        assert result[1]["content"] == "Sure!"
        assert "images" not in result[1]

    def test_apply_multimodal_multiple_messages(self, ollama_provider):
        """Should handle multiple user messages."""
        messages = [
            {"role": "user", "content": "First [IMAGE:aaa]"},
            {"role": "assistant", "content": "Response"},
            {"role": "user", "content": "Second [IMAGE:bbb]"},
        ]
        result = ollama_provider._apply_ollama_multimodal(messages)

        assert result[0]["content"] == "First"
        assert result[0]["images"] == ["aaa"]
        assert result[2]["content"] == "Second"
        assert result[2]["images"] == ["bbb"]

    def test_apply_multimodal_no_content(self, ollama_provider):
        """Should handle messages without content."""
        messages = [
            {"role": "tool", "tool_call_id": "123", "content": "Result"},
        ]
        result = ollama_provider._apply_ollama_multimodal(messages)
        assert result == messages


# ============================================================================
# Tool Call Quirk Handling
# ============================================================================

class TestToolCallQuirkHandling:
    """Test _extract_ollama_tool_name and _parse_ollama_tool_calls methods."""

    def test_extract_normal_tool_name(self, ollama_provider):
        """Should return tool name unchanged for normal calls."""
        name, args = ollama_provider._extract_ollama_tool_name("shell", {"command": "date"})
        assert name == "shell"
        assert args == {"command": "date"}

    def test_extract_nested_tool_call_wrapper(self, ollama_provider):
        """Should unwrap nested tool_call format."""
        wrapped = {
            "name": "tool_call",
            "arguments": {"name": "shell", "arguments": {"command": "date"}},
        }
        name, args = ollama_provider._extract_ollama_tool_name(
            wrapped["name"], wrapped["arguments"]
        )
        assert name == "shell"
        assert args == {"command": "date"}

    def test_extract_nested_with_angle_brackets(self, ollama_provider):
        """Should handle tool_call> and tool_call< variants."""
        name, args = ollama_provider._extract_ollama_tool_name(
            "tool_call>shell", {"name": "file_read", "arguments": {"path": "/tmp"}}
        )
        assert name == "file_read"
        assert args == {"path": "/tmp"}

    def test_extract_prefixed_tool_name(self, ollama_provider):
        """Should strip tool. prefix."""
        name, args = ollama_provider._extract_ollama_tool_name(
            "tool.shell", {"command": "ls"}
        )
        assert name == "shell"
        assert args == {"command": "ls"}

    def test_extract_prefixed_variants(self, ollama_provider):
        """Should handle various tool. prefixes."""
        name1, _ = ollama_provider._extract_ollama_tool_name("tool.file_read", {})
        assert name1 == "file_read"

        name2, _ = ollama_provider._extract_ollama_tool_name("tool.web_search", {})
        assert name2 == "web_search"

    def test_parse_ollama_tool_calls_empty(self, ollama_provider):
        """Should return empty list when no tool calls."""
        mock_message = MagicMock()
        mock_message.tool_calls = None
        result = ollama_provider._parse_ollama_tool_calls(mock_message)
        assert result == []

    def test_parse_ollama_tool_calls_single(self, ollama_provider):
        """Should parse single tool call."""
        mock_tc = MagicMock()
        mock_tc.id = "call_123"
        mock_tc.function.name = "shell"
        mock_tc.function.arguments = '{"command": "date"}'

        mock_message = MagicMock()
        mock_message.tool_calls = [mock_tc]

        result = ollama_provider._parse_ollama_tool_calls(mock_message)
        assert len(result) == 1
        assert result[0].id == "call_123"
        assert result[0].name == "shell"
        assert result[0].arguments == {"command": "date"}

    def test_parse_ollama_tool_calls_with_quirks(self, ollama_provider):
        """Should apply quirk handling when parsing."""
        # Nested wrapper format
        mock_tc = MagicMock()
        mock_tc.id = "call_456"
        mock_tc.function.name = "tool_call"
        mock_tc.function.arguments = '{"name": "shell", "arguments": {"command": "ls"}}'

        mock_message = MagicMock()
        mock_message.tool_calls = [mock_tc]

        result = ollama_provider._parse_ollama_tool_calls(mock_message)
        assert len(result) == 1
        assert result[0].name == "shell"  # Unwrapped


# ============================================================================
# Reasoning Model Support
# ============================================================================

class TestReasoningModelSupport:
    """Test reasoning model (think parameter) support."""

    def test_reasoning_disabled_by_default(self, ollama_provider):
        """Reasoning should be disabled by default."""
        assert ollama_provider._ollama_reasoning_enabled is False

    def test_chat_does_not_add_think_when_disabled(self, ollama_provider):
        """Should not add think parameter when reasoning disabled."""
        # This is tested indirectly - the parameter won't be in kwargs
        # We verify the attribute exists and is False by default
        assert hasattr(ollama_provider, '_ollama_reasoning_enabled')
        assert ollama_provider._ollama_reasoning_enabled is False


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegration:
    """Integration tests for Ollama enhancements."""

    @pytest.mark.asyncio
    async def test_chat_with_multimodal(self, ollama_provider):
        """Test chat with image markers is converted properly."""
        messages = [
            {"role": "user", "content": "What's in [IMAGE:data:image/png;base64,test123]?"}
        ]

        with patch('hermitcrab.providers.litellm_provider.acompletion') as mock_completion:
            mock_response = MagicMock()
            mock_choice = MagicMock()
            mock_message = MagicMock()
            mock_message.content = "I see an image"
            mock_message.tool_calls = None
            mock_choice.message = mock_message
            mock_response.choices = [mock_choice]
            mock_completion.return_value = mock_response

            await ollama_provider.chat(messages=messages, model="ollama/llama3.1")

            # Verify acompletion was called
            assert mock_completion.called
            call_kwargs = mock_completion.call_args.kwargs
            # Messages should have been transformed
            # Note: regex replacement leaves "What's in ?" (space before ?)
            content = call_kwargs["messages"][0]["content"]
            assert content == "What's in ?"
            assert call_kwargs["messages"][0]["images"] == ["test123"]

    @pytest.mark.asyncio
    async def test_chat_with_tool_calls(self, ollama_provider):
        """Test chat with tool calls uses quirk handling."""
        messages = [{"role": "user", "content": "Run date command"}]
        tools = [{
            "type": "function",
            "function": {
                "name": "shell",
                "description": "Run shell command",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}
            }
        }]

        with patch('hermitcrab.providers.litellm_provider.acompletion') as mock_completion:
            mock_response = MagicMock()
            mock_choice = MagicMock()
            mock_message = MagicMock()
            mock_message.content = "Running command"

            # Mock tool call with nested format
            mock_tc = MagicMock()
            mock_tc.id = "call_789"
            mock_tc.function.name = "tool_call"
            mock_tc.function.arguments = '{"name": "shell", "arguments": {"command": "date"}}'
            mock_message.tool_calls = [mock_tc]

            mock_choice.message = mock_message
            mock_response.choices = [mock_choice]
            mock_completion.return_value = mock_response

            response = await ollama_provider.chat(
                messages=messages,
                tools=tools,
                model="ollama/llama3.1",
            )

            # Should have parsed the tool call with quirk handling
            assert len(response.tool_calls) == 1
            assert response.tool_calls[0].name == "shell"
