import subprocess

class DockerWatcher:
    def __init__(self):
        self.name = "DockerWatcher"

    def get_container_status(self):
        result = subprocess.run(['docker', 'ps', '--format', '{{.Names}}: {{.Status}}'], capture_output=True, text=True)
        return result.stdout.strip()
