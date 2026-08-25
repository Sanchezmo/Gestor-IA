"""
Command Layer V1 - Telegram Integration.

Preview keyboard and callback query handler.
"""

from __future__ import annotations

from uuid import UUID

from core.hermes.commands.executor import CommandExecutor
from core.hermes.commands.models import CommandPreview
from core.integrations.telegram.client import TelegramClient, TelegramMessage


async def send_command_preview(telegram: TelegramClient, chat_id: int, preview: CommandPreview) -> TelegramMessage:
    """Send preview with inline keyboard. Returns message."""
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Confirmar", "callback_data": f"confirm:{preview.command_id}"},
                {"text": "❌ Cancelar", "callback_data": f"cancel:{preview.command_id}"},
            ]
        ]
    }
    return await telegram.send_message(
        chat_id=chat_id,
        text=preview.summary,
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def handle_command_callback(
    executor: CommandExecutor,
    telegram: TelegramClient,
    chat_id: int,
    message_id: int,
    callback_data: str,
    telegram_user_id: int,
) -> None:
    """Handle confirm/cancel callback queries."""
    if callback_data.startswith("confirm:"):
        command_id = UUID(callback_data.split(":", 1)[1])
        result = await executor.confirm(command_id, telegram_user_id)

        if result.success and result.idempotent:
            resource_type = result.resource_type or "recurso"
            name = result.data.get("name") or result.data.get("label") or f"ID:{result.resource_id}"
            text = f"⚠️ Ya confirmado. {resource_type.capitalize()}: {name}"
        elif result.success:
            resource_type = result.resource_type or "recurso"
            name = result.data.get("name") or result.data.get("label") or f"ID:{result.resource_id}"
            text = f"✅ {resource_type.capitalize()} creado: {name}"
        else:
            text = f"❌ {result.error_message}"

        await telegram.edit_message_text(chat_id, message_id, text)

    elif callback_data.startswith("cancel:"):
        command_id = UUID(callback_data.split(":", 1)[1])
        result = await executor.cancel(command_id, telegram_user_id)
        text = "❌ Operación cancelada." if result.success else f"❌ {result.error_message}"
        await telegram.edit_message_text(chat_id, message_id, text)
