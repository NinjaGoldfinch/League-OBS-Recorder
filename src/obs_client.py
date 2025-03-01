import tracemalloc
tracemalloc.start()

from logger import setup_logger
import asyncio
from obswebsocket import obsws, requests, events  # Changed import
from typing import Optional, Dict, Any, Callable, Coroutine, Union
import time
import os
import shutil
import concurrent.futures
import threading

class OBSClient:
    def __init__(self, host: str = "localhost", port: int = 4455, password: str = "", timeout: float = 3.0, log_level: str = 'INFO'):
        """Initialize OBS WebSocket client"""
        self.host = host
        self.port = port
        self.password = password
        self.timeout = 10.0  # Increased default timeout
        self.ws = None
        self.current_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.current_loop)
        self.last_recording_path = None
        self.connected = False
        self.connection_error = None
        self.last_error = None
        self.response_data = None
        self._lock = threading.Lock()
        self.operation_timeout = 5  # Increased from 3.0 to 10.0 seconds
        self.ws_timeout = 3.0  # Increased from 5.0 to 10.0 seconds
        self.disconnect_timeout = 10.0  # New longer timeout specifically for disconnect
        
        # Add shutdown event and task tracking
        self._shutdown_event = threading.Event()
        self._tasks = set()
        self._loop_lock = threading.Lock()
        
        # Logger initialization
        self.logger = setup_logger('OBSClient', 'obs_client.log', log_level)
        
        # Replace simpleobsws with obsws
        self.ws = obsws(host=host, port=port, password=password)
        self.ws.register(self._on_event)  # Register event handler
        
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="OBSClient")
        self._main_loop = None
        self._loop_owner = None

        self._recording_event = asyncio.Event()
        self._recording_state = None
        self._recording_path = None

    def _mask_password(self, password: str) -> str:
        """Mask password to show only first 4 and last 4 characters"""
        if not password or len(password) < 8:
            return "****"
        return f"{password[:4]}...{password[-4:]}"

    def _get_connection_info(self) -> str:
        """Format connection details for logging"""
        return (
            f"Connection Details:\n"
            f"  Host: {self.host}\n"
            f"  Port: {self.port}\n"
            f"  Password: {self._mask_password(self.password)}\n"
            f"  Timeout: {self.timeout}s"
        )

    def _on_event(self, event):
        """Handle OBS events"""
        try:
            # Events in obs-websocket don't have type_name, use __class__.__name__ instead
            event_type = event.__class__.__name__
            self.logger.debug(f"OBS Event received: {event_type}")
            
            # Special handling for record state changes
            if isinstance(event, events.RecordStateChanged):
                event_data = event.__dict__.get('datain', {})
                self.logger.debug(f"Recording state changed: {event.__dict__}")
                
                # Update recording state
                self._recording_state = event_data.get('outputState')
                if event_data.get('outputPath'):
                    self._recording_path = event_data['outputPath']
                
                # Set event when recording has started
                if (self._recording_state == 'OBS_WEBSOCKET_OUTPUT_STARTED' 
                    and event_data.get('outputActive', False)):
                    self._recording_event.set()
        except Exception as e:
            self.logger.error(f"Error handling OBS event: {e}")

    def _extract_response_data(self, response, field_name: Optional[str] = None) -> Any:
        """Extract data from OBS response using consistent pattern"""
        try:
            if not response:
                return None
                
            response_dict = vars(response)
            if 'datain' not in response_dict:
                return None
                
            datain = response_dict['datain']
            if not isinstance(datain, dict):
                return None
                
            if field_name:
                return datain.get(field_name)
            return datain
            
        except Exception as e:
            self.logger.error(f"Error extracting response data: {e}")
            return None

    def _process_response(self, response, expected_fields: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process OBS response and extract relevant data with validation
        
        Args:
            response: Raw OBS response object
            expected_fields: Dictionary of field names and their expected types
                           e.g. {'outputActive': bool, 'profiles': list}
        
        Returns:
            Dictionary containing processed data
        """
        try:
            # Extract base data
            data = self._extract_response_data(response)
            if not data:
                return {}

            # If no field validation needed, return all data
            if not expected_fields:
                return data

            # Validate and extract specific fields
            result = {}
            for field, expected_type in expected_fields.items():
                value = data.get(field)
                
                # Handle special case for Optional types
                if expected_type is Optional[str] and value is None:
                    result[field] = None
                    continue
                    
                # Validate type and convert if needed
                if value is not None and isinstance(value, expected_type):
                    result[field] = value
                else:
                    self.logger.warning(
                        f"Field '{field}' has unexpected type or value. "
                        f"Expected {expected_type}, got {type(value)}"
                    )
                    result[field] = None

            return result

        except Exception as e:
            self.logger.error(f"Error processing response: {e}")
            return {}

    async def _make_request(self, request_type: str, data: Dict = None) -> Optional[Dict]:
        """Make WebSocket request with better error handling"""
        if not self.ws or not self.connected:
            self.logger.error(f"Cannot make request {request_type}: Not connected to OBS")
            return None

        try:
            # Validate request type exists
            if not hasattr(requests, request_type):
                self.logger.error(f"Invalid request type: {request_type}")
                return None
            
            # Create request object based on type
            request_class = getattr(requests, request_type)
            
            # Log request attempt
            self.logger.debug(f"Making request: {request_type} with data: {data}")
            
            # Call with or without data
            try:
                if data:
                    response = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None, 
                            lambda: self.ws.call(request_class(**data))
                        ),
                        timeout=self.ws_timeout
                    )
                else:
                    response = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: self.ws.call(request_class())
                        ),
                        timeout=self.ws_timeout
                    )
                
                # Log raw response for debugging
                self.logger.debug(f"Raw response from {request_type}: {response}")
                
                # Extract and validate response data
                extracted_data = self._extract_response_data(response)
                if extracted_data is None:
                    self.logger.warning(f"No data extracted from {request_type} response")
                    
                return extracted_data
                
            except asyncio.TimeoutError:
                self.logger.error(f"Request {request_type} timed out after {self.ws_timeout}s")
                return None
            except AttributeError as ae:
                self.logger.error(f"Request {request_type} failed: Invalid request format - {ae}")
                return None
            except Exception as e:
                self.logger.error(f"Request {request_type} failed: {str(e)}")
                return None
                
        except Exception as e:
            self.logger.error(f"Request {request_type} error: {str(e)}")
            return None

    async def _connect(self) -> bool:
        """Async connect to OBS WebSocket with timeout"""
        try:
            self.logger.debug(f"Attempting to connect to OBS...\n{self._get_connection_info()}")
            self.last_error = None
            self.connection_error = None
            self.response_data = None
            
            try:
                # Test socket availability and connect
                await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, self.ws.connect),
                    timeout=self.timeout
                )
                self.logger.debug("WebSocket connection successful")
                
                # Test connection with version request
                version_response = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(None, self.ws.call, requests.GetVersion()),
                    timeout=self.timeout
                )
                
                version_data = self._extract_response_data(version_response)
                self.response_data = version_data
                self.logger.debug(f"OBS Version: {version_data}")
                self.connected = True

                # Reset event state on connection
                self._recording_event.clear()
                self._recording_state = None
                self._recording_path = None

                return True
                
            except Exception as e:
                self.last_error = f"Connection failed: {str(e)}"
                self.logger.error(self.last_error)
                raise
                
        except Exception as e:
            self.logger.error(f"Failed to connect: {e}")
            return False

    async def async_connect(self) -> bool:
        """Async version of connect method"""
        try:
            return await self._connect()
        except Exception as e:
            if str(e) and str(e) != "Unknown error":
                error_msg = f"Connection error: {str(e)}"
                self.logger.error(error_msg)
                self.connection_error = error_msg
            return False

    def connect(self) -> bool:
        """Synchronous connect using current loop"""
        try:
            loop = self._get_loop()
            return loop.run_until_complete(self.async_connect())
        except Exception as e:
            if str(e) and str(e) != "Unknown error":
                error_msg = f"Connection error: {str(e)}"
                self.logger.error(error_msg)
                self.connection_error = error_msg
            return False

    async def _disconnect(self):
        """Async disconnect from OBS WebSocket with improved cleanup"""
        if not self.ws:
            return

        try:
            # Shorter timeout for individual operations
            op_timeout = 2.0
            
            # Check recording status with timeout
            try:
                status = await asyncio.wait_for(
                    self._make_request('GetRecordStatus'),
                    timeout=op_timeout
                )
                if status and status.get('outputActive', False):
                    self.logger.warning("Recording still active during disconnect")
                    try:
                        await asyncio.wait_for(
                            self._make_request('StopRecord'),
                            timeout=op_timeout
                        )
                        # Add small delay for recording to stop
                        await asyncio.sleep(1)
                    except asyncio.TimeoutError:
                        self.logger.warning("Stop recording timed out during disconnect")
            except Exception as e:
                self.logger.warning(f"Error checking recording status: {e}")

            # Disconnect WebSocket with timeout
            try:
                disconnect_future = asyncio.get_event_loop().run_in_executor(
                    None, self.ws.disconnect
                )
                await asyncio.wait_for(disconnect_future, timeout=op_timeout)
                self.logger.info("WebSocket disconnected successfully")
            except asyncio.TimeoutError:
                self.logger.warning("WebSocket disconnect timed out, forcing closure")
            except Exception as e:
                self.logger.error(f"WebSocket disconnect error: {e}")
        finally:
            # Ensure cleanup happens regardless of errors
            self.ws = None
            self.connected = False
            self._recording_state = None
            self._recording_path = None

    def _ensure_loop_not_running(self):
        """Ensure the event loop is not running"""
        try:
            loop = self._get_loop()
            if loop.is_running():
                self.logger.debug("Creating new event loop since current one is running")
                loop.close()
                self._main_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._main_loop)
                return self._main_loop
            return loop
        except Exception as e:
            self.logger.error(f"Error ensuring loop not running: {e}")
            return asyncio.new_event_loop()

    def _run_operation(
        self, 
        operation: Union[Callable, Coroutine], 
        timeout: float = 5.0, 
        silence_errors: bool = False
    ) -> Any:
        """Execute an operation with timeout"""
        if not self.ws or not self.connected:
            self.logger.error("Not connected to OBS")
            return False

        try:
            loop = self._get_loop()

            def sync_wrapper():
                try:
                    return operation()
                except Exception as e:
                    if not silence_errors:
                        self.logger.error(f"Sync operation failed: {str(e)}")
                    return False

            # If it's a synchronous operation, run it directly in the thread pool
            if callable(operation) and not asyncio.iscoroutinefunction(operation):
                return loop.run_until_complete(
                    loop.run_in_executor(None, sync_wrapper)
                )

            # If it's an async operation, run it with timeout
            async def async_wrapper():
                try:
                    if asyncio.iscoroutinefunction(operation):
                        return await asyncio.wait_for(operation(), timeout=timeout)
                    elif asyncio.iscoroutine(operation):
                        return await asyncio.wait_for(operation, timeout=timeout)
                    else:
                        raise TypeError(f"Unsupported operation type: {type(operation)}")
                except asyncio.TimeoutError:
                    self.logger.error(f"Operation timed out after {timeout}s")
                    return False
                except Exception as e:
                    if not silence_errors:
                        self.logger.error(f"Async operation failed: {str(e)}")
                    return False

            if asyncio.iscoroutinefunction(operation) or asyncio.iscoroutine(operation):
                return loop.run_until_complete(async_wrapper())

            raise TypeError(f"Unsupported operation type: {type(operation)}")

        except Exception as e:
            if not silence_errors:
                self.logger.error(f"Operation execution failed: {str(e)}")
            return False

    async def _get_profiles(self) -> Optional[list]:
        """Get list of available OBS profiles with retry"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                request = requests.GetProfileList()
                response = await asyncio.wait_for(
                    asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self.ws.call(request)
                    ),
                    timeout=self.ws_timeout
                )
                
                # Use new processor with expected fields
                result = self._process_response(response, {'profiles': list})
                if 'profiles' in result:
                    return result['profiles']

                self.logger.warning(f"Attempt {attempt + 1}: Could not find profiles in response")
                await asyncio.sleep(0.5)

            except Exception as e:
                self.logger.error(f"Attempt {attempt + 1}: Error getting profiles: {e}")
                await asyncio.sleep(0.5)

        return None

    async def async_set_profile(self, profile_name: str) -> bool:
        """Async version of set profile"""
        try:
            # Get profiles with retry
            profiles = await self._get_profiles()
            if not profiles:
                self.logger.error("Failed to get profile list after retries")
                return False
            
            self.logger.debug(f"Available profiles: {profiles}")
            
            if profile_name not in profiles:
                self.logger.error(f"Profile '{profile_name}' not found in available profiles: {profiles}")
                return False
            
            # Set profile directly and trust the response
            try:
                request = requests.SetCurrentProfile(profileName=profile_name)
                self.logger.debug(f"Sending profile change request for '{profile_name}'...")
                
                response = await asyncio.get_event_loop().run_in_executor(None, self.ws.call, request)
                self.logger.debug(f"Profile change raw response: {response}")
                
                if response:
                    self.logger.info(f"Successfully set profile to '{profile_name}'")
                    return True
                    
                self.logger.error(f"Failed to set profile: {response}")
                return False
                
            except Exception as e:
                self.logger.error(f"Error during profile change: {e}")
                return False
                
        except Exception as e:
            self.logger.error(f"Profile set error: {e}")
            return False

    def set_profile(self, profile_name: str) -> bool:
        """Synchronous set profile using current loop with better error handling"""
        if not self.ws or not self.connected:
            self.logger.error("Not connected to OBS")
            return False

        try:
            loop = self._get_loop()
            return loop.run_until_complete(
                asyncio.wait_for(
                    self.async_set_profile(profile_name),
                    timeout=self.operation_timeout
                )
            )
        except asyncio.TimeoutError:
            self.logger.error(f"Profile set operation timed out after {self.operation_timeout}s")
            return False
        except Exception as e:
            self.logger.error(f"Failed to set profile: {e}")
            return False

    def start_recording(self) -> bool:
        """Start OBS recording"""
        def _start():
            if not self.ws:
                return False
            try:
                request = requests.StartRecord()
                response = self.ws.call(request)
                status = getattr(response, 'status', None)
                self.logger.debug(f"Start recording response status: {status}")
                return status == 'ok'
            except Exception as e:
                self.logger.error(f"Start recording failed: {e}")
                return False

        return self._run_operation(_start, timeout=5.0, silence_errors=True)

    def stop_recording(self) -> bool:
        """Stop OBS recording"""
        def _stop():
            if not self.ws:
                return False
            try:
                request = requests.StopRecord()
                response = self.ws.call(request)
                status = getattr(response, 'status', None)
                self.logger.debug(f"Stop recording response status: {status}")
                return status == 'ok'
            except Exception as e:
                self.logger.error(f"Stop recording failed: {e}")
                return False

        return self._run_operation(_stop, timeout=5.0, silence_errors=True)

    def get_last_recording_path(self) -> Optional[str]:
        """Get the path of the last recording"""
        def _get_path():
            try:
                request = requests.GetRecordingFolder()
                response = self.ws.call(request)
                if response and hasattr(response, 'datain'):
                    return response.datain.get('recFolder')
                return None
            except Exception as e:
                self.logger.error(f"Get recording path failed: {e}")
                return None
            
        # Pass regular function since ws.call is synchronous
        return self._run_operation(_get_path, timeout=5.0, silence_errors=True)

    def modify_last_recording(self, new_path: str) -> bool:
        """Rename/move the last recording"""
        def _modify():
            try:
                request = requests.SetRecordingFolder(recFolder=os.path.dirname(new_path))
                response = self.ws.call(request)
                return getattr(response, 'status', None) == 'ok'
            except Exception as e:
                self.logger.error(f"Modify recording failed: {e}")
                return False
            
        # Pass regular function since ws.call is synchronous
        return self._run_operation(_modify, timeout=5.0, silence_errors=True)

    def get_recording_status(self) -> Dict[str, Any]:
        """Get recording status with improved error handling"""
        async def _get_status():
            try:
                response = await self._make_request('GetRecordStatus')
                if response is None:
                    self.logger.warning("Could not get recording status, returning default values")
                    return {
                        "isRecording": False,
                        "recordingPaused": False,
                        "recordingTimecode": "",
                        "recordingBytes": 0
                    }
                
                # Define expected fields and types
                expected_fields = {
                    'outputActive': bool,
                    'outputPaused': bool,
                    'outputTimecode': str,
                    'outputBytes': (int, float)  # Accept either type
                }
                
                # Process response with validation
                result = self._process_response(response, expected_fields)
                
                # Map to our standard format
                return {
                    "isRecording": result.get('outputActive', False),
                    "recordingPaused": result.get('outputPaused', False),
                    "recordingTimecode": result.get('outputTimecode', ""),
                    "recordingBytes": result.get('outputBytes', 0)
                }

            except Exception as e:
                self.logger.error(f"Error getting recording status: {e}")
                return {
                    "isRecording": False,
                    "recordingPaused": False,
                    "recordingTimecode": "",
                    "recordingBytes": 0
                }

        try:
            return self._run_operation(_get_status())
        except Exception as e:
            self.logger.error(f"Failed to get recording status: {e}")
            return {
                "isRecording": False,
                "recordingPaused": False,
                "recordingTimecode": "",
                "recordingBytes": 0
            }

    def get_last_recording_path(self) -> Optional[str]:
        """Return the path of the last recording"""
        return self.last_recording_path

    def modify_last_recording(self, new_path: str) -> bool:
        """Move or rename the last recording to a new path"""
        if not self.last_recording_path:
            self.logger.error(
                f"No recording path available\n"
                f"OBS connection status:\n{self._get_connection_info()}"
            )
            return False

        try:
            # Wait for potential file operations to complete
            time.sleep(1)
            
            if not os.path.exists(self.last_recording_path):
                self.logger.error(f"Source file not found: {self.last_recording_path}")
                return False

            if not os.access(self.last_recording_path, os.R_OK):
                self.logger.error(f"Source file not readable: {self.last_recording_path}")
                return False

            # Ensure the new path has the same extension as the original file
            orig_ext = os.path.splitext(self.last_recording_path)[1]
            new_ext = os.path.splitext(new_path)[1]
            if not new_ext:
                new_path = f"{new_path}{orig_ext}"
            elif new_ext.lower() != orig_ext.lower():
                self.logger.warning(f"Keeping original extension: {orig_ext}")
                new_path = f"{os.path.splitext(new_path)[0]}{orig_ext}"

            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(os.path.abspath(new_path)), exist_ok=True)

            # Check if target path is writable
            target_dir = os.path.dirname(os.path.abspath(new_path))
            if not os.access(target_dir, os.W_OK):
                self.logger.error(f"Target directory not writable: {target_dir}")
                return False

            # If target file exists, remove it first
            if os.path.exists(new_path):
                os.remove(new_path)

            # Move the file
            shutil.move(self.last_recording_path, new_path)
            
            # Verify the move was successful
            if os.path.exists(new_path):
                self.last_recording_path = new_path
                self.logger.info(f"Recording successfully moved to: {new_path}")
                return True
            else:
                self.logger.error("File move appeared to succeed but target file not found")
                return False

        except PermissionError as e:
            self.logger.error(f"Permission error: {str(e)}")
            return False
        except OSError as e:
            self.logger.error(f"OS error while moving file: {str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to modify recording path: {str(e)}")
            return False

    def _cleanup_tasks(self):
        """Clean up pending tasks"""
        try:
            loop = self._get_loop()
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            if pending:
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception as e:
            self.logger.error(f"Task cleanup error: {e}")

    def _cleanup_loop(self):
        """Clean up event loop"""
        try:
            if self.current_loop and self.current_loop.is_running():
                self.current_loop.stop()
            if self.current_loop and not self.current_loop.is_closed():
                self.current_loop.close()
        except Exception as e:
            self.logger.error(f"Loop cleanup error: {e}")

    def _force_cleanup(self):
        """Force cleanup of resources with improved task handling"""
        try:
            if self._main_loop:
                # Cancel any pending tasks first
                try:
                    loop = self._main_loop
                    if not loop.is_closed():
                        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                        if pending:
                            self.logger.debug(f"Cleaning up {len(pending)} remaining tasks...")
                            for task in pending:
                                task.cancel()
                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception as e:
                    self.logger.error(f"Error cleaning up tasks: {e}")

                # Now close the loop
                if not self._main_loop.is_closed():
                    self._main_loop.close()
        except Exception as e:
            self.logger.error(f"Loop cleanup error: {e}")
        finally:
            self.ws = None
            self.connected = False
            self.thread_pool = None
            self._main_loop = None
            self._loop_owner = None

    def disconnect(self):
        """Clean disconnect with proper loop and timeout handling"""
        if not self.ws:
            return

        try:
            loop = asyncio.get_event_loop()
            
            # If we're already in an event loop, use run_coroutine_threadsafe
            if loop.is_running():
                self.logger.debug("Event loop is running, using thread pool for disconnect")
                future = asyncio.run_coroutine_threadsafe(self._disconnect(), loop)
                try:
                    # Use the longer disconnect timeout
                    future.result(timeout=self.disconnect_timeout)
                except concurrent.futures.TimeoutError:
                    self.logger.error(f"Disconnect timed out after {self.disconnect_timeout}s")
                except Exception as e:
                    self.logger.error(f"Disconnect error: {e}")
            else:
                # If no loop is running, use run_until_complete
                try:
                    loop.run_until_complete(
                        asyncio.wait_for(self._disconnect(), timeout=self.disconnect_timeout)
                    )
                except asyncio.TimeoutError:
                    self.logger.error(f"Disconnect timed out after {self.disconnect_timeout}s")
                except Exception as e:
                    self.logger.error(f"Disconnect error: {e}")
            
        except Exception as e:
            self.logger.error(f"Error during disconnect cleanup: {e}")
        finally:
            # Clean up thread pool first
            if self.thread_pool:
                try:
                    # Cancel any pending futures
                    self.thread_pool.shutdown(wait=True)
                except Exception as e:
                    self.logger.error(f"Error shutting down thread pool: {e}")
                finally:
                    self.thread_pool = None
            
            # Force cleanup as last resort
            self._force_cleanup()

    def _get_loop(self):
        """Get or create event loop for current thread"""
        current_thread = threading.current_thread()
        if self._main_loop is None or self._loop_owner != current_thread:
            self._main_loop = asyncio.new_event_loop()
            self._loop_owner = current_thread
            asyncio.set_event_loop(self._main_loop)
        return self._main_loop

    def is_recording(self) -> bool:
        """Check if OBS is currently recording"""
        try:
            if not self.ws or not self.ws.ws.connected:
                return False
                
            response = self.ws.call(requests.GetRecordStatus())
            return getattr(response, 'datain', {}).get('outputActive', False)
        except Exception as e:
            self.logger.error(f"Error checking recording status: {e}")
            return False

if __name__ == "__main__":
    # Example usage
    client = OBSClient(password="EugUfLDwG9cTS01p", log_level='INFO')
    if client.connect():
        try:
            # Change profile
            client.set_profile("Valorant")
            
            # Start recording
            client.start_recording()
            
            # Get recording status
            
            time.sleep(1)
            status = client.get_recording_status()
            print(f"Recording status: {status}")
            
            # Wait for 5 seconds
            time.sleep(5)
            
            # Stop recording
            client.stop_recording()
            
            # Add longer wait time after stopping recording
            time.sleep(2)  # Wait for file to be fully written
            
            # Get the last recording path
            last_path = client.get_last_recording_path()
            print(f"Last recording path: {last_path}")
            
            time.sleep(1)
            
            # Modify the last recording path
            if last_path:
                new_path = os.path.join(os.path.dirname(last_path), "renamed_recording")  # Extension will be added automatically
                client.modify_last_recording(new_path)
            
        finally:
            client.disconnect()