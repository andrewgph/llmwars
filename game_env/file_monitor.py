import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import logging

class FileChangeHandler(FileSystemEventHandler):
    def __init__(self):
        self.logger = logging.getLogger('file_monitor')
        handler = logging.FileHandler(os.path.join(os.environ.get('SHARED_LOGS'), 'file_changes.log'))
        handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

    def on_any_event(self, event):
        if event.is_directory:
            return
        
        self.logger.info(f"Event type: {event.event_type}, Path: {event.src_path}")

def main():
    agent_space = os.environ.get('AGENT_SPACE')
    event_handler = FileChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, agent_space, recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    main()