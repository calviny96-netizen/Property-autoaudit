"""Run the server's deploy script from the platform administrator's dashboard.

The script restarts this very API, so it runs detached from it and every bit of
state lives in files: a lock held while it runs, its output, and its exit code.
Enabled only where ``XM_DEPLOY_SCRIPT`` points at an existing script.
"""
import fcntl
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

LOG_TAIL_BYTES = 200_000
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
# The script gets a clean environment: no database credentials, and no NODE_ENV
# that would make ``npm ci`` skip the build tooling.
PASSED_ENV = ('HOME', 'PATH', 'USER', 'LOGNAME', 'SHELL', 'LANG', 'LC_ALL', 'PM2_HOME', 'NVM_DIR')
# Orphan the run (so a process manager killing the API's children cannot reach it)
# while it keeps the lock on fd 9; the script itself must not inherit that lock.
RUNNER = 'exec 9<&0; ( bash "$1" </dev/null >"$2" 2>&1 9<&-; echo $? >"$3" ) </dev/null >/dev/null 2>&1 &'


def script_path() -> Path | None:
    configured = os.getenv('XM_DEPLOY_SCRIPT', '').strip()
    path = Path(configured) if configured else None
    return path if path and path.is_file() else None


def state_dir(script: Path) -> Path:
    configured = os.getenv('XM_DEPLOY_STATE_DIR', '').strip()
    return Path(configured) if configured else script.parent / 'data' / 'deploy'


def _stamp(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def _is_running(folder: Path) -> bool:
    try:
        with open(folder / 'deploy.lock', 'rb') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
    except OSError:
        pass
    return False


def _read_log(folder: Path) -> str:
    try:
        with open(folder / 'deploy.log', 'rb') as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(log.tell() - LOG_TAIL_BYTES, 0))
            return ANSI.sub('', log.read().decode('utf-8', 'replace'))
    except OSError:
        return ''


def _commit(repo: Path) -> dict | None:
    try:
        done = subprocess.run(['git', '-C', str(repo), 'log', '-1', '--format=%h%x00%s%x00%cI'],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = done.stdout.strip().split('\x00')
    return {'sha': parts[0], 'subject': parts[1], 'date': parts[2]} if done.returncode == 0 and len(parts) == 3 else None


def status() -> dict:
    script = script_path()
    if not script:
        return {'enabled': False}
    folder = state_dir(script)
    started_at = _stamp(folder / 'deploy.started')
    exit_code = None
    if _is_running(folder):
        state = 'running'
    elif not started_at:
        state = 'idle'
    else:
        try:
            exit_code = int((folder / 'deploy.exit').read_text().strip())
        except (OSError, ValueError):
            pass  # Started but never finished: the server went down mid-deploy.
        state = 'success' if exit_code == 0 else 'failed'
    return {
        'enabled': True, 'status': state, 'exit_code': exit_code, 'started_at': started_at,
        'finished_at': _stamp(folder / 'deploy.exit') if state != 'running' else None,
        'commit': _commit(script.parent), 'log': _read_log(folder),
    }


def start() -> None:
    script = script_path()
    if not script:
        raise HTTPException(404, 'Deploying from the dashboard is not enabled on this server.')
    folder = state_dir(script)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / 'deploy.lock', 'a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(409, 'A deploy is already running.') from None
        (folder / 'deploy.exit').unlink(missing_ok=True)
        (folder / 'deploy.log').write_bytes(b'')
        (folder / 'deploy.started').write_bytes(b'')
        env = {key: os.environ[key] for key in PASSED_ENV if os.environ.get(key)}
        # The lock travels to the run as its stdin and outlives this request.
        subprocess.run(
            ['bash', '-c', RUNNER, 'xm-deploy', str(script), str(folder / 'deploy.log'), str(folder / 'deploy.exit')],
            stdin=lock, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=script.parent, env=env, start_new_session=True, check=True, timeout=10)


@router.get('/admin/deploy')
def get_deploy():
    return status()


@router.post('/admin/deploy', status_code=202)
def post_deploy():
    start()
    return status()
