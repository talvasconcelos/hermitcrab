from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermitcrab.channels.telegram import TelegramChannel
from hermitcrab.config.schema import TelegramConfig


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
