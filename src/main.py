from api import LCUWebSocket
from logic import LCUGameLogic
from cache import LCUEventCache
from asyncio import run as asyncio_run
from logger import setup_logger
from config import Config
import asyncio
import argparse
import sys

def parse_args():
    parser = argparse.ArgumentParser(description='LCU API Testing Tool')
    parser.add_argument(
        '--config',
        default='config.toml',
        help='Path to config file (default: config.toml)'
    )
    return parser.parse_args()

async def initialize_components(args):
    """Initialize all components asynchronously"""
    # Load configuration
    config = Config.load(args.config)
    
    main_logger = setup_logger('LCUMain', 
                             config.logging.file_path, 
                             config.logging.level)
    main_logger.info(f"Starting application with config from: {args.config}")
    
    cache = LCUEventCache()
    game_logic = LCUGameLogic(cache, 
                             log_level=config.logging.level, 
                             obs_password=config.obs.password)
    client = LCUWebSocket(log_level=config.logging.level)
    
    # Start async initialization
    await game_logic.initialize()
    
    return main_logger, client, game_logic

async def shutdown(client, game_logic):
    """Handle graceful shutdown."""
    try:
        # Cleanup OBS resources first
        if game_logic:
            game_logic.cleanup()
        
        # Cancel pending tasks
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        
        # Close client first to prevent new tasks
        if client:
            await client.close()
        
        if tasks:
            print(f"Cleaning up {len(tasks)} remaining tasks...")
            sys.stdout.flush()
            for task in tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Small delay to allow messages to be processed
        await asyncio.sleep(0.5)
        print("\nGoodbye!")
        sys.stdout.flush()
        
    except Exception as e:
        print(f"\nError during shutdown: {e}")
        sys.stdout.flush()

async def main():
    args = parse_args()
    
    try:
        main_logger, client, game_logic = await initialize_components(args)
        
        main_logger.info("Starting application...")
        async with client.connection() as websocket:
            main_logger.info("Starting LCU WebSocket client...")
            
            # Subscribe to events
            main_logger.info("Subscribing to gameflow events...")
            await websocket.subscribe('lol-gameflow_v1_session', game_logic.handle_events)
            
            # Start listening for events
            main_logger.info("Starting event listener (Press Ctrl+C to exit)...")
            await websocket.listen()
            
    except KeyboardInterrupt:
        print("\nShutting down...")
        sys.stdout.flush()
    except ConnectionError as e:
        main_logger.error(f"Connection error: {e}")
    except Exception as e:
        print(f"\nError: {e}")
        sys.stdout.flush()
        raise
    finally:
        await shutdown(client, game_logic)

if __name__ == "__main__":
    try:
        asyncio_run(main())
    except KeyboardInterrupt:
        pass  # Already handled in main()