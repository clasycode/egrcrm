"""Before Django starts: move local storage by copying, never deleting the old DB."""
from contextlib import closing
import os,sqlite3,secrets,shutil,datetime,socket
from pathlib import Path
try:
    probe=socket.create_connection(('127.0.0.1',8000),timeout=0.3)
except OSError:
    pass
else:
    probe.close()
    raise SystemExit('CRM is still running on port 8000. Close the old CRM window before updating. No database was copied or migrated.')
root=Path(__file__).resolve().parent
data=Path(os.environ.get('CRM_DATA_DIR',str(root)))
data.mkdir(parents=True,exist_ok=True)
old=root/'dev.sqlite3';new=data/'dev.sqlite3'
if old.exists() and not new.exists() and old.resolve()!=new.resolve():
    with closing(sqlite3.connect(old)) as src,closing(sqlite3.connect(new)) as dst: src.backup(dst)
key=data/'.local-key'
if not key.exists():
    old_key=root/'.local-key'
    if old_key.exists() and old_key.resolve()!=key.resolve(): shutil.copy2(old_key,key)
    else: key.write_text(secrets.token_urlsafe(64),encoding='ascii')
if new.exists():
    backup=data/'backups';backup.mkdir(exist_ok=True)
    target=backup/('before-upgrade-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.sqlite3')
    with closing(sqlite3.connect(new)) as src,closing(sqlite3.connect(target)) as dst: src.backup(dst)
print('Data directory:',data)
print('Database and key are retained when the program folder is replaced.')
