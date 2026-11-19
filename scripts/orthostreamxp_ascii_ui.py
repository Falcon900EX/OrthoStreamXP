#!/usr/bin/env python3
"""OrthoStreamXP macOS 1.4: standalone numbered, ANSI-colour terminal UI.

Uses the existing CLI and INI. No external packages or engine modifications.
"""
from __future__ import annotations

import datetime as dt
import argparse
import errno
import select
import shlex
import signal
import time
import uuid
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap

HOME = Path.home()
PROJECT = HOME / 'Downloads' / 'OrthoStreamXP-macos-upgrade-1.4'
CONFIG = HOME / 'Library' / 'Application Support' / 'OrthoStreamXP' / 'orthostreamxp-mac.ini'
LOGS = CONFIG.parent / 'logs'
CACHE = HOME / 'Library' / 'Caches' / 'OrthoStreamXP'
STATE_DIR = CONFIG.parent / 'ascii-ui-state'
SESSION_DIR = STATE_DIR / 'engine-session'
COLOR = sys.stdout.isatty() and not os.environ.get('NO_COLOR')
C = {
    'reset': '\033[0m', 'bold': '\033[1m', 'dim': '\033[2m',
    'cyan': '\033[96m', 'blue': '\033[94m', 'green': '\033[92m',
    'yellow': '\033[93m', 'red': '\033[91m', 'purple': '\033[95m',
}


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _live_engine_processes() -> list[int]:
    """Catch engine instances launched outside this control panel, too."""
    try:
        output = subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True,
                                         stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return [-1]  # Unknown is unsafe; refuse duplicate launches.
    found = []
    for row in output.splitlines():
        parts = row.strip().split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        argv = parts[1].strip()
        # Executable itself, not the launcher, editor, or grep of its name.
        executable = argv.split(None, 1)[0].strip('"\'')
        if Path(executable).name == 'OrthoStreamXP.Mac':
            found.append(pid)
    return found


