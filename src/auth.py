import psutil
import base64
import aiofiles
from pathlib import Path
from typing import Optional, Tuple
from logger import setup_logger
from path_finder import LeaguePathFinder

class LCUAuth:
    def __init__(self, league_path: str = None):
        self.logger = setup_logger('LCUAuth')
        self.port: Optional[str] = None
        self.token: Optional[str] = None
        self.protocol: str = 'https'
        self.base_url: Optional[str] = None
        if league_path is None:
            path_finder = LeaguePathFinder()
            found_path = path_finder.find_league_path()
            if found_path:
                self.league_path = found_path
            else:
                self.league_path = Path("C:/Riot Games/League of Legends")  # fallback
        else:
            self.league_path = Path(league_path)
        self.lockfile_path = self.league_path / "lockfile"

    async def get_lockfile_auth(self) -> Tuple[Optional[str], Optional[str]]:
        """Get authentication details from lockfile."""
        try:
            if not self.lockfile_path.exists():
                self.logger.warning("Lockfile not found")
                return None, None

            async with aiofiles.open(self.lockfile_path, mode='r') as f:
                data = await f.read()
                
                process_name, pid, port, password, protocol = data.split(':')
                auth_token = base64.b64encode(f"riot:{password}".encode('utf-8')).decode('utf-8')
                
                self.port = port
                self.token = auth_token
                self.base_url = f'{self.protocol}://127.0.0.1:{self.port}'
                self.logger.info(f"Successfully obtained LCU authentication details from lockfile (PID: {pid})")
                return port, auth_token

        except Exception as e:
            self.logger.error(f"Error getting authentication details from lockfile: {e}")
            return None, None

    async def get_auth(self) -> Tuple[Optional[str], Optional[str]]:
        """Get authentication details, trying process first then lockfile."""
        try:
            # Try process-based authentication first
            for process in psutil.process_iter(['name', 'cmdline']):
                if process.info['name'] == 'LeagueClientUx.exe':
                    args = process.info['cmdline']
                    auth_token = next((arg.split('=')[1] for arg in args if arg.startswith('--remoting-auth-token=')), None)
                    port = next((arg.split('=')[1] for arg in args if arg.startswith('--app-port=')), None)

                    if auth_token and port:
                        self.port = port
                        self.token = base64.b64encode(f'riot:{auth_token}'.encode('utf-8')).decode('utf-8')
                        self.base_url = f'{self.protocol}://127.0.0.1:{self.port}'
                        self.logger.info(f"Successfully obtained LCU authentication details from process (PID: {process.pid})")
                        return port, self.token

            # If process auth fails, try lockfile auth
            self.logger.info("Process-based auth failed, trying lockfile...")
            return await self.get_lockfile_auth()

        except Exception as e:
            self.logger.error(f"Error getting authentication details: {e}")
            return None, None

