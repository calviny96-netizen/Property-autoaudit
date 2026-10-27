"""Tiny in-process caches shared by routes (one API process serves many tabs)."""
import time
from threading import Lock

STATS_TTL = 15
_lock = Lock()
_stats_cache = {}
_values = {}


def cached_stats(workspace, loader, *, version=None):
    """Share counters per workspace, refreshing immediately after a worker rebuild."""
    with _lock:
        cached = _stats_cache.get(workspace)
        if cached is not None and cached[2] == version and time.monotonic() - cached[0] < STATS_TTL:
            return cached[1]
    value = loader()
    with _lock:
        _stats_cache[workspace] = (time.monotonic(), value, version)
    return value


def cached_value(workspace, key, ttl, loader):
    """Memoise an expensive read for a few seconds, per workspace. Never shared between companies."""
    full = (workspace, key)
    with _lock:
        cached = _values.get(full)
        if cached is not None and time.monotonic() - cached[0] < ttl:
            return cached[1]
    value = loader()
    with _lock:
        if len(_values) > 500:
            _values.clear()
        _values[full] = (time.monotonic(), value)
    return value


def invalidate_stats(workspace=None):
    """Called when something users can see changes right away (a status mark)."""
    with _lock:
        if workspace is None:
            _stats_cache.clear()
            _values.clear()
        else:
            _stats_cache.pop(workspace, None)
            for key in [key for key in _values if key[0] == workspace]:
                del _values[key]


def daily_snapshot(workspace, key, loader, *, refresh=False):
    """Persist one WIB-day snapshot per company/key; serialize concurrent rebuilds."""
    import fcntl
    import hashlib
    import json
    import os
    from datetime import datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo

    directory = Path(os.getenv('XM_STATS_DIR', '/data/statistics'))
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(json.dumps([str(workspace), key], sort_keys=True).encode()).hexdigest()
    path = directory / (digest + '.json')
    requested_at = datetime.now(ZoneInfo('Asia/Jakarta'))
    with (directory / (digest + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            saved = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            saved = None
        if saved and saved['day'] == requested_at.date().isoformat():
            # Concurrent refresh clicks share the calculation already in progress.
            if not refresh or saved['saved_at'] >= requested_at.isoformat():
                return saved['value']
        value = loader()
        stamp = datetime.now(ZoneInfo('Asia/Jakarta')).isoformat()
        value = {**value, 'statistics_updated_at': stamp}
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'day': requested_at.date().isoformat(), 'saved_at': stamp,
                                         'value': value}, default=str))
        temporary.replace(path)
        return value
