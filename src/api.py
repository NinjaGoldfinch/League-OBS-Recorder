import asyncio
import ssl
import websockets
import json
import logging
from typing import Optional, Callable, Dict
from contextlib import asynccontextmanager

from logger import setup_logger
from auth import LCUAuth

class LCUWebSocket:
    RETRY_INTERVAL = 5  # seconds
    MAX_RETRIES = 3

    def __init__(self, league_path: str = None, log_level: str = 'INFO'):
        self.league_path = league_path
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.event_handlers: Dict[str, Callable] = {}
        self.logger = setup_logger('LCUWebSocket', 'lcu_websocket.log', log_level)
        self._running = False
        self._debug = self.logger.isEnabledFor(logging.DEBUG)
        self.auth = LCUAuth(league_path)

    @asynccontextmanager
    async def connection(self):
        """Context manager for WebSocket connection."""
        try:
            await self.connect()
            yield self
        finally:
            await self.close()

    async def connect(self):
        """Establish connection to LCU WebSocket with retry logic."""
        for attempt in range(self.MAX_RETRIES):
            try:
                port, auth_token = await self.auth.get_auth()
                if not port or not auth_token:
                    raise ConnectionError("Failed to obtain authentication details")
                
                ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                
                uri = f"wss://127.0.0.1:{port}"
                if self._debug:
                    self.logger.debug(f"Attempting connection to: {uri}")
                    self.logger.debug("SSL verification disabled for local connection")
                
                self.websocket = await websockets.connect(
                    uri,
                    ssl=ssl_context,
                    extra_headers={"Authorization": f"Basic {auth_token}"}
                )
                if self._debug:
                    self.logger.debug("WebSocket connection established successfully")
                self.logger.info("Connected to LCU WebSocket")
                break
                
            except Exception as e:
                if self._debug:
                    self.logger.debug(f"Connection error details: {str(e)}")
                if attempt == self.MAX_RETRIES - 1:
                    raise ConnectionError(f"Failed to connect after {self.MAX_RETRIES} attempts") from e
                self.logger.warning(f"Connection attempt {attempt + 1} failed, retrying in {self.RETRY_INTERVAL}s...")
                await asyncio.sleep(self.RETRY_INTERVAL)

    async def subscribe(self, event_path: str = "*", callback: Optional[Callable] = None):
        """Subscribe to LCU events."""
        if not self.websocket:
            raise ConnectionError("WebSocket not connected")

        subscribe_msg = [5, f"OnJsonApiEvent{('_' + event_path) if event_path != '*' else ''}"]
        await self.websocket.send(json.dumps(subscribe_msg))
        
        if callback:
            self.event_handlers[event_path] = callback
            self.logger.info(f"Subscribed to: {event_path}")

    async def listen(self):
        """Listen for events with automatic reconnection."""
        self._running = True
        self.logger.debug("Event listener started")
        
        while self._running:
            try:
                async for message in self.websocket:
                    if not self._running:  # Check if we should stop
                        self.logger.debug("Stopping message processing...")
                        break
                    await self._handle_message(message)
            except websockets.ConnectionClosed:
                if self._running:  # Only attempt reconnect if we're not shutting down
                    self.logger.warning("Connection closed, attempting to reconnect...")
                    await self.connect()
                else:
                    break
            except Exception as e:
                self.logger.error(f"Error in event loop: {e}")
                if self._running:
                    await asyncio.sleep(1)
                else:
                    break

    async def _handle_message(self, message: str):
        """Process incoming WebSocket messages."""
        try:
            data = json.loads(message)
            if not isinstance(data, list) or len(data) <= 2:
                if self._debug:
                    self.logger.debug(f"Invalid message format - Type: {type(data)}, Content: {data}")
                return

            message_id = data[0]  # Message ID is at index 0
            event_type = data[1]
            event_data = data[2]

            # if self._debug:
            #     self.logger.debug(f"Message ID: {message_id}")
            #     self.logger.debug(f"Event type received: {event_type}")
            #     self.logger.debug(f"Event data: {json.dumps(event_data, indent=2)}")
            #     self.logger.debug(f"Active subscriptions: {list(self.event_handlers.keys())}")

            for path, handler in self.event_handlers.items():
                if path in event_type:
                    await handler(path, event_data)
        except json.JSONDecodeError:
            if self._debug:
                self.logger.debug(f"Failed to decode message. Raw content: {message}")
            else:
                self.logger.warning("Failed to decode message")
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")

    async def close(self):
        """Clean up resources."""
        if not self._running:
            return
            
        self._running = False
        self.logger.info("Closing WebSocket connection...")
        
        if self.websocket:
            try:
                await self.websocket.close()
                self.logger.debug("Connection closed successfully")
            except Exception as e:
                self.logger.error(f"Error during closure: {e}")
            finally:
                self.websocket = None