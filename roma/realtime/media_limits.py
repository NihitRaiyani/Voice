"""Bound ordered WebSocket writes; TCP backpressure is not RTP packet loss."""
import asyncio

from starlette.websockets import WebSocketDisconnect


class BoundedMediaWebSocket:
    def __init__(self, socket, *, send_timeout_secs=1, max_message_bytes=131072, on_failure=None):
        if not 0 < send_timeout_secs <= 10 or not 1024 <= max_message_bytes <= 1048576:
            raise ValueError('Invalid media limits')
        self.socket, self.send_timeout_secs = socket, send_timeout_secs
        self.max_message_bytes = max_message_bytes
        self.on_failure = on_failure

    def __getattr__(self, name):
        return getattr(self.socket, name)

    async def _close(self, code):
        # Pipecat catches socket write exceptions, so request cancellation explicitly.
        if self.on_failure is not None:
            try:
                async with asyncio.timeout(.5):
                    await self.on_failure()
            except Exception:  # noqa: BLE001 — peer close still needs its own attempt.
                pass
        try:
            async with asyncio.timeout(min(.5, self.send_timeout_secs)):
                await self.socket.close(code=code)
        except Exception:  # noqa: BLE001 — closing an unavailable peer is best effort.
            pass

    async def _send(self, operation, value, **kwargs):
        size = len(value.encode()) if isinstance(value, str) else len(value)
        if size > self.max_message_bytes:
            await self._close(1009)
            raise WebSocketDisconnect(code=1009)
        try:
            async with asyncio.timeout(self.send_timeout_secs):
                await operation(value, **kwargs)
        except (TimeoutError, WebSocketDisconnect, RuntimeError, OSError):
            await self._close(1013)
            raise WebSocketDisconnect(code=1013) from None

    async def send_text(self, data):
        await self._send(self.socket.send_text, data)

    async def send_bytes(self, data):
        await self._send(self.socket.send_bytes, data)

    async def send_json(self, data, mode='text'):
        import json
        encoded = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
        await self._send(self.socket.send_text if mode == 'text' else self.socket.send_bytes,
                         encoded if mode == 'text' else encoded.encode())
