"""
Telegram Bot Client - Abstracción para Telegram Bot API.

REUTILIZADO desde Transvega Animal - integration-api/app/core/telegram_client.py
Limpio, genérico, con mock para tests.
"""

from dataclasses import dataclass
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger()


@dataclass
class TelegramFile:
    """Representa un archivo de Telegram listo para descargar."""
    file_id: str
    file_path: str
    file_size: Optional[int] = None
    file_unique_id: Optional[str] = None


@dataclass
class TelegramMessage:
    """Resultado de enviar un mensaje."""
    message_id: int
    chat_id: int
    date: int
    text: Optional[str] = None


class TelegramClient:
    """
    Cliente para Telegram Bot API.
    
    Encapsula todas las llamadas HTTP a Telegram Bot API.
    Usa bot_token pasado por constructor (por instancia).
    """
    
    def __init__(self, bot_token: Optional[str] = None):
        self.bot_token = bot_token
        self._client: Optional[httpx.AsyncClient] = None
        self._api_base = "https://api.telegram.org/bot"
        
        if not self.bot_token:
            logger.warning("telegram_client_no_token", message="bot_token not configured")
    
    @property
    def base_url(self) -> str:
        if not self.bot_token:
            raise ValueError("bot_token is required but not configured")
        return f"{self._api_base}{self.bot_token}"
    
    async def start(self) -> None:
        """Inicializar cliente HTTP."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
            logger.info("telegram_client_started")
    
    async def close(self) -> None:
        """Cerrar cliente HTTP."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("telegram_client_closed")
    
    async def __aenter__(self) -> "TelegramClient":
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
    
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("TelegramClient not started. Call start() first or use async context manager.")
        return self._client
    
    # =========================================================================
    # MÉTODOS CORE
    # =========================================================================
    
    async def _post(self, method: str, **kwargs) -> dict[str, Any]:
        """POST a Telegram Bot API."""
        url = f"{self.base_url}/{method}"
        response = await self.client.post(url, json=kwargs)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise TelegramAPIError(data.get("description", "Unknown Telegram API error"))
        return data.get("result", {})
    
    async def _get(self, method: str, **kwargs) -> dict[str, Any]:
        """GET a Telegram Bot API."""
        url = f"{self.base_url}/{method}"
        response = await self.client.get(url, params=kwargs)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise TelegramAPIError(data.get("description", "Unknown Telegram API error"))
        return data.get("result", {})
    
    # =========================================================================
    # MENSAJES
    # =========================================================================
    
    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = "HTML",
        disable_web_page_preview: bool = True,
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[dict] = None,
    ) -> TelegramMessage:
        """Enviar mensaje de texto."""
        logger.info("telegram_send_message", chat_id=chat_id, text_length=len(text))
        
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        if reply_markup:
            payload["reply_markup"] = reply_markup
        
        result = await self._post("sendMessage", **payload)
        
        return TelegramMessage(
            message_id=result["message_id"],
            chat_id=result["chat"]["id"],
            date=result["date"],
            text=result.get("text"),
        )
    
    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
        url: Optional[str] = None,
        cache_time: int = 0,
    ) -> bool:
        """Responder callback query (para inline keyboards)."""
        payload = {"callback_query_id": callback_query_id}
        if text is not None:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        if url:
            payload["url"] = url
        if cache_time:
            payload["cache_time"] = cache_time
        
        result = await self._post("answerCallbackQuery", **payload)
        return result
    
    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: Optional[str] = "HTML",
        reply_markup: Optional[dict] = None,
    ) -> TelegramMessage:
        """Editar texto de mensaje enviado por el bot."""
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        
        result = await self._post("editMessageText", **payload)
        
        return TelegramMessage(
            message_id=result["message_id"],
            chat_id=result["chat"]["id"],
            date=result["date"],
            text=result.get("text"),
        )
    
    async def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Borrar mensaje."""
        result = await self._post("deleteMessage", chat_id=chat_id, message_id=message_id)
        return result
    
    # =========================================================================
    # ARCHIVOS
    # =========================================================================
    
    async def get_file(self, file_id: str) -> TelegramFile:
        """Obtener info de archivo (file_path para descarga)."""
        logger.debug("telegram_get_file", file_id=file_id)
        result = await self._get("getFile", file_id=file_id)
        
        return TelegramFile(
            file_id=result["file_id"],
            file_path=result["file_path"],
            file_size=result.get("file_size"),
            file_unique_id=result.get("file_unique_id"),
        )
    
    async def download_file(self, file_path: str) -> bytes:
        """Descargar archivo desde servidores de Telegram."""
        if not self.bot_token:
            raise ValueError("bot_token required for file download")
        
        download_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        logger.debug("telegram_download_file", file_path=file_path)
        
        response = await self.client.get(download_url, timeout=60.0)
        response.raise_for_status()
        
        content = response.content
        logger.info("telegram_file_downloaded", file_path=file_path, size=len(content))
        return content
    
    async def get_file_and_download(self, file_id: str) -> bytes:
        """Convenience: get_file + download en una llamada."""
        file_info = await self.get_file(file_id)
        return await self.download_file(file_info.file_path)
    
    # =========================================================================
    # FOTOS/MEDIA
    # =========================================================================
    
    async def send_photo(
        self,
        chat_id: int,
        photo: str,  # file_id o URL
        caption: Optional[str] = None,
        parse_mode: Optional[str] = "HTML",
    ) -> TelegramMessage:
        """Enviar foto."""
        payload = {"chat_id": chat_id, "photo": photo}
        if caption:
            payload["caption"] = caption
        if parse_mode:
            payload["parse_mode"] = parse_mode
        
        result = await self._post("sendPhoto", **payload)
        
        return TelegramMessage(
            message_id=result["message_id"],
            chat_id=result["chat"]["id"],
            date=result["date"],
            text=result.get("caption"),
        )
    
    async def send_document(
        self,
        chat_id: int,
        document: str,  # file_id o URL
        caption: Optional[str] = None,
        parse_mode: Optional[str] = "HTML",
    ) -> TelegramMessage:
        """Enviar documento."""
        payload = {"chat_id": chat_id, "document": document}
        if caption:
            payload["caption"] = caption
        if parse_mode:
            payload["parse_mode"] = parse_mode
        
        result = await self._post("sendDocument", **payload)
        
        return TelegramMessage(
            message_id=result["message_id"],
            chat_id=result["chat"]["id"],
            date=result["date"],
            text=result.get("caption"),
        )


class TelegramAPIError(Exception):
    """Excepción para errores de Telegram API."""
    pass


# =========================================================================
# MOCK PARA TESTING
# =========================================================================

class MockTelegramClient(TelegramClient):
    """Mock Telegram client para testing."""
    
    def __init__(self):
        self.bot_token = "mock-token"
        self._client = None
        self._api_base = "https://api.telegram.org/bot"
        
        # Call tracking
        self.calls: list[dict[str, Any]] = []
        self._send_message_results: list[TelegramMessage] = []
        self._get_file_results: dict[str, TelegramFile] = {}
        self._download_results: dict[str, bytes] = {}
    
    async def start(self) -> None:
        pass
    
    async def close(self) -> None:
        pass
    
    def mock_send_message(self, result: TelegramMessage) -> None:
        self._send_message_results.append(result)
    
    def mock_get_file(self, file_id: str, result: TelegramFile) -> None:
        self._get_file_results[file_id] = result
    
    def mock_download(self, file_path: str, content: bytes) -> None:
        self._download_results[file_path] = content
    
    async def send_message(self, chat_id: int, text: str, **kwargs) -> TelegramMessage:
        self.calls.append({
            "method": "send_message",
            "chat_id": chat_id,
            "text": text,
            "kwargs": kwargs,
        })
        
        if self._send_message_results:
            return self._send_message_results.pop(0)
        
        return TelegramMessage(
            message_id=len(self.calls),
            chat_id=chat_id,
            date=0,
            text=text,
        )
    
    async def answer_callback_query(self, **kwargs) -> bool:
        self.calls.append({"method": "answer_callback_query", "kwargs": kwargs})
        return True
    
    async def get_file(self, file_id: str) -> TelegramFile:
        self.calls.append({"method": "get_file", "file_id": file_id})
        
        if file_id in self._get_file_results:
            return self._get_file_results[file_id]
        
        return TelegramFile(
            file_id=file_id,
            file_path=f"photos/{file_id}.jpg",
            file_size=1024,
        )
    
    async def download_file(self, file_path: str) -> bytes:
        self.calls.append({"method": "download_file", "file_path": file_path})
        
        if file_path in self._download_results:
            return self._download_results[file_path]
        
        return b"fake-image-data"
    
    async def get_file_and_download(self, file_id: str) -> bytes:
        self.calls.append({"method": "get_file_and_download", "file_id": file_id})
        file_info = await self.get_file(file_id)
        return await self.download_file(file_info.file_path)


# Factory
async def create_telegram_client(bot_token: Optional[str] = None) -> TelegramClient:
    """Crear y arrancar TelegramClient."""
    client = TelegramClient(bot_token)
    await client.start()
    return client