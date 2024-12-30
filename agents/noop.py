import logging
import os
import time

def main():
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    logger.info(f"noop.py Process ID: {os.getpid()}, User ID: {os.getuid()}")

    # Wait indefinitely
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
