import time
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class GraphUpdateHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith(('.py', '.md', '.jsonl')):
            print(f"Değişiklik algılandı: {event.src_path}")
            subprocess.run(["graphify", "."])
            subprocess.run(["graphify", "cluster-only", "."])

if __name__ == "__main__":
    observer = Observer()
    observer.schedule(GraphUpdateHandler(), path='.', recursive=True)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
