import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import socket
import subprocess
import sys
import time
import urllib.request
from .storage import data_identity

URL='http://127.0.0.1:8000'


def running_status(directory):
    try:
        request=urllib.request.Request(URL+'/local/status/',headers={'Accept':'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=1) as response:
            data=json.loads(response.read(8192))
        return data if data.get('application')=='egrcrm' and data.get('data_id')==data_identity(directory) else None
    except (OSError,ValueError):return None


def port_in_use():
    try:
        with socket.create_connection(('127.0.0.1',8000),timeout=.3):return True
    except OSError:return False


class AlreadyRunning(Exception):pass
class Cancelled(Exception):pass


class InstanceLock:
    def __init__(self,path):self.path=Path(path);self.stream=None
    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        stream=self.path.open('a+b');stream.seek(0)
        if not stream.read(1):stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            stream.close();raise AlreadyRunning('CRM уже запускается или работает. Используйте открытое окно программы.')
        self.stream=stream
    def close(self):
        if self.stream:self.stream.close();self.stream=None


def console_python():
    if os.name=='nt':
        candidate=Path(sys.executable).with_name('python.exe')
        if candidate.exists():return str(candidate)
    return sys.executable


def local_environment(directory,secret,stop_file):
    env=os.environ.copy()
    env.update(CRM_LOCAL_MODE='1',CRM_DATA_DIR=str(directory),DJANGO_SECRET_KEY=secret,DEBUG='0',POSTGRES_HOST='',TRUST_PROXY_HTTPS='0',PYTHONUNBUFFERED='1',PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',CRM_LOCAL_STOP_FILE=str(stop_file))
    return env


def dependency_runtime(root,directory,emit,cancel):
    requirements=Path(root)/'requirements.txt'
    digest=hashlib.sha256(requirements.read_bytes()).hexdigest()
    tag=f'py{sys.version_info.major}{sys.version_info.minor}-'+digest[:12]
    home=Path(directory)/'runtime'/tag
    python=home/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    marker=home/'.ready'
    if not python.exists():
        emit('progress','Подготавливаю среду Python…')
        home.parent.mkdir(parents=True,exist_ok=True)
        run_logged([console_python(),'-m','venv',str(home)],root,None,emit,cancel)
    if not marker.exists() or marker.read_text()!=digest:
        emit('progress','Первый запуск: устанавливаю компоненты. Это может занять несколько минут…')
        run_logged([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(requirements)],root,None,emit,cancel)
        run_logged([str(python),'-c','import django, waitress, phonenumbers, openpyxl'],root,None,emit,cancel)
        marker.write_text(digest)
    return str(python)


def creation_options():
    return {'creationflags':subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {}


def run_logged(command,root,env,emit,cancel,input_text=None):
    process=subprocess.Popen(command,cwd=root,env=env,stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',**creation_options())
    if input_text is not None:
        process.stdin.write(input_text);process.stdin.close()
    output=[];lines=queue.Queue()
    def read():
        try:
            for line in process.stdout:lines.put(line)
        finally:lines.put(None)
    reader=threading.Thread(target=read,daemon=True);reader.start()
    try:
        while True:
            if cancel.is_set():raise Cancelled()
            try:line=lines.get(timeout=.2)
            except queue.Empty:continue
            if line is None:break
            output.append(line);emit('log',line.rstrip())
        code=process.wait()
        if cancel.is_set():raise Cancelled()
        if code:raise RuntimeError('Не удалось выполнить шаг подготовки. Подробности — в журнале запуска.')
        return ''.join(output)
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        reader.join(timeout=2);process.stdout.close()


def account_command(python,root,env,action,payload=None):
    # Password travels through stdin, never command arguments, logs or a file.
    result=subprocess.run([python,'manage.py','desktop_account',action],cwd=root,env=env,input=json.dumps(payload or {}),text=True,encoding='utf-8',stdout=subprocess.PIPE,stderr=subprocess.PIPE,**creation_options())
    if result.returncode:raise RuntimeError('Не удалось проверить учётную запись. Смотрите журнал запуска.')
    return json.loads(result.stdout)


class LocalProcesses:
    def __init__(self,root,directory,python,env):
        self.root=Path(root);self.directory=Path(directory);self.python=python;self.env=env;self.children={};self.streams=[];self.job=None
    def start(self):
        if os.name=='nt':
            from .windows_job import ProcessJob
            self.job=ProcessJob()
        logs=self.directory/'logs';logs.mkdir(exist_ok=True)
        try:
            for name,command in [('web',['serve_local']),('worker',['egr_worker'])]:
                stream=(logs/(name+'.log')).open('a',encoding='utf-8');self.streams.append(stream)
                process=subprocess.Popen([self.python,'manage.py',*command],cwd=self.root,env=self.env,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,**creation_options())
                self.children[name]=process
                if self.job:self.job.assign(process)
            for _ in range(100):
                if any(p.poll() is not None for p in self.children.values()):raise RuntimeError('Не удалось запустить CRM или сборщик. Откройте журналы в окне программы.')
                if running_status(self.directory):return
                time.sleep(.3)
            raise RuntimeError('CRM не ответила при запуске. Откройте журналы и повторите запуск.')
        except Exception:self.stop();raise
    def stop(self):
        Path(self.env['CRM_LOCAL_STOP_FILE']).touch()
        worker=self.children.get('worker')
        if worker and worker.poll() is None:
            try:worker.wait(timeout=40)
            except subprocess.TimeoutExpired:worker.terminate()
        for process in self.children.values():
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
            else:process.wait()
        if self.job:self.job.close();self.job=None
        for stream in self.streams:stream.close()
        self.streams=[];self.children={}
        Path(self.env['CRM_LOCAL_STOP_FILE']).unlink(missing_ok=True)
