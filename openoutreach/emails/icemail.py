# openoutreach/emails/icemail.py
"""Parse IceMail's 'Export Mailboxes' rows, pasted from the exported XLS.

The export is a flat table — ``Email | Password | Recovery Email | Service
Provider | Admin`` — copied out of the sheet as tab-separated rows. We read the
``Email`` and ``Password`` columns by name; host/port are the Google-box
constants on the Mailbox model, so nothing else is needed.
"""
from __future__ import annotations

import csv


def parse_mailboxes(pasted: str) -> list[tuple[str, str]]:
    """Return ``(email, password)`` pairs from a pasted Export-Mailboxes block.

    The paste must include the header row. Raises ``ValueError`` if the Email and
    Password columns aren't present.
    """
    lines = [ln for ln in pasted.splitlines() if ln.strip()]
    if not lines:
        return []

    delimiter = "\t" if "\t" in lines[0] else ","
    rows = list(csv.reader(lines, delimiter=delimiter))
    header = [c.strip().lower() for c in rows[0]]
    try:
        i_email, i_pw = header.index("email"), header.index("password")
    except ValueError:
        raise ValueError(
            "Paste must include the header row (Email, Password, …) from the "
            "IceMail Export Mailboxes sheet."
        )

    pairs: list[tuple[str, str]] = []
    for row in rows[1:]:
        if len(row) <= max(i_email, i_pw):
            continue
        email, password = row[i_email].strip(), row[i_pw].strip()
        if email and password:
            pairs.append((email, password))
    return pairs
