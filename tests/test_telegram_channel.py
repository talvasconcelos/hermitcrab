import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from loguru import logger

from hermitcrab.channels.telegram import TelegramChannel
from hermitcrab.config.schema import TelegramConfig


def test_telegram_allowlist_is_fail_closed_when_empty() -> None:
    bus = SimpleNamespace(publish_inbound=AsyncMock())
    channel = TelegramChannel(TelegramConfig(allow_from=[]), bus)

    assert channel.is_allowed("111|alice") is False
    assert channel.is_allowed("111") is False


def test_telegram_allowlist_wildcard_opens_channel() -> None:
    bus = SimpleNamespace(publish_inbound=AsyncMock())
    channel = TelegramChannel(TelegramConfig(allow_from=["*"]), bus)

    assert channel.is_allowed("111|alice") is True


def test_telegram_username_cannot_spoof_numeric_allowlist_id() -> None:
    bus = SimpleNamespace(publish_inbound=AsyncMock())
    channel = TelegramChannel(TelegramConfig(allow_from=["111"]), bus)

    # A different user whose username equals the whitelisted numeric id must not pass.
    assert channel.is_allowed("222|111") is False
    assert channel.is_allowed("111|alice") is True


@pytest.mark.asyncio
async def test_unauthorized_message_skips_media_download_and_transcription() -> None:
    bus = SimpleNamespace(publish_inbound=AsyncMock())
    channel = TelegramChannel(TelegramConfig(allow_from=["999"]), bus)

    file_obj = SimpleNamespace(download_to_drive=AsyncMock())
    bot = SimpleNamespace(get_file=AsyncMock(return_value=file_obj))
    channel._app = SimpleNamespace(bot=bot)
    channel._start_typing = MagicMock()
    channel._stop_typing = MagicMock()

    message = SimpleNamespace(
        chat_id=123,
        text=None,
        caption=None,
        photo=None,
        voice=SimpleNamespace(file_id="voice_file", mime_type="audio/ogg"),
        audio=None,
        document=None,
        message_id=1,
        chat=SimpleNamespace(type="private"),
    )
    user = SimpleNamespace(id=111, username="intruder", first_name="Bad")
    update = SimpleNamespace(message=message, effective_user=user)

    await channel._on_message(update, None)

    bot.get_file.assert_not_called()
    file_obj.download_to_drive.assert_not_called()
    channel._start_typing.assert_not_called()
    channel._stop_typing.assert_called_once_with("123")
    bus.publish_inbound.assert_not_called()


@pytest.mark.asyncio
async def test_authorized_message_starts_typing_and_publishes() -> None:
    bus = SimpleNamespace(publish_inbound=AsyncMock())
    channel = TelegramChannel(TelegramConfig(allow_from=["111"]), bus)
    channel._app = SimpleNamespace(bot=SimpleNamespace(get_file=AsyncMock()))
    channel._start_typing = MagicMock()
    channel._stop_typing = MagicMock()

    message = SimpleNamespace(
        chat_id=123,
        text="hello",
        caption=None,
        photo=None,
        voice=None,
        audio=None,
        document=None,
        message_id=2,
        chat=SimpleNamespace(type="private"),
    )
    user = SimpleNamespace(id=111, username="tal", first_name="Tal")
    update = SimpleNamespace(message=message, effective_user=user)

    await channel._on_message(update, None)

    channel._start_typing.assert_called_once_with("123")
    channel._stop_typing.assert_not_called()
    bus.publish_inbound.assert_called_once()


@pytest.mark.asyncio
async def test_voice_transcription_content_is_not_logged(monkeypatch) -> None:
    bus = SimpleNamespace(publish_inbound=AsyncMock())
    channel = TelegramChannel(TelegramConfig(allow_from=["111"]), bus)
    file_obj = SimpleNamespace(download_to_drive=AsyncMock())
    channel._app = SimpleNamespace(bot=SimpleNamespace(get_file=AsyncMock(return_value=file_obj)))
    channel._start_typing = MagicMock()
    channel._stop_typing = MagicMock()

    class FakeTranscriber:
        def __init__(self, api_key: str = "") -> None:
            pass

        async def transcribe(self, path) -> str:
            return "SUPER SECRET VOICE CONTENT"

    monkeypatch.setattr(
        "hermitcrab.providers.transcription.GroqTranscriptionProvider", FakeTranscriber
    )

    message = SimpleNamespace(
        chat_id=123,
        text=None,
        caption=None,
        photo=None,
        voice=SimpleNamespace(file_id="voice_file", mime_type="audio/ogg"),
        audio=None,
        document=None,
        message_id=3,
        chat=SimpleNamespace(type="private"),
    )
    user = SimpleNamespace(id=111, username="tal", first_name="Tal")
    update = SimpleNamespace(message=message, effective_user=user)

    buf = io.StringIO()
    handler_id = logger.add(buf, level="DEBUG")
    try:
        await channel._on_message(update, None)
    finally:
        logger.remove(handler_id)

    assert "SUPER SECRET VOICE CONTENT" not in buf.getvalue()
    bus.publish_inbound.assert_called_once()
