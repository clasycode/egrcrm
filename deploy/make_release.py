"""Package source from a ZIP checkout without Git or any credentials/database."""
import argparse
import hashlib
from pathlib import Path
import tarfile

ROOT_FILES=('manage.py','requirements.txt')
ROOT_DIRS=('config','crm','templates','deploy')
EXCLUDED={'.git','.venv','__pycache__','data','backups','staticfiles','node_modules'}


def package(root,target):
    if target.resolve().is_relative_to(root.resolve()):raise ValueError('Release archive must be outside the project directory')
    with tarfile.open(target,'w:gz') as archive:
        for relative in ROOT_FILES:
            path=root/relative
            if not path.is_file() or path.is_symlink():raise ValueError('Required source file is unavailable: '+relative)
            archive.add(path,arcname=relative,recursive=False)
        for folder in ROOT_DIRS:
            base=root/folder
            if not base.is_dir() or base.is_symlink():raise ValueError('Required source directory is unavailable: '+folder)
            for path in sorted(base.rglob('*')):
                relative=path.relative_to(root)
                if any(part in EXCLUDED for part in relative.parts):continue
                if path.is_symlink():raise ValueError('Source symlinks are not supported: '+str(relative))
                if not path.is_file():continue
                if path.name.startswith('.env') or path.name in ('.local-key','id_rsa','id_ed25519','server_key') or path.suffix.lower() in ('.pyc','.sqlite3','.sqlite','.db','.ppk','.pem','.key','.p12','.pfx'):continue
                archive.add(path,arcname=relative.as_posix(),recursive=False)
    return hashlib.sha256(target.read_bytes()).hexdigest()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('target',type=Path);parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent)
    args=parser.parse_args()
    print(package(args.root.resolve(),args.target))
