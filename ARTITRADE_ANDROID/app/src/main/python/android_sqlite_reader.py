"""Read-only SQLite access for Android research and historical ledger views.

The bundled EXE bridge exposes trading APIs but has no ``_db_rows`` helper.
This module deliberately never creates, migrates, or modifies an account DB.
"""
from pathlib import Path
import sqlite3


def db_rows(db, query, parameters=()):
    """Return dictionaries from a read-only SELECT; absent old tables return [].

    Only the caller's already installed files can be read. A missing column
    raises an error so the caller cannot silently display incomplete records.
    """
    if not isinstance(query, str) or not query.lstrip().upper().startswith('SELECT'):
        raise ValueError('ARTITRADE research reader accepts SELECT only')
    source = Path(db)
    if not source.is_file():
        return []
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=4) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        try:
            return [dict(row) for row in conn.execute(query, parameters)]
        except sqlite3.OperationalError as error:
            if 'no such table' in str(error).lower():
                return []
            raise
