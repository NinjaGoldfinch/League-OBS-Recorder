import logging
from colorama import Fore, Style, init
from typing import Optional, Union

# Initialize colorama for Windows
init()

# Update log level mapping to use uppercase keys
LOG_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}

class CustomFormatter(logging.Formatter):
    """Custom formatter with colors and timestamps"""
    
    FORMATS = {
        logging.DEBUG: Fore.CYAN + "[%(asctime)s] %(levelname)s: %(message)s" + Style.RESET_ALL,
        logging.INFO: Fore.GREEN + "[%(asctime)s] %(levelname)s: %(message)s" + Style.RESET_ALL,
        logging.WARNING: Fore.YELLOW + "[%(asctime)s] %(levelname)s: %(message)s" + Style.RESET_ALL,
        logging.ERROR: Fore.RED + "[%(asctime)s] %(levelname)s: %(message)s" + Style.RESET_ALL,
        logging.CRITICAL: Fore.RED + Style.BRIGHT + "[%(asctime)s] %(levelname)s: %(message)s" + Style.RESET_ALL
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt='%Y-%m-%d %H:%M:%S')
        return formatter.format(record)

def setup_logger(name: str, log_file: Optional[str] = None, log_level: Union[str, int] = 'INFO') -> logging.Logger:
    """Set up and configure a logger with optional file output."""
    if isinstance(log_level, str):
        level = LOG_LEVELS.get(log_level.upper(), logging.INFO)
    else:
        level = log_level

    # Get or create logger for the specific name
    logger = logging.getLogger(name)
    logger.handlers = []  # Clear any existing handlers
    logger.setLevel(level)  # Set specific level for this logger
    logger.propagate = False  # Prevent propagation to root
    
    # Add console handler with custom formatter
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(CustomFormatter())
    console_handler.setLevel(level)
    logger.addHandler(console_handler)
    
    # Add file handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(
            logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
        )
        file_handler.setLevel(level)
        logger.addHandler(file_handler)
    
    return logger