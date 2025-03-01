import tomli
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, Any
from logger import setup_logger

@dataclass
class OBSConfig:
    """OBS WebSocket configuration settings"""
    host: str = "localhost"
    port: int = 4455
    password: str = ""
    timeout: float = 3.0
    profile_name: str = "League of Legends"

@dataclass
class LoggingConfig:
    """Logging configuration settings"""
    level: str = "INFO"
    file_path: Optional[str] = None

@dataclass
class Config:
    """Main configuration container"""
    obs: OBSConfig
    logging: LoggingConfig
    
    @classmethod
    def load(cls, config_path: str = "config.toml") -> 'Config':
        """Load configuration from TOML file"""
        logger = setup_logger('Config')
        
        try:
            config_file = Path(config_path)
            if not config_file.exists():
                logger.warning(f"Config file not found at {config_path}, using defaults")
                return cls(
                    obs=OBSConfig(),
                    logging=LoggingConfig()
                )
            
            with open(config_file, "rb") as f:
                data = tomli.load(f)
            
            # Parse OBS config
            obs_data = data.get("obs", {})
            obs_config = OBSConfig(
                host=obs_data.get("host", OBSConfig.host),
                port=obs_data.get("port", OBSConfig.port),
                password=obs_data.get("password", OBSConfig.password),
                timeout=obs_data.get("timeout", OBSConfig.timeout),
                profile_name=obs_data.get("profile_name", OBSConfig.profile_name)
            )
            
            # Parse logging config
            logging_data = data.get("logging", {})
            logging_config = LoggingConfig(
                level=logging_data.get("level", LoggingConfig.level),
                file_path=logging_data.get("file_path", LoggingConfig.file_path)
            )
            
            return cls(
                obs=obs_config,
                logging=logging_config
            )
            
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            logger.info("Using default configuration")
            return cls(
                obs=OBSConfig(),
                logging=LoggingConfig()
            )