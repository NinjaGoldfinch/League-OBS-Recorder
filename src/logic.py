import asyncio
from colorama import Fore, Style, init
from typing import Dict, Tuple, Union, List, Optional, Callable
from logger import setup_logger
from cache import LCUEventCache
from obs_client import OBSClient
import os

# Initialize colorama for Windows
init()

class LCUGameLogic:
    def __init__(self, cache: LCUEventCache, log_level: str = 'INFO', obs_password: str = None):
        self.cache = cache
        self.in_game: bool = False
        self.is_recording: bool = False  # Add recording state tracker
        self.queue_status: str = 'HomeScreen'
        self.handlers: Dict[str, Callable] = {
            'lol-gameflow_v1_session': self._handle_gameflow
        }
        
        # Initialize logger with specified level
        self.logger = setup_logger('LCUGameLogic', 'lcu_game_logic.log', log_level)
        
        # Define game states we don't want to track
        self.ignored_queue_types: set = {
            'TUTORIAL_MODULE_1', 
            'TUTORIAL_MODULE_2', 
            'TUTORIAL_MODULE_3', 
            'PRACTICE_TOOL'
        }
        
        self.logger.info('LCUGameLogic initialized')
        
        # Initialize OBS client
        self.obs = None
        self.obs_init_task = None
        if obs_password:
            try:
                self.obs = OBSClient(password=obs_password, timeout=3.0, log_level=log_level)
                # Don't create task here, just store parameters
                self.obs_password = obs_password
                self.log_level = log_level
            except Exception as e:
                self.logger.error(f'Error creating OBS client: {e}')
                self.obs = None

        self.is_debug = log_level.upper() == 'DEBUG'
        self.obs_ready = asyncio.Event()  # Add this to track OBS initialization

    async def initialize(self):
        """Initialize async components"""
        if self.obs:
            try:
                self.logger.debug("Starting OBS initialization in background...")
                # Create a task for OBS initialization
                init_task = asyncio.create_task(self._init_obs_async())
                # Continue initialization without waiting
                init_task.add_done_callback(lambda _: self.obs_ready.set())
            except Exception as e:
                self.logger.error(f'Error during OBS initialization: {e}')
                self.obs = None

    async def _init_obs_async(self):
        """Asynchronous OBS initialization"""
        try:
            if await self.obs.async_connect():
                self.logger.info('OBS client connected successfully')
                if await self.obs.async_set_profile("League of Legends"):
                    self.logger.info('OBS profile set successfully')
                else:
                    self.logger.warning('Failed to set OBS profile')
                    self.obs = None
        except Exception as e:
            self.logger.error(f'OBS initialization error: {e}')
            self.obs = None
        finally:
            # Always set the event, even on failure
            self.obs_ready.set()

    def _check_if_dodge(self, data: Dict) -> Tuple[Union[bool, str], str, List]:
        """Check if a game dodge occurred"""
        dodge = data.get('gameDodge', {})
        
        if not dodge:
            return False, '', []
        
        if (dodge.get('phase', '') in ['ReadyCheck', 'ChampSelect'] and 
            dodge.get('state', 'Invalid') == 'PartyDodged'):
            return True, dodge.get('phase', ''), dodge.get('dodgeIds', [])
        
        return 'Unknown', dodge.get('phase', ''), dodge.get('dodgeIds', [])
    
    def _get_queue_type(self, data: Dict) -> str:
        """Extract queue type from game data"""
        return data.get('gameData', {}).get('queue', {}).get('type', '')
    
    def _is_data_different(self, new_data: Optional[Dict], cached_data: Optional[Dict]) -> bool:
        """Check if new data differs from cached data"""
        if cached_data is None:
            return True
        return new_data != cached_data

    async def handle_events(self, event_uri: str, data: Dict) -> None:
        """Route events to appropriate handlers"""
        if self.is_debug:
            self.logger.debug(f'Event received: {event_uri}')
        if event_uri in self.handlers:
            await self.handlers[event_uri](data)

    async def _handle_recording_start(self) -> bool:
        """Handle starting OBS recording"""
        if not self.obs:
            return False
            
        try:
            await asyncio.wait_for(self.obs_ready.wait(), timeout=5.0)
            
            self.logger.info(f"{Fore.GREEN}Starting game recording{Style.RESET_ALL}")
            self.obs.start_recording()
            
            # Give OBS a moment to start recording
            await asyncio.sleep(1)
            
            # Check if recording actually started
            if self.obs.is_recording():
                self.is_recording = True  # Update recording state
                self.logger.info("Recording started successfully")
                return True
            else:
                self.logger.error("Failed to start recording")
                return False
                
        except Exception as e:
            self.logger.error(f"Error starting recording: {e}")
            return False

    async def _handle_recording_stop(self, queue_type: str, json_data: Dict) -> bool:
        """Handle stopping OBS recording and file renaming"""
        if not self.obs:
            return False
            
        try:
            if not self.obs.is_recording():
                self.logger.warning("Attempted to stop recording but OBS was not recording")
                self.is_recording = False  # Ensure state is synced
                return True

            self.logger.info(f"{Fore.YELLOW}Stopping game recording{Style.RESET_ALL}")
            self.obs.stop_recording()
            
            # Give OBS a moment to stop recording
            await asyncio.sleep(2)
            
            # Verify recording stopped
            if self.obs.is_recording():
                self.logger.error("Failed to stop recording")
                return False

            self.is_recording = False  # Update recording state
            # Rest of the rename logic
            game_id = json_data.get('gameData', {}).get('gameId', 'unknown')
            if self.is_debug:
                self.logger.debug(f"Processing recording for game: {game_id}")

            last_path = self.obs.get_last_recording_path()
            if last_path:
                queue_name = queue_type.lower().replace('_', '')
                new_name = f"league_{queue_name}_game_{game_id}"
                new_path = os.path.join(os.path.dirname(last_path), new_name)
                if not self.obs.modify_last_recording(new_path):
                    self.logger.error("Failed to rename recording")
                    return False
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            return False

    async def _handle_gameflow(self, data: Dict) -> None:
        """Handle gameflow state changes"""
        if not self._is_data_different(data, self.cache.gameflow_session):
            return

        self.cache.gameflow_session = data
        json_data = data.get('data', {})
        current_phase = json_data.get('phase', '')
        queue_type = self._get_queue_type(json_data)

        # Update state
        self.queue_status = current_phase
        self.game_dodge = self._check_if_dodge(json_data)
        
        # Handle different queue types
        if queue_type in self.ignored_queue_types:
            if not self.is_debug:
                self.logger.debug("Ignoring game type (not in debug mode)")
                return
            else:
                self.logger.debug("DEBUG MODE: Recording ignored queue type")
        
        # Log phase changes and update game state
        if current_phase != self.cache.previous_phase:
            prev_phase = self.cache.previous_phase or 'None'
            self.logger.info(
                f"Matchmaking change: {Fore.CYAN}{prev_phase}{Style.RESET_ALL} -> "
                f"{Fore.CYAN}{current_phase}{Style.RESET_ALL}"
            )
            
            # Update in_game state based on phase
            if current_phase in ['ReadyCheck', 'ChampSelect']:
                self.in_game = True
            elif current_phase in ['EndOfGame', 'GameComplete', 'None', 'Lobby']:
                self.in_game = False
            elif current_phase == 'TerminatedInError':
                if queue_type == 'PRACTICE_TOOL':
                    self.logger.debug("Ignoring termination error for Practice Tool")
                    self.in_game = False
            
            # Handle OBS recording based on game phase
            if self.obs:
                if current_phase in ['ReadyCheck', 'ChampSelect'] and not self.is_recording:
                    self.logger.info(f"Starting recording during {current_phase}")
                    await self._handle_recording_start()
                
                # Add new condition to stop recording when transitioning from ReadyCheck/ChampSelect to None
                elif (current_phase == 'None' and 
                      prev_phase in ['ReadyCheck', 'ChampSelect'] and 
                      self.is_recording):
                    self.logger.warning(f"Game cancelled during {prev_phase}, stopping recording")
                    await self._handle_recording_stop(queue_type, json_data)
                
                elif current_phase in ['EndOfGame', 'GameComplete'] and self.is_recording:
                    await self._handle_recording_stop(queue_type, json_data)
                        
                elif current_phase == 'TerminatedInError' and self.is_recording:  # Use tracked state
                    if queue_type == 'PRACTICE_TOOL':
                        self.logger.debug("Ignoring termination error for Practice Tool")
                    else:
                        self.logger.warning("Game terminated in error")
                        await self._handle_recording_stop(queue_type, json_data)
                            
                elif (current_phase in ['Lobby', 'Matchmaking'] 
                      and self.game_dodge[0] is True 
                      and self.is_recording):  # Use tracked state
                    self.logger.warning(f"Game dodged in phase: {self.game_dodge[1]}, stopping recording")
                    await self._handle_recording_stop(queue_type, json_data)
        
        # Store current phase for next comparison
        self.cache.previous_phase = current_phase
        
        # Only log essential debug info
        if self.is_debug:
            self.logger.debug(
                f"Game State Update:\n"
                f"  Phase: {current_phase}\n"
                f"  Queue: {queue_type}\n"
                f"  In Game: {self.in_game}\n"
                f"  Queue Status: {self.queue_status}\n"
                f"  Game Dodge: {self.game_dodge}\n"
                f"  Recording: {self.is_recording}"  # Use tracked state instead of querying OBS
            )

    def cleanup(self):
        """Cleanup resources"""
        try:
            if self.obs:
                if self.is_recording:  # Use tracked state
                    try:
                        self.obs.stop_recording()
                    except:
                        self.logger.debug("Failed to stop recording during cleanup")
                        
                # Use synchronous disconnect
                try:
                    self.obs.disconnect()
                except:
                    self.logger.debug("Failed to shutdown OBS during cleanup")
                    
                self.obs = None
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")