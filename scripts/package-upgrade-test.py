"""Real installer upgrade on disposable CI runners/containers only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request

if os.environ.get('GITHUB_ACTIONS') != 'true' and not (Path('/.dockerenv').exists() and os.environ.get('ALSHITASKS_ISOLATED_UPGRADE') == '1'):
    raise SystemExit('Requires a disposable CI runner or explicitly isolated container')
windows = sys.platform == 'win32'
new = Path(sys.argv[1]).resolve()
repo = 'https://api.github.com/repos/Alshival-Ai/alshitasks/releases/latest'
headers = {'User-Agent': 'Alshival-upgrade-acceptance', 'Accept': 'application/vnd.github+json'}
token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
if token:
    headers['Authorization'] = 'Bearer ' + token
with urllib.request.urlopen(urllib.request.Request(repo, headers=headers), timeout=30) as response:
    release = json.load(response)
version = release['tag_name'].removeprefix('v')
name = f'alshitasks_{version}_x64_setup.exe' if windows else f'alshitasks_{version}_amd64.deb'
assets = {row['name']: row['browser_download_url'] for row in release['assets']}


def install(package, target):
    if windows:
        subprocess.run([str(package), '/S', '--updated', '/D=' + str(target)], check=True, timeout=600)
    else:
        subprocess.run(['sudo', 'apt-get', '-o', 'DPkg::Lock::Timeout=120', '--no-remove', '--no-install-recommends', 'install', '-y', str(package)], check=True, timeout=900)


with tempfile.TemporaryDirectory(prefix='alshitasks-package-upgrade-') as temporary:
    directory = Path(temporary)
    old = directory / name
    urllib.request.urlretrieve(assets[name], old)
    with urllib.request.urlopen(assets['SHA256SUMS'], timeout=30) as response:
        sums = response.read().decode()
    expected = [line.split()[0] for line in sums.splitlines() if len(line.split()) == 2 and line.split()[1] == name]
    with old.open('rb') as stream:
        assert len(expected) == 1 and hashlib.file_digest(stream, 'sha256').hexdigest() == expected[0]
    target = directory / 'Alshival.Ai' if windows else Path('/usr/lib/alshitasks')
    install(old, target)
    installed_app = target / 'resources/app' if windows else target
    python = target / 'resources/python/python.exe' if windows else Path('/usr/bin/python3')
    data = directory / 'user-data'
    program = '''
import json,sys,uuid
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from alshitasks.paths import Paths
from alshitasks.store import Store
paths=Paths(sys.argv[2]); store=Store(paths)
if sys.argv[3]=='seed':
    item=store.create({'kind':'task','data':{'title':'Offline upgrade fixture'},'idempotency_key':str(uuid.uuid4())})
    store.set_setting('upgrade_fixture', {'preference': 'retained'})
    store.db.execute('INSERT INTO outbox(id,connection,item_id,base,payload,state,reason) VALUES(?,?,?,?,?,?,?)',
        (str(uuid.uuid4()),'fixture-connection',item['id'],'{}','{"title":"Unsent edit"}','queued',''))
    store.db.commit()
    (paths.attachments/'upgrade-fixture.txt').write_text('Keep this attachment')
result={'items':[tuple(r) for r in store.db.execute('SELECT id,revision,data FROM items ORDER BY id')],
        'outbox':[tuple(r) for r in store.db.execute('SELECT * FROM outbox ORDER BY id')],
        'preference':store.setting('upgrade_fixture'), 'attachment':(paths.attachments/'upgrade-fixture.txt').read_text(),
        'integrity':store.db.execute('PRAGMA quick_check').fetchone()[0]}
print(json.dumps(result)); store.db.close()
'''
    def snapshot(mode):
        return json.loads(subprocess.check_output([str(python), '-c', program, str(installed_app / 'service'), str(data), mode], text=True))
    before = snapshot('seed')
    if windows:
        import winreg
        startup_key = r'Software\Microsoft\Windows\CurrentVersion\Run'
        startup_value = '"' + str(target / 'Alshival.Ai.exe') + '" --start-in-tray'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, startup_key) as key:
            winreg.SetValueEx(key, 'Alshival.Ai', 0, winreg.REG_SZ, startup_value)
    install(new, target)
    after = snapshot('read')
    assert before == after, 'Upgrade changed user records, queued edits, preferences or files'
    assert after['integrity'] == 'ok'
    if windows:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup_key) as key:
            assert winreg.QueryValueEx(key, 'Alshival.Ai')[0] == startup_value, 'Upgrade lost start-at-login preference'
    assert (installed_app / 'desktop/updates.cjs').exists()
    if not windows:
        assert Path('/usr/share/polkit-1/actions/ai.alshival.alshitasks.policy').exists()
        assert os.access(target / 'desktop/update-install', os.X_OK)
    print('PASS: real package upgrade retains task identities/revisions, offline outbox, preferences, attachments and SQLite integrity')
