import datetime
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import uuid


def default_data_dir():
    override=os.environ.get('CRM_DATA_DIR')
    if override:return Path(override).expanduser().resolve()
    if os.name=='nt':
        base=os.environ.get('LOCALAPPDATA')
        if not base:raise RuntimeError('Не определена папка LOCALAPPDATA пользователя Windows.')
        return Path(base)/'RudyCortex'/'EGRCRM'
    return Path.home()/'.local'/'share'/'RudyCortex'/'EGRCRM'


def data_identity(directory):
    return hashlib.sha256(os.path.normcase(str(Path(directory).resolve())).encode()).hexdigest()[:24]


def sqlite_copy(source,target):
    source,target=Path(source),Path(target)
    temporary=target.with_name('.copy-'+uuid.uuid4().hex+'.sqlite3')
    try:
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(temporary) as dst:src.backup(dst)
        if target.exists():raise FileExistsError('Копия уже существует; перезапись отменена: '+str(target))
        temporary.chmod(0o600)
        temporary.replace(target)
    finally:
        if temporary.exists():temporary.unlink()


def backup_database(directory):
    directory=Path(directory)
    source=directory/'dev.sqlite3'
    if not source.exists():return None
    (directory/'backups').mkdir(parents=True,exist_ok=True)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    target=directory/'backups'/('before-upgrade-'+stamp+'.sqlite3')
    sqlite_copy(source,target)
    return target


def prepare_storage(root,directory,make_backup=False):
    root,directory=Path(root).resolve(),Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    source,target=root/'dev.sqlite3',directory/'dev.sqlite3'
    imported=False
    if source.exists() and not target.exists() and source!=target:
        sqlite_copy(source,target);imported=True
    key=directory/'.local-key'
    if not key.exists():
        legacy=root/'.local-key'
        if legacy.exists() and legacy!=key:
            with key.open('x',encoding='ascii') as stream:stream.write(legacy.read_text(encoding='ascii').strip())
        else:
            descriptor=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'w',encoding='ascii') as stream:stream.write(secrets.token_urlsafe(64))
        key.chmod(0o600)
    secret=key.read_text(encoding='ascii').strip()
    if not secret:raise RuntimeError('Локальный ключ пуст. Оригинал сохранён; обратитесь к журналу запуска.')
    backup=backup_database(directory) if make_backup else None
    return {'directory':directory,'database_exists':target.exists(),'imported':imported,'secret':secret,'backup':backup}
