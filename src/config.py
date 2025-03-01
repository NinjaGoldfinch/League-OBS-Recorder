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
    level: str = "DEBUG"
    file_path: Optional[str] = None

@dataclass
class Config:
    obs: OBSConfig
    logging: LoggingConfig
    
    @classmethod
    def load(cls, config_path: str = "config.local.toml") -> 'Config':
        """
        Load configuration from TOML file with fallback to template
        """
        # Start with DEBUG level to catch all initial loading logs
        logger = setup_logger('Config', None, 'DEBUG')
        
        # Try loading local config first
        local_config = Path(config_path).resolve()
        template_config = Path("config.template.toml").resolve()
        
        logger.debug(f"Checking for local config at: {local_config}")
        logger.debug(f"Template config path: {template_config}")
        
        config_file = None
        if local_config.exists():
            config_file = local_config
            logger.info(f"Using local config: {local_config}")
        elif template_config.exists():
            config_file = template_config
            logger.warning(f"Local config not found, using template: {template_config}")
        else:
            logger.warning("No config files found, using defaults")
            return cls(
                obs=OBSConfig(),
                logging=LoggingConfig()
            )
        
        try:
            with open(config_file, "rb") as f:
                data = tomli.load(f)
                logger.debug(f"Successfully loaded TOML from {config_file}")
            
            # Parse logging config first to update logger
            logging_data = data.get("logging", {})
            logger.debug("Parsing logging config settings:")
            logging_config = LoggingConfig(
                level=logging_data.get("level", LoggingConfig.level),
                file_path=logging_data.get("file_path", LoggingConfig.file_path)
            )
            
            # Update logger with configured level
            logger = setup_logger('Config', logging_config.file_path, logging_config.level)
            logger.debug(f"Updated logger level to: {logging_config.level}")
            logger.debug(f"Log File Path: {logging_config.file_path}")
            
            # Parse OBS config
            obs_data = data.get("obs", {})
            logger.debug("Parsing OBS config settings:")
            obs_config = OBSConfig(
                host=obs_data.get("host", OBSConfig.host),
                port=obs_data.get("port", OBSConfig.port),
                password=obs_data.get("password"),
                timeout=obs_data.get("timeout", OBSConfig.timeout),
                profile_name=obs_data.get("profile_name", OBSConfig.profile_name)
            )
            
            # Log OBS settings with updated log level
            logger.debug(f"OBS Host: {obs_config.host}")
            logger.debug(f"OBS Port: {obs_config.port}")
            logger.debug(f"OBS Password: {'Set' if obs_config.password else 'Not Set'}")
            logger.debug(f"OBS Timeout: {obs_config.timeout}")
            logger.debug(f"OBS Profile: {obs_config.profile_name}")
            
            logger.info("Configuration loaded successfully")
            return cls(
                obs=obs_config,
                logging=logging_config
            )
            
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            logger.debug("Stack trace:", exc_info=True)
            logger.info("Using default configuration")
            return cls(
                obs=OBSConfig(),
                logging=LoggingConfig()
            )