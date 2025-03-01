import winreg
import json
from pathlib import Path
from typing import Optional
from logger import setup_logger

class LeaguePathFinder:
    def __init__(self):
        self.logger = setup_logger('LeaguePathFinder')
        
    def _get_riot_client_path(self) -> Optional[Path]:
        """Get Riot Client installation path from Windows Registry."""
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Riot Games, Inc\Riot Client") as key:
                install_location = winreg.QueryValueEx(key, "InstallLocation")[0]
                return Path(install_location)
        except WindowsError as e:
            self.logger.debug(f"Failed to find Riot Client in registry: {e}")
            return None

    def _get_league_path_from_riot_settings(self, riot_client_path: Path) -> Optional[Path]:
        """Get League of Legends path from Riot Client settings."""
        try:
            settings_path = riot_client_path / "Config" / "global.json"
            if not settings_path.exists():
                return None

            with open(settings_path, 'r') as f:
                settings = json.load(f)
                
            install_dir = settings.get("install_dir", {}).get("league_of_legends")
            if install_dir:
                return Path(install_dir)
        except Exception as e:
            self.logger.debug(f"Failed to parse Riot Client settings: {e}")
        return None

    def find_league_path(self) -> Optional[Path]:
        """Find League of Legends installation path."""
        # Common installation paths
        common_paths = [
            Path("C:/Riot Games/League of Legends"),
            Path("D:/Riot Games/League of Legends"),
            Path("C:/Program Files/Riot Games/League of Legends"),
            Path("C:/Program Files (x86)/Riot Games/League of Legends"),
        ]

        # Try getting path from Riot Client first
        riot_client_path = self._get_riot_client_path()
        if riot_client_path:
            league_path = self._get_league_path_from_riot_settings(riot_client_path)
            if league_path and league_path.exists():
                self.logger.info(f"Found League path from Riot Client settings: {league_path}")
                return league_path

        # Try common paths
        for path in common_paths:
            if path.exists():
                self.logger.info(f"Found League path in common location: {path}")
                return path

        self.logger.warning("Could not find League of Legends installation path")
        return None
