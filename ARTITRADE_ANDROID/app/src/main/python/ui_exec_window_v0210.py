"""Display-window helper: a trimmed execution list keeps the BUY rows of every visible position.

Screen copy only. SQLite, the ledger and the exports are never touched.
"""
EXTRA_CAP=1200
BUY_CAP=600


def identities(row):
    """Identities tying a fill to its position (uid first, then native position id)."""
    found=set()
    if not isinstance(row,dict):return found
    uid=str(row.get('position_uid') or '').strip()
    if uid:found.add('uid|'+uid)
    for field in ('position_id','source_position_id'):
        value=row.get(field)
        if value not in (None,'',0,'0'):
            found.add('pid|'+str(value).strip())
    return found


def linked_fills(kept,omitted):
    """Rows cut off by the newest-N window that still belong to a position inside it.

    The window kept only the newest rows, so a position sold hundreds of times lost its
    older BUY row and the phone showed sells without a buy.
    """
    wanted=set()
    for row in kept:wanted|=identities(row)
    if not wanted or not omitted:return []
    buys,others=[],[]
    for row in omitted:
        if not isinstance(row,dict) or not (identities(row)&wanted):continue
        side=str(row.get('side') or row.get('type') or '').upper()
        (buys if side.startswith('BUY') else others).append(row)
    return buys[:BUY_CAP]+others[:EXTRA_CAP]
