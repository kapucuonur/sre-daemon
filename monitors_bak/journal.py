import subprocess

class JournalWatcher:
    def __init__(self):
        self.name = "JournalWatcher"

    def get_recent_errors(self):
        result = subprocess.run(['journalctl', '-n', '20', '--no-pager'], capture_output=True, text=True)
        return [line for line in result.stdout.split('\n') if "error" in line.lower()]
