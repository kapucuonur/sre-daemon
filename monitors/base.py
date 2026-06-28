import abc

class BaseWatcher(abc.ABC):
    """Base class for all system/application monitor watchers."""
    
    @abc.abstractmethod
    def start(self):
        """Start the watcher loop in a separate thread."""
        pass

    @abc.abstractmethod
    def stop(self):
        """Stop the watcher loop."""
        pass
