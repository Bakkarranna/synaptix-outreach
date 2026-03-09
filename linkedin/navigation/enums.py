from enum import Enum


class ProfileState(str, Enum):
    NEW = "new"
    READY_TO_CONNECT = "ready_to_connect"
    PENDING = "pending"
    CONNECTED = "connected"
    COMPLETED = "completed"
    FAILED = "failed"
