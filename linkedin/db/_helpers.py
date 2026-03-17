import uuid


def _make_ticket() -> str:
    """Generate a unique 16-char ticket for a Deal."""
    return uuid.uuid4().hex[:16]
