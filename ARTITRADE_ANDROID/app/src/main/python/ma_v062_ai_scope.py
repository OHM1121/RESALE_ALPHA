"""v062: only the newly activated MA cohort feeds MA AI/research.

All historical AI journals and all old MA trade databases are preserved
byte-for-byte. New MA-only AI writes use a separate root and source mapping.
Original/AT, GMO, CLOSE, base coin, and independent trend AI unchanged.
"""
from __future__ import annotations
from pathlib import Path

_INSTALLED=False

def ai_root(root):
    return Path(root)/"MA_EPOCH_V062_AI"

def install():
    global _INSTALLED
    if _INSTALLED:return
    import ai_strategy_analysis_v037 as original
    import ai_research_v040 as journal
    import ai_trade_lifecycle_v042 as lifecycle
    import ai_comprehensive_v052 as comprehensive
    import ma_epoch_v062 as epoch

    # A previous MA position id 1 and a new MA position id 1 must never be
    # joined simply by colliding source IDs. Read only new SQLite source.
    original.SOURCE[("stock","ma")]=(
        "stock/MA_EPOCH_V062/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
        "candidates")
    original.SOURCE[("coin","ma")]=(
        "coin/ma_epoch_v062/upbit_paper.sqlite3","candidates")

    old_capture=journal.capture_scope
    def capture(root,market,strategy,research,ledger,now=None,market_score=None):
        scoped=ai_root(root) if strategy=="ma" else root
        return old_capture(scoped,market,strategy,research,ledger,now,market_score)
    journal.capture_scope=capture

    old_stored=journal.stored
    def stored(root,market,strategy):
        return old_stored(ai_root(root) if strategy=="ma" else root,market,strategy)
    journal.stored=stored

    old_save=journal.save_report
    def save(root,market,strategy,report,when=None):
        return old_save(ai_root(root) if strategy=="ma" else root,
                        market,strategy,report,when)
    journal.save_report=save

    old_rebuild=lifecycle.rebuild
    def rebuild(root,market,strategy,*args,**kwargs):
        return old_rebuild(ai_root(root) if strategy=="ma" else root,
                           market,strategy,*args,**kwargs)
    lifecycle.rebuild=rebuild

    old_comprehensive=comprehensive.analyze
    def analyze(root,market,strategy,*args,**kwargs):
        return old_comprehensive(ai_root(root) if strategy=="ma" else root,
                                 market,strategy,*args,**kwargs)
    comprehensive.analyze=analyze

    _INSTALLED=True
