"""Android v0.7.4 runtime compatibility layer.

Does not alter strategy thresholds/state machines. It wires the latest extracted
GMO service into the same Kiwoom 0B/0D stream before the live-order adapter
patches the GMO paper-fill transition.
"""
from pathlib import Path
import android_live_bridge as _core

_ORIG_PATCH_GMO = _core.Runtime._patch_gmo
_ORIG_STOP = _core.Runtime.stop


def _patched_gmo(self):
    if not self.stock_runner:
        return _ORIG_PATCH_GMO(self)
    if not hasattr(self.stock_runner, 'gmo') or self.stock_runner.gmo is None:
        from d_gmo_runtime import GMOService
        gmo_root = Path(self.root) / 'stock' / 'gmo'
        gmo = GMOService(gmo_root)
        self.stock_runner.gmo = gmo

        original_tick = self.stock_runner._new_tick
        def tick_with_gmo(tick):
            try:
                gmo.on_priority_tick(tick)
                gmo.on_live_tick(tick)
            except Exception as exc:
                self._err('gmo_tick', exc)
            return original_tick(tick)
        self.stock_runner._new_tick = tick_with_gmo

        original_book = self.stock_runner.engine.book.on_message
        def book_with_gmo(message, *args, **kwargs):
            try:
                gmo.on_book_message(message, self.stock_runner.market_map)
            except Exception as exc:
                self._err('gmo_book', exc)
            return original_book(message, *args, **kwargs)
        self.stock_runner.engine.book.on_message = book_with_gmo

        gmo.start(lambda: self.stock_runner.api,
                  lambda: dict(self.stock_runner.market_map))
    return _ORIG_PATCH_GMO(self)


def _patched_stop(self):
    try:
        gmo = getattr(self.stock_runner, 'gmo', None) if self.stock_runner else None
        if gmo is not None:
            gmo.stop()
    except Exception as exc:
        self._err('stop_gmo', exc)
    return _ORIG_STOP(self)


_core.Runtime._patch_gmo = _patched_gmo
_core.Runtime.stop = _patched_stop

# Re-export the stable Android bridge API.
start = _core.start
stop = _core.stop
snapshot = _core.snapshot
hub_records = _core.hub_records
set_live = _core.set_live
