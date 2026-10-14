#!/usr/bin/python3
"""Root-only, fixed-product Debian updater. No caller-controlled commands or feeds."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
import email.utils

REPO = 'Alshival-Ai/alshitasks'
STATE = Path('/var/lib/alshitasks-updates')
MANAGED = Path('/etc/alshitasks-managed-updates')
WEEK = 7 * 86400


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60, **kwargs).stdout.strip()


def stable(value):
    if not re.fullmatch(r'\d+\.\d+\.\d+', value or ''):
        raise ValueError('Invalid stable version')
    return value


def select(release, arch):
    if release.get('draft') or release.get('prerelease') or arch != 'amd64':
        raise ValueError('Unsupported release or architecture')
    version = stable(release['tag_name'].removeprefix('v'))
    names = [f'alshitasks_{version}_{arch}.deb', 'SHA256SUMS']
    result = []
    for name in names:
        rows = [a for a in release['assets'] if a['name'] == name]
        expected = f'https://github.com/{REPO}/releases/download/{release["tag_name"]}/{name}'
        if len(rows) != 1 or rows[0]['browser_download_url'] != expected:
            raise ValueError('Incomplete release or unexpected download origin')
        result.append(expected)
    return version, names[0], result


def download(url, destination, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'Alshival-package-updater'})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=60) as response, destination.open('wb') as stream:
        if not response.url.startswith('https://'):
            raise ValueError('Insecure redirect refused')
        count = 0
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > limit or time.monotonic() - started > 1800:
                raise ValueError('Update download exceeded its limit')
            stream.write(chunk)
        stream.flush()
        os.fsync(stream.fileno())


def verify(package, sums):
    rows = [p for line in sums.splitlines() if len(p := line.split()) == 2 and p[1].lstrip('*') == package.name]
    if len(rows) != 1 or not re.fullmatch(r'[a-fA-F0-9]{64}', rows[0][0]):
        raise ValueError('Missing package checksum')
    with package.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != rows[0][0].lower():
        raise ValueError('Package checksum mismatch')


def read(name):
    try:
        return json.loads((STATE / name).read_text())
    except FileNotFoundError:
        return {}


def write(name, value):
    tmp = STATE / (name + '.tmp')
    tmp.write_text(json.dumps(value))
    tmp.chmod(0o644 if name == 'status.json' else 0o600)
    tmp.replace(STATE / name)


def status(**values):
    old = read('status.json')
    old.update(values)
    write('status.json', old)


def installed():
    value = run(['dpkg-query', '-W', '-f=${Status}\t${Version}', 'alshitasks'])
    state, version = value.split('\t', 1)
    if state not in ('install ok installed', 'hold ok installed'):
        raise RuntimeError('The existing package needs repair in the system package manager before updating.')
    return version


def newer(value, current):
    return subprocess.run(['dpkg', '--compare-versions', value, 'gt', current], capture_output=True).returncode == 0


def stage(version=None, candidate=None, uid=None):
    suffix = 'tags/v' + stable(version) if version else 'latest'
    metadata = STATE / 'release.json'
    download(f'https://api.github.com/repos/{REPO}/releases/{suffix}', metadata, 1024 * 1024)
    target, name, urls = select(json.loads(metadata.read_text()), run(['dpkg', '--print-architecture']))
    if version and target != version:
        raise ValueError('Unexpected release version')
    if not newer(target, installed()):
        write('pending.json', {})
        status(phase='current', availableVersion=None, error='')
        return False
    sums = STATE / 'SHA256SUMS'
    download(urls[1], sums, 1024 * 1024)
    package = STATE / name
    valid = False
    try:
        verify(package, sums.read_text())
        valid = True
    except (ValueError, FileNotFoundError):
        pass
    if not valid:
        part = STATE / (name + '.part')
        status(phase='downloading', availableVersion=target, progress=None, error='')
        try:
            if candidate:
                # Copy only a regular file owned by the authenticated caller; never
                # trust its name, contents, or digest. Revalidate in root-owned staging.
                fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as source, part.open('wb') as dest:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_size > 1024 ** 3:
                        raise ValueError('Invalid downloaded package')
                    copied = 0
                    while chunk := source.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > 1024 ** 3:
                            raise ValueError('Package size limit exceeded')
                        dest.write(chunk)
            else:
                download(urls[0], part, 1024 ** 3)
            part.replace(package)
            verify(package, sums.read_text())
        except Exception:
            part.unlink(missing_ok=True)
            package.unlink(missing_ok=True)
            raise
    for field, expected in [('Package', 'alshitasks'), ('Version', target), ('Architecture', 'amd64')]:
        if run(['dpkg-deb', '-f', str(package), field]) != expected:
            raise ValueError('Unexpected Debian package identity')
    write('pending.json', {'version': target, 'name': name, 'sums': sums.read_text()})
    status(phase='ready', availableVersion=target, progress=100, error='')
    for cached in STATE.iterdir():
        if re.fullmatch(r'alshitasks_\d+\.\d+\.\d+_amd64\.deb(?:\.part)?', cached.name) and cached.name != name:
            cached.unlink()
    return True


def desktop_pids(proc=Path('/proc')):
    result = []
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            exe = os.readlink(entry / 'exe').removesuffix(' (deleted)')
            args = (entry / 'cmdline').read_bytes().split(b'\0')
            if exe == '/usr/lib/alshitasks/electron/electron' and b'--type=' not in b' '.join(args):
                result.append((int(entry.name), entry.stat().st_uid))
        except (OSError, ProcessLookupError):
            pass
    return result


def backend_uids(proc=Path('/proc')):
    result = set()
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            command = (entry / 'cmdline').read_bytes().replace(b'\0', b' ')
            if re.search(rb' /usr/lib/alshitasks/service/alshitasks-cli\.py(?: |$)', command) and re.search(rb'(?:^| )service(?: |$)', command):
                result.add(entry.stat().st_uid)
        except OSError:
            pass
    return result


def user_services():
    services = []
    for entry in Path('/run/user').iterdir():
        if not entry.name.isdecimal() or int(entry.name) < 1000:
            continue
        uid = int(entry.name)
        try:
            user = pwd.getpwuid(uid)
            bus = entry / 'bus'
            info = bus.lstat()
            if info.st_uid != uid or not stat.S_ISSOCK(info.st_mode):
                continue
        except (KeyError, FileNotFoundError):
            continue
        command = ['runuser', '-u', user.pw_name, '--', 'env', f'XDG_RUNTIME_DIR={entry}', f'DBUS_SESSION_BUS_ADDRESS=unix:path={bus}', 'systemctl', '--user']
        if subprocess.run(command + ['is-active', '--quiet', 'alshitasks.service'], capture_output=True, timeout=30).returncode == 0:
            services.append(command)
    return services


def install(requester=None, uid=None):
    pending = read('pending.json')
    if not pending:
        return False
    stable(pending['version'])
    name = f'alshitasks_{pending["version"]}_amd64.deb'
    if pending['name'] != name:
        raise ValueError('Invalid pending package')
    if not newer(pending['version'], installed()):
        write('pending.json', {})
        status(phase='current', availableVersion=None, error='')
        return True
    if 'alshitasks' in run(['apt-mark', 'showhold']).splitlines():
        raise RuntimeError('Updates are paused by the system package manager.')
    active = desktop_pids()
    if requester and (requester, uid) not in active:
        raise ValueError('The update request did not come from the installed desktop.')
    if any(pid != requester for pid, _ in active):
        status(phase='ready', error='Close the other Alshival.Ai desktop sessions before installing this update.')
        return False
    verify(STATE / name, pending['sums'])
    # The shared lock also prevents new desktop launches during package replacement.
    status(phase='installing', installerPid=os.getpid(), error='')
    services = user_services()
    managed_uids = {pwd.getpwnam(command[2]).pw_uid for command in services}
    if backend_uids() - managed_uids:
        raise RuntimeError('Stop background services that are not managed by systemd, then retry the update.')
    stopped = []
    try:
        for command in services:
            run(command + ['stop', 'alshitasks.service'])
            stopped.append(command)
        if any(pid != requester for pid, _ in desktop_pids()):
            raise RuntimeError('Another desktop opened. Close it before installing.')
        subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', '--no-remove', 'install', '-y', str(STATE / name)], env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'}, check=True, timeout=900, stdout=sys.stderr, stderr=sys.stderr)
        if installed() != pending['version']:
            raise RuntimeError('The package manager did not install the expected version.')
        write('pending.json', {})
        status(phase='current', installedVersion=pending['version'], availableVersion=None, error='')
        return True
    finally:
        failures = []
        for command in stopped:
            try:
                run(command + ['start', 'alshitasks.service'])
            except Exception:
                failures.append(True)
        if failures:
            raise RuntimeError('The package installation finished, but a background service needs restarting.')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--pending', action='store_true')
    parser.add_argument('--version')
    parser.add_argument('--package')
    parser.add_argument('--requester', type=int)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise PermissionError('Administrator authorization is required.')
    os.umask(0o022)
    STATE.mkdir(mode=0o755, exist_ok=True)
    if STATE.is_symlink() or STATE.stat().st_uid != 0 or STATE.stat().st_mode & 0o022:
        raise PermissionError('Unsafe updater state directory')
    with (STATE / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            uid = int(os.environ['PKEXEC_UID']) if 'PKEXEC_UID' in os.environ else None
            if args.check or args.pending:
                if not MANAGED.exists():
                    raise RuntimeError('This computer does not use managed updates.')
            if args.check:
                previous = read('status.json')
                due = time.time() * 1000 >= previous.get('nextCheck', 0)
                manual = args.force and time.time() * 1000 - previous.get('lastAttempt', 0) >= 60000
                if due or manual:
                    status(phase='checking', enabled=True, managed=True, error='', lastAttempt=int(time.time() * 1000))
                    stage()
                    status(lastChecked=int(time.time() * 1000), nextCheck=int((time.time() + WEEK) * 1000), failures=0)
                print(json.dumps(read('status.json')))
                return
            if args.version:
                stage(stable(args.version), args.package, uid)
            result = install(args.requester, uid)
            print(json.dumps({'installed': result, **read('status.json')}))
        except Exception as error:
            previous = read('status.json')
            failures = previous.get('failures', 0) + 1
            retry = time.time() + [3600, 21600, 86400][min(2, failures - 1)]
            if isinstance(error, urllib.error.HTTPError):
                header = error.headers.get('Retry-After')
                reset = error.headers.get('X-RateLimit-Reset')
                try:
                    retry = max(retry, time.time() + int(header) if header and header.isdecimal() else email.utils.parsedate_to_datetime(header).timestamp() if header else float(reset or 0))
                except (TypeError, ValueError):
                    pass
            status(phase='ready' if read('pending.json') else 'error', error=str(error), failures=failures, nextCheck=int(retry * 1000) if args.check else previous.get('nextCheck', 0))
            raise



if __name__ == '__main__':
    try:
        main()
    except BlockingIOError:
        print(json.dumps({'installed': False, 'error': 'Another update operation is running. Try again shortly.'}))
        sys.exit(1)
    except Exception as error:
        print(json.dumps({'installed': False, 'error': str(error)}))
        sys.exit(1)
