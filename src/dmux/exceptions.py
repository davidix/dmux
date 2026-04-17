"""Domain errors for dmux."""


class DmuxError(Exception):
    """Base error."""


class SessionNotFoundError(DmuxError):
    """No tmux session with the given name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Session not found: {name}")
        self.name = name


class WindowNotFoundError(DmuxError):
    """Window missing for the given session."""


class PaneNotFoundError(DmuxError):
    """Pane missing."""


class SnapshotNotFoundError(DmuxError):
    """No saved snapshot."""

    def __init__(self, label: str = "default") -> None:
        super().__init__(f"No snapshot found for label: {label}")


class SessionExistsError(DmuxError):
    """Session name already in use."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Session already exists: {name}")
        self.name = name


class PluginManagerError(DmuxError):
    """TPM / plugin list operation failed."""