def _orthostream_mounts() -> list[tuple[str, str]] | None:
    """Read mount records without probing the mounted filesystem.

    None means inspection failed, which must never authorize startup/recovery.
    Each tuple is (mountpoint, filesystem identity).
    """
    try:
        output = subprocess.check_output(['mount'], text=True,
                                         stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    mounts = []
    for line in output.splitlines():
        if ' on ' not in line or ' (macfuse,' not in line:
            continue
        source, rest = line.split(' on ', 1)
        mountpoint, _, _ = rest.partition(' (')
        if Path(mountpoint).name.startswith('z_OrthoStreamXP_'):
            mounts.append((mountpoint, source))
    return mounts


def _active_orthostream_mounts() -> bool:
    mounts = _orthostream_mounts()
    return mounts is None or bool(mounts)


def _xplane_processes() -> list[int]:
    """Conservatively find the simulator executable, not unrelated file paths."""
    try:
        output = subprocess.check_output(['ps', '-axo', 'pid=,command='], text=True,
                                         stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return [-1]
    found = []
    for row in output.splitlines():
        parts = row.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        command = parts[1].strip()
        executable = command.split(None, 1)[0].strip('"\'')
        if Path(executable).name.lower() in ('x-plane', 'x-plane.exe'):
            found.append(pid)
    return found


def _read_session() -> dict:
    try:
        return json.loads((SESSION_DIR / 'state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _write_session(token: str, **values) -> None:
    """Only the owner of the reserved session may update it."""
    previous = _read_session()
    if previous.get('token') != token:
        return
    record = dict(previous, **values)
    temp = SESSION_DIR / f'.state.{os.getpid()}.tmp'
    temp.write_text(json.dumps(record), encoding='utf-8')
    os.replace(temp, SESSION_DIR / 'state.json')


def _session_status() -> str:
    state = _read_session()
    if not SESSION_DIR.is_dir():
        return 'STOPPED'
    status = state.get('status', 'UNKNOWN')
    if status == 'LAUNCHING':
        # Allow time for Terminal/AppleScript to create the engine session.
        if time.time() - state.get('created', 0) < 60:
            return 'STARTING'
        return 'UNKNOWN'
    if status == 'RUNNING':
        runner = state.get('runner_pid', 0)
        engine = state.get('engine_pid', 0)
        if _pid_alive(runner) and _pid_alive(engine):
            return 'RUNNING'
        if _pid_alive(engine):
            return 'RUNNING'
        return 'UNKNOWN'
    if status == 'STOPPED':
        return 'STOPPED'
    return 'UNKNOWN'


def engine_status() -> str:
    status = _session_status()
    if status in ('RUNNING', 'STARTING'):
        return status
    # A stale/missing status file must not permit another active engine or mount.
    if _live_engine_processes():
        return 'RUNNING (EXTERNAL)'
    mounts = _orthostream_mounts()
    if mounts is None:
        return 'UNKNOWN (MOUNT CHECK FAILED)'
    if mounts:
        return 'ORPHANED MOUNT'
    # A previous session may be stale; don't keep Home stuck as UNKNOWN.
    if status == 'UNKNOWN' and _read_session().get('status') == 'STOPPED':
        return 'STOPPED'
    return status


def _reserve_session() -> str:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    for attempt in range(2):
        try:
            SESSION_DIR.mkdir(mode=0o700)
        except FileExistsError:
            status = engine_status()
            if status != 'STOPPED' and status != 'UNKNOWN':
                raise RuntimeError(f'Engine already active ({status})')
            record = _read_session()
            if status == 'UNKNOWN' and (
                _pid_alive(record.get('runner_pid', 0)) or
                _pid_alive(record.get('engine_pid', 0))
            ):
                raise RuntimeError('Existing engine session is still active')
            # Avoid reclaiming a session that a different launcher just reserved.
            if status == 'UNKNOWN' and time.time() - record.get('created', time.time()) < 60:
                raise RuntimeError('Previous launch is not yet resolved')
            try:
                (SESSION_DIR / 'state.json').unlink(missing_ok=True)
                SESSION_DIR.rmdir()
            except OSError as exc:
                raise RuntimeError('Cannot safely reclaim old engine session') from exc
        else:
            (SESSION_DIR / 'state.json').write_text(json.dumps({
                'token': token, 'status': 'LAUNCHING', 'created': time.time()
            }), encoding='utf-8')
            return token
    raise RuntimeError('Unable to reserve engine session')


def launch_engine() -> None:
    heading('START ENGINE', 'Streaming opens in a separate macOS Terminal window.')
    if sys.platform != 'darwin':
        print(style('  Launching a macOS Terminal window requires macOS.', 'red'))
        pause()
        return
    launcher = PROJECT / 'scripts' / 'run-macos.sh'
    if not launcher.is_file() or not (PROJECT / 'artifacts/macos-arm64/OrthoStreamXP.Mac').is_file():
        print(style('  Engine not built; run scripts/build-macos.sh first.', 'red'))
        pause()
        return
    status = engine_status()
    if status == 'ORPHANED MOUNT':
        emergency_unmount(via_start=True)
        return
    if status != 'STOPPED' and status != 'UNKNOWN':
        print(style(f'  Cannot start: {status}', 'yellow'))
        if status.startswith('RUNNING'):
            print(style('  Engine already running; use its Terminal to stop it.', 'cyan'))
        pause()
        return
    if _live_engine_processes() or _active_orthostream_mounts():
        print(style('  Engine process or mount is already active.', 'yellow'))
        pause()
        return
    try:
        token = _reserve_session()
        # Run this same UI file as a standalone engine session in Terminal.
        command = f'{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))} --engine-session {shlex.quote(token)}'
        # Terminal's "do script" creates its own session rather than blocking us.
        applescript = ('tell application "Terminal"\n'
                       'activate\n'
                       f'do script {json.dumps(command)}\n'
                       'end tell')
        result = subprocess.run(['osascript', '-e', applescript], capture_output=True,
                                text=True, timeout=15)
        if result.returncode:
            _write_session(token, status='STOPPED', error=result.stderr.strip())
            raise RuntimeError(result.stderr.strip() or 'Terminal did not open')
        print(style('  Engine terminal launch requested.', 'green'))
        print(style('  The control panel remains available.', 'cyan'))
        return  # Redraw Home immediately; do not block for another keypress.
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(style(f'  Launch failed: {exc}', 'red'))
        pause()


def run_engine_session(token: str) -> int:
    """Runs only in the second Terminal session. Ctrl+C targets the engine group."""
    if _read_session().get('token') != token:
        print('OrthoStreamXP: engine session token invalid or expired.')
        return 2
    print('OrthoStreamXP 1.4 / macOS ARM64 / Engine Terminal', flush=True)
    print('Ctrl+C requests graceful engine shutdown; control panel stays open.\n', flush=True)
    if _live_engine_processes() or _active_orthostream_mounts():
        _write_session(token, status='STOPPED', error='Existing engine process or mount')
        print('Existing OrthoStreamXP engine/mount detected. Not starting another.')
        return 3
    child = None
    rc = 1
    try:
        child = subprocess.Popen(['bash', 'scripts/run-macos.sh', 'run'],
                                 cwd=PROJECT, start_new_session=True)
        _write_session(token, status='RUNNING', runner_pid=os.getpid(), engine_pid=child.pid)
        try:
            rc = child.wait()
        except KeyboardInterrupt:
            print('\nRequesting graceful engine shutdown...', flush=True)
            try:
                os.killpg(child.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            # No forced kill/unmount: allow the existing engine shutdown routine.
            try:
                rc = child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                print('Engine still shutting down. Check macFUSE before restarting.', flush=True)
                rc = 130
    except (OSError, Exception) as exc:
        print(f'Engine session error: {exc}', flush=True)
        rc = 1
    finally:
        _write_session(token, status='STOPPED', stopped=time.time(), exit_code=rc)
    print(f'\nEngine exited (code {rc}). Return to the original CLI Home menu.', flush=True)
    print('You may close this Terminal window.', flush=True)
    return rc


def emergency_unmount(via_start: bool = False) -> None:
    """Guarded recovery for an orphaned *owned* mount only.

    Never force automatically. Use the existing identity-aware Mac lifecycle helper.
    """
    heading('EMERGENCY / UNMOUNT',
            'USE ONLY IF ORTHOSTREAMXP IS UNRESPONSIVE AND ITS MOUNT IS STUCK.')
    print(style('  [ SAFETY COVER CLOSED ]', 'yellow', 'bold'))
    print(style('  WARNING: Unmounting during a flight can interrupt scenery reads.', 'red'))
    print('  Close X-Plane and stop the OrthoStreamXP engine first.')
    if via_start:
        print(style('  Start is blocked by a leftover mount. Recovery is available here.', 'yellow'))
    print()
    print('  To lift the safety cover, type LIFT (or 0 to cancel).')
    if ask('Safety cover').upper() != 'LIFT':
        return

    engines = _live_engine_processes()
    pilots = _xplane_processes()
    mounts = _orthostream_mounts()
    if engines or pilots or mounts is None:
        print(style('  REFUSED: Engine/X-Plane active, or process/mount status unknown.', 'red'))
        print(style(f'  Engine PIDs: {engines}  X-Plane PIDs: {pilots}', 'yellow'))
        pause()
        return
    if not mounts:
        print(style('  No OrthoStreamXP macFUSE mounts found. Nothing to recover.', 'green'))
        pause()
        return
    if len(mounts) != 1:
        print(style('  REFUSED: Multiple mounts found. Diagnose individually.', 'red'))
        for path, _ in mounts:
            print('  ' + fit(path))
        pause()
        return
    path, identity = mounts[0]
    expected_parent = HOME / 'X-Plane 12' / 'Custom Scenery'
    if (Path(path).parent != expected_parent or
            not Path(path).name.startswith('z_OrthoStreamXP_') or
            not re.fullmatch(r'OrthoStreamXP-[0-9A-Za-z_-]+', identity)):
        print(style('  REFUSED: Mount location or filesystem identity unexpected.', 'red'))
        pause()
        return

    heading('EMERGENCY / ARMED', 'SAFETY COVER OPEN — normal unmount first.')
    print(style('  [ SAFETY COVER OPEN ]', 'yellow', 'bold'))
    print('  Target: ' + fit(path))
    print('  Identity: ' + fit(identity))
    print(style('  Only proceed if X-Plane is closed and streaming has stopped.', 'red'))
    print('  Type UNMOUNT to request a normal unmount, or 0 to cancel.')
    if ask('Confirm').upper() != 'UNMOUNT':
        return
    try:
        # Import only when requested; the donor implementation is the existing
        # identity-verified, bounded macFUSE lifecycle recovery routine.
        sys.path.insert(0, str(PROJECT / 'platform' / 'macos'))
        from donor.mac_lifecycle import unmount_owned
    except (ImportError, OSError) as exc:
        print(style(f'  Recovery helper unavailable: {exc}', 'red'))
        pause()
        return

    def safe_unmount(force: bool) -> bool:
        # Recheck before each action to catch engines/X-Plane started meanwhile.
        if _live_engine_processes() or _xplane_processes():
            raise RuntimeError('Engine or X-Plane is active; unmount cancelled')
        current = _orthostream_mounts()
        if current is None or any(m[0] == path and m[1] != identity for m in current):
            raise RuntimeError('Mount identity could not be reverified')
        if not current or (path, identity) not in current:
            return True
        return unmount_owned(path, identity, force=force, timeout=8.0)

    print(style('  Requesting normal unmount...', 'cyan'))
    try:
        success = safe_unmount(force=False)
    except (OSError, RuntimeError) as exc:
        print(style(f'  Unmount refused: {exc}', 'red'))
        pause()
        return
    if success:
        heading('EMERGENCY / RECOVERY COMPLETE')
        print(style('  Normal unmount succeeded. Return Home to start the engine.', 'green'))
        pause()
        return

    heading('EMERGENCY / FORCE UNMOUNT', 'NORMAL UNMOUNT FAILED. FORCE IS LAST RESORT.')
    print(style('  [ RED GUARD — FORCE UNMOUNT ]', 'red', 'bold'))
    print(style('  Force may disrupt stalled filesystem operations.', 'red'))
    print('  Type FORCE to explicitly request it, or 0 to cancel.')
    if ask('Second confirmation').upper() != 'FORCE':
        return
    try:
        success = safe_unmount(force=True)
    except (OSError, RuntimeError) as exc:
        print(style(f'  Forced recovery refused: {exc}', 'red'))
        pause()
        return
    heading('EMERGENCY / RECOVERY RESULT')
    print(style('  Mount detached. Return Home to start OrthoStreamXP.' if success
                else '  Mount remains active. Do not start another engine.',
                'green' if success else 'red'))
    pause()


def engine_status_line() -> str:
    status = engine_status()
    color = 'green' if status.startswith('RUNNING') else 'yellow' if status == 'STARTING' else 'cyan'
    if status.startswith('UNKNOWN') or status == 'ORPHANED MOUNT':
        color = 'red'
    return style(status, color, 'bold')


def style(text: object, *colors: str) -> str:
    text = str(text)
    return ''.join(C[x] for x in colors) + text + C['reset'] if COLOR else text


def terminal_size():
    """Read the current terminal size on every redraw (including window resizes)."""
    size = shutil.get_terminal_size(fallback=(80, 24))
    return max(20, size.columns), max(8, size.lines)


def content_width():
    return max(16, terminal_size()[0] - 4)


def fit(value: object, width: int | None = None) -> str:
    """Truncate display text safely to the current column budget."""
    width = content_width() if width is None else max(1, width)
    text = str(value).replace('\t', '    ').replace('\r', ' ').replace('\n', ' ')
    return text if len(text) <= width else text[:max(0, width - 1)] + '…'


def line(char='-', width=None):
    width = content_width() if width is None else min(width, content_width())
    print(style(char * width, 'blue'))


def clear_screen():
    """Redraw each page from the top without leaving previous menus behind."""
    if sys.stdout.isatty() and os.environ.get('TERM', '') != 'dumb':
        print('\033[2J\033[H', end='', flush=True)
    else:
        print('\n' * 2, end='')


def heading(title: str, subtitle: str = ''):
    clear_screen()
    cols, rows = terminal_size()
    brand = 'ORTHOSTREAMXP  /  MACOS ARM64  /  1.4'
    print(style('  ' + fit(brand), 'bold', 'cyan'))
    line('=')
    print(style('  ' + fit(title), 'bold', 'purple'))
    if subtitle and rows >= 12:
        print(style('  ' + fit(subtitle), 'dim'))
    line()


def ask(prompt='Selection', watch_status: str | None = None) -> str:
    try:
        rendered = style(f'  {fit(prompt, max(8, content_width()-4))} > ', 'cyan')
        if watch_status is not None and sys.stdin.isatty():
            sys.stdout.write(rendered)
            sys.stdout.flush()
            while True:
                readable, _, _ = select.select([sys.stdin], [], [], 1.0)
                if readable:
                    return sys.stdin.readline().strip()
                if engine_status() != watch_status:
                    return '__STATUS_CHANGED__'
        return input(rendered).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return '0'


def pause():
    ask('Press ENTER to return')


def choice(items: list[str], footer='0. Back', watch_status: str | None = None) -> int | None:
    """Numbered choices, paginated to fit terminal height. Numbers stay global."""
    _, rows = terminal_size()
    page = 0
    # Reserve room for heading, prompt, footer and any parent-page content.
    per_page = max(1, rows - 12)
    while True:
        per_page = max(1, terminal_size()[1] - 12)
        pages = max(1, (len(items) + per_page - 1) // per_page)
        page = min(page, pages - 1)
        begin = page * per_page
        end = min(len(items), begin + per_page)
        for idx in range(begin, end):
            number = str(idx + 1)
            available = max(8, content_width() - len(number) - 3)
            print(f'  {style(number, "cyan")}. {fit(items[idx], available)}')
        print('  ' + fit(footer))
        if pages > 1:
            print(style(f'  Page {page + 1}/{pages}  |  N next  P previous', 'dim'))
        picked = ask(watch_status=watch_status)
        if picked == '__STATUS_CHANGED__':
            return -2
        if picked in ('0', ''):
            return None
        if pages > 1 and picked.lower() in ('n', 'p'):
            direction = 1 if picked.lower() == 'n' else -1
            if 0 <= page + direction < pages:
                page += direction
            # Caller redraw is not available, so redraw the page heading in place.
            heading('NUMBERED SELECTIONS', f'Page {page + 1} of {pages}')
            continue
        if picked.isdecimal() and 1 <= int(picked) <= len(items):
            return int(picked) - 1
        print(style('  Invalid selection.', 'yellow'))
        # Clearing avoids endlessly accumulating invalid-input messages.
        heading('NUMBERED SELECTIONS', f'Page {page + 1} of {pages}')


def ini_entries() -> list[tuple[str, str, int]]:
    if not CONFIG.is_file():
        return []
    found = []
    for number, line_text in enumerate(CONFIG.read_text(encoding='utf-8').splitlines(), 1):
        s = line_text.strip()
        if not s or s.startswith(('#', ';', '[')) or '=' not in line_text:
            continue
        key, value = line_text.split('=', 1)
        key = key.strip()
        if re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.-]*', key):
            found.append((key, value.strip(), number))
    return found


CATEGORIES = [
    ('Imagery & Quality', ('quality', 'level-', 'texture-', 'provider', 'zoom', 'ground-detail', 'paging-', 'mip-')),
    ('Downloads & Workers', ('download-', 'worker', 'encoder-', 'dds-', 'build-', 'timeout', 'attempt')),
    ('Caching & Storage', ('cache', 'store', 'lazy-')),
    ('Aircraft & Prefetch', ('aircraft', 'prefetch', 'lookahead', 'track-', 'near-wait', 'max-wait', 'flight-')),
    ('Night, Seasons & Colour', ('night-', 'skip-photos', 'season', 'colour', 'color', 'exposure', 'match-')),
    ('Filesystem & Diagnostics', ('filesystem', 'mount', 'read-log', 'log-', 'detail')),
]


def section_for(key: str) -> str:
    k = key.lower()
    for label, needles in CATEGORIES:
        if any(s in k for s in needles):
            return label
    return 'Other Settings'


def render_value(key: str, value: str) -> str:
    v = value.lower()
    if v in ('on', 'true', 'yes'):
        return style(value, 'green')
    if v in ('off', 'false', 'no'):
        return style(value, 'yellow')
    if key == 'level-change' and value == '-1':
        return style(value + ' (Sharp)', 'green')
    return style(value if len(value) < 100 else value[:97] + '...', 'cyan')


def update_existing_value(key: str, lineno: int, new_value: str):
    raw = CONFIG.read_text(encoding='utf-8')
    lines = raw.splitlines(keepends=True)
    if lineno > len(lines):
        raise ValueError('INI changed since page loaded; reopen settings.')
    before = lines[lineno-1]
    match = re.match(r'^(\s*' + re.escape(key) + r'\s*=\s*)([^\r\n]*)(\r?\n?)$', before, re.I)
    if not match:
        raise ValueError('INI changed since page loaded; reopen settings.')
    newline = match.group(3)
    lines[lineno-1] = match.group(1) + new_value + newline
    backup = CONFIG.with_name(CONFIG.name + '.backup-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    shutil.copy2(CONFIG, backup)
    temp = CONFIG.with_name(CONFIG.name + '.ascii-ui.tmp')
    try:
        temp.write_text(''.join(lines), encoding='utf-8')
        os.chmod(temp, CONFIG.stat().st_mode & 0o777)
        os.replace(temp, CONFIG)
    finally:
        temp.unlink(missing_ok=True)
    print(style('  Saved. Changes apply on the NEXT engine launch.', 'green'))
    print(style('  ' + fit(f'Backup: {backup}'), 'dim'))


def edit_page(label: str):
    while True:
        entries = [v for v in ini_entries() if section_for(v[0]) == label]
        heading('SETTINGS  /  ' + label.upper(), 'Edits apply on next launch; only existing INI keys shown.')
        if not entries:
            print(style('  No settings in this category.', 'yellow'))
            pause()
            return
        options = [f'{key} = {val}' for key, val, _ in entries]
        selected = choice(options)
        if selected is None:
            return
        if selected < 0:
            continue
        key, old, lineno = entries[selected]
        heading('EDIT SETTING', f'Line {lineno} in {CONFIG.name}')
        print('  ' + style(key, 'bold') + ' = ' + render_value(key, old))
        print(style('  Enter a new value, or leave blank to cancel.', 'dim'))
        new = ask('New value')
        if not new or new == old:
            continue
        if '\n' in new or '\r' in new or '\x00' in new or new.startswith(('#', ';')):
            print(style('  Invalid INI value.', 'red'))
            pause()
            continue
        if old.lower() in ('on','off','true','false','yes','no') and new.lower() not in ('on','off','true','false','yes','no'):
            print(style('  Boolean setting: use on/off, true/false, or yes/no.', 'yellow'))
            pause()
            continue
        if re.fullmatch(r'[+-]?\d+', old) and not re.fullmatch(r'[+-]?\d+', new):
            print(style('  Expected a whole number.', 'yellow'))
            pause()
            continue
        print(f'  {old} {style("->", "yellow")} {new}')
        if ask('Save this change? [y/N]').lower() == 'y':
            try:
                update_existing_value(key, lineno, new)
            except Exception as exc:
                print(style(f'  Not saved: {exc}', 'red'))
            pause()


def settings():
    while True:
        entries = ini_entries()
        labels = [label for label, _ in CATEGORIES] + ['Other Settings']
        counts = {label: sum(section_for(e[0]) == label for e in entries) for label in labels}
        heading('NUMBERED SETTINGS', f'{len(entries)} INI entries; edits apply on next engine launch.')
        print(style('  ' + fit(f'Config: {CONFIG}'), 'dim'))
        selected = choice([f'{label}  {style(f"({counts[label]})", "dim")}' for label in labels])
        if selected is None:
            return
        if selected >= 0:
            edit_page(labels[selected])


def latest_log() -> Path | None:
    matches = list(LOGS.glob('orthostreamxp-*.log')) if LOGS.is_dir() else []
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def pretty_log(raw: str) -> str:
    low = raw.lower()
    if any(s in low for s in ('error', 'failed', 'exception', 'without real image')):
        return style(raw, 'red')
    if any(s in low for s in ('warning', 'slow', 'timeout', 'limit')):
        return style(raw, 'yellow')
    if any(s in low for s in ('suited this flight', 'mounts are ready', 'passed')):
        return style(raw, 'green')
    if any(s in low for s in ('flight tuning', 'load over', 'mbit', 'texture', 'cache')):
        return style(raw, 'cyan')
    return raw


def log_page(filtered=False):
    log = latest_log()
    if not log:
        heading('PERFORMANCE & FLIGHT TUNING' if filtered else 'LATEST ENGINE LOG')
        print(style('  No OrthoStreamXP logs found.', 'yellow'))
        pause()
        return
    raw = log.read_text(encoding='utf-8', errors='replace').splitlines()
    if filtered:
        terms = ('flight tuning', 'load over', 'mib', 'mbit', 'texture', 'download',
                 'read without', 'memory', 'peak', 'advisor', 'performance', 'cache limit')
        raw = [s for s in raw if any(t in s.lower() for t in terms)]
    page = None
    while True:
        heading('PERFORMANCE & FLIGHT TUNING' if filtered else 'LATEST ENGINE LOG',
                'Green: healthy  /  Yellow: caution  /  Red: errors  /  Cyan: measurements')
        print(style('  ' + fit(f'Source: {log.name}'), 'dim'))
        # Logs are wrapped to fit the width, then paged by terminal height.
        width = content_width()
        rendered = []
        for entry in raw:
            rendered.extend(textwrap.wrap(entry.expandtabs(4), width=width,
                                          break_long_words=True, break_on_hyphens=False)
                            or [''])
        per_page = max(1, terminal_size()[1] - 11)
        pages = max(1, (len(rendered) + per_page - 1) // per_page)
        page = pages - 1 if page is None else min(page, pages - 1)
        start = page * per_page
        for item in rendered[start:start + per_page]:
            print('  ' + pretty_log(item))
        print(style(f'  Page {page+1}/{pages}  |  {len(raw)} entries  |  N next  P previous  0 back', 'dim'))
        response = ask()
        if response.lower() == 'p' and page > 0:
            page -= 1
        elif response.lower() == 'n' and page < pages - 1:
            page += 1
        else:
            return


def cache_page():
    heading('CACHE & STORAGE', 'Read-only disk usage; no cache removal.')
    if not CACHE.is_dir():
        print(style(f'  Cache not found: {CACHE}', 'yellow'))
        pause()
        return
    for folder in sorted(CACHE.iterdir(), key=lambda p:p.name):
        if not folder.is_dir():
            continue
        try:
            size = sum(f.stat().st_size for f in folder.rglob('*') if f.is_file())
        except (OSError, PermissionError):
            continue
        gig = size / (1024**3)
        print('  ' + fit(folder.name, max(8, content_width()-12)) + '  ' + style(f'{gig:.2f} GiB', 'cyan' if gig < 25 else 'yellow'))
    pause()


def run_command(label: str, command: list[str], needs_idle: bool = False):
    heading(label, 'Running existing Mac launcher; output follows below.')
    if needs_idle and engine_status() != 'STOPPED' and engine_status() != 'UNKNOWN':
        print(style('  Not available while streaming is running.', 'yellow'))
        pause()
        return
    if not PROJECT.is_dir():
        print(style(f'  Project directory missing: {PROJECT}', 'red'))
        pause()
        return
    print(style('  ' + fit('Command: ' + ' '.join(command)), 'dim'))
    line()
    try:
        completed = subprocess.run(command, cwd=PROJECT, check=False)
        print(style(f'\n  Exit code: {completed.returncode}', 'green' if completed.returncode == 0 else 'yellow'))
    except KeyboardInterrupt:
        print(style('\n  Interrupted. Check mounts before starting again.', 'yellow'))
    except OSError as exc:
        print(style(f'  Could not start: {exc}', 'red'))
    pause()


def main():
    while True:
        heading('MAIN MENU', 'Numbered pages  |  ANSI colours  |  Existing macOS CLI + INI')
        print('  Git workspace: ' + fit(PROJECT.name, max(8, content_width()-17)))
        print(f'  Config: {style("FOUND", "green") if CONFIG.is_file() else style("MISSING", "red")}')
        status_snapshot = engine_status()
        print('  Engine: ' + engine_status_line())
        print()
        options = [
            'Start OrthoStreamXP (separate Terminal; disabled if running)',
            'Settings (numbered categories and options)',
            'Performance / Flight Tuning (coloured log view)',
            'Latest engine log',
            'Cache usage',
            'Run doctor',
            'Run synthetic macFUSE smoke test',
            'EMERGENCY UNMOUNT — ONLY IF ORTHOSTREAMXP IS UNRESPONSIVE',
        ]
        selected = choice(options, '0. Quit', watch_status=status_snapshot)
        if selected == -2:
            continue  # Redraw Home promptly when engine starts or stops.
        if selected is None:
            print(style('  Goodbye.', 'cyan'))
            return
        if selected == 0:
            launch_engine()
        elif selected == 1:
            settings()
        elif selected == 2:
            log_page(True)
        elif selected == 3:
            log_page(False)
        elif selected == 4:
            cache_page()
        elif selected == 5:
            run_command('DOCTOR', ['bash','scripts/run-macos.sh','doctor'])
        elif selected == 6:
            run_command('SYNTHETIC SMOKE TEST', ['bash','scripts/smoke-macos.sh'], needs_idle=True)
        elif selected == 7:
            emergency_unmount()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--engine-session':
        sys.exit(run_engine_session(sys.argv[2]))
    try:
        main()
    except KeyboardInterrupt:
        print('\n' + style('  Terminal interface closed.', 'cyan'))
