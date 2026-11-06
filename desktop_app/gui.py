"""Small local control panel; Django and dependencies run in a separate environment."""
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog
import uuid
import webbrowser
from .storage import default_data_dir, prepare_storage, backup_database
from .runtime import (URL, InstanceLock, running_status, port_in_use, dependency_runtime,
                      local_environment, run_logged, account_command, LocalProcesses)


class Application:
    def __init__(self):
        self.root=Path(__file__).resolve().parent.parent
        self.directory=default_data_dir();self.directory.mkdir(parents=True,exist_ok=True)
        self.events=queue.Queue();self.cancel=threading.Event();self.processes=None;self.lock=None;self.ready=False;self.closing=False
        self.window=tk.Tk();self.window.title('RudyCortex — CRM');self.window.geometry('650x430');self.window.minsize(600,410)
        self.window.configure(bg='#f5f2eb')
        tk.Label(self.window,text='RudyCortex',font=('Segoe UI',25),bg='#f5f2eb',fg='#282820').pack(anchor='w',padx=28,pady=(24,8))
        self.status=tk.StringVar(value='Подготавливаю запуск…')
        tk.Label(self.window,textvariable=self.status,font=('Segoe UI',11),wraplength=590,justify='left',bg='#f5f2eb').pack(anchor='w',padx=28,pady=8)
        panel=tk.Frame(self.window,bg='#f5f2eb');panel.pack(fill='x',padx=28,pady=12)
        self.buttons=[]
        for label,action in [('Открыть CRM',lambda:webbrowser.open(URL)),('Резервная копия',self.backup),('Восстановить доступ',self.reset)]:
            button=tk.Button(panel,text=label,command=action,padx=10,pady=9,state='disabled');button.pack(side='left',padx=(0,8));self.buttons.append(button)
        links=tk.Frame(self.window,bg='#f5f2eb');links.pack(fill='x',padx=28)
        for label,path in [('Журналы',self.directory/'logs'),('Папка данных',self.directory)]:
            tk.Button(links,text=label,command=lambda p=path:self.open_folder(p)).pack(side='left',padx=(0,8))
        self.output=tk.Text(self.window,height=5,font=('Consolas',9),state='disabled',bg='#eeece5',relief='flat');self.output.pack(fill='both',expand=True,padx=28,pady=12)
        self.stop=tk.Button(self.window,text='Остановить и закрыть',command=self.close,pady=7);self.stop.pack(anchor='e',padx=28,pady=(0,12))
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.log=(self.directory/'logs');self.log.mkdir(exist_ok=True)
        self.window.after(100,self.poll)
        self.starter=threading.Thread(target=self.start,daemon=True);self.starter.start()
    def emit(self,kind,value):self.events.put((kind,value))
    def start(self):
        try:
            if running_status(self.directory):self.emit('existing',None);return
            self.lock=InstanceLock(self.directory/'.desktop.lock');self.lock.acquire()
            if port_in_use():raise RuntimeError('Порт 8000 занят. Закройте прежний запуск CRM и повторите запуск.')
            state=prepare_storage(self.root,self.directory)
            self.python=dependency_runtime(self.root,self.directory,self.emit,self.cancel)
            self.env=local_environment(self.directory,state['secret'],self.directory/('.stop-'+uuid.uuid4().hex))
            self.emit('progress','Проверяю базу данных…')
            info=account_command(self.python,self.root,self.env,'inspect')
            if info['pending']:
                backup_database(self.directory)
                run_logged([self.python,'manage.py','migrate','--noinput'],self.root,self.env,self.emit,self.cancel)
            run_logged([self.python,'manage.py','collectstatic','--noinput','--verbosity','0'],self.root,self.env,self.emit,self.cancel)
            info=account_command(self.python,self.root,self.env,'inspect')
            if not info['owners']:
                self.account_done=threading.Event();self.account_ok=False
                self.emit('create',None)
                while not self.account_done.wait(.2):
                    if self.cancel.is_set():return
                if not self.account_ok:raise RuntimeError('Создание владельца отменено. Запустите программу снова, когда будете готовы.')
            if self.cancel.is_set():return
            self.emit('progress','Запускаю CRM и сборщик…')
            self.processes=LocalProcesses(self.root,self.directory,self.python,self.env);self.processes.start()
            if not self.cancel.is_set():self.emit('ready',None)
        except Exception as error:self.emit('error',str(error))
    def account(self,action):
        owners=account_command(self.python,self.root,self.env,'inspect')['owners'] if action=='reset' else []
        prompt='Логин владельца:' + ('\nДоступные владельцы: '+', '.join(owners) if owners else '')
        username=simpledialog.askstring('Учётная запись',prompt,parent=self.window)
        if username is None:return False
        while True:
            password=simpledialog.askstring('Учётная запись','Новый пароль (не менее 8 символов):',show='•',parent=self.window)
            if password is None:return False
            confirmation=simpledialog.askstring('Учётная запись','Повторите пароль:',show='•',parent=self.window)
            if confirmation is None:return False
            if password!=confirmation:messagebox.showerror('Пароль','Пароли не совпали.',parent=self.window);continue
            result=account_command(self.python,self.root,self.env,action,{'username':username,'password':password})
            if result.get('ok'):return True
            messagebox.showerror('Учётная запись',result.get('error','Не удалось сохранить.'),parent=self.window)
    def reset(self):
        try:
            if self.account('reset'):messagebox.showinfo('Доступ восстановлен','Войдите в CRM с новым паролем.',parent=self.window)
        except Exception as error:messagebox.showerror('Ошибка',str(error),parent=self.window)
    def backup(self):
        self.buttons[1].configure(state='disabled')
        def work():
            try:
                run_logged([self.python,'manage.py','backup_crm'],self.root,self.env,self.emit,self.cancel)
                self.emit('backup',None)
            except Exception as error:self.emit('error',str(error))
        threading.Thread(target=work,daemon=True).start()
    def open_folder(self,path):
        path.mkdir(parents=True,exist_ok=True)
        if os.name=='nt':os.startfile(path)
        else:subprocess.Popen(['xdg-open',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def poll(self):
        try:
            while True:
                kind,value=self.events.get_nowait()
                if kind=='progress':self.status.set(value)
                elif kind=='log':
                    with (self.log/'launcher.log').open('a',encoding='utf-8') as stream:stream.write(value+'\n')
                    self.output.configure(state='normal');self.output.insert('end',value+'\n');self.output.see('end');self.output.configure(state='disabled')
                elif kind=='create':
                    try:self.account_ok=self.account('create')
                    except Exception as error:messagebox.showerror('Ошибка',str(error),parent=self.window)
                    finally:self.account_done.set()
                elif kind=='ready':
                    self.ready=True;self.status.set('CRM работает. Браузер можно закрыть — сбор продолжится. Для завершения используйте кнопку ниже.')
                    for button in self.buttons:button.configure(state='normal')
                    webbrowser.open(URL)
                elif kind=='existing':
                    webbrowser.open(URL);messagebox.showinfo('CRM уже работает','CRM открыта в браузере. Управление — в прежнем окне программы.',parent=self.window);self.window.destroy();return
                elif kind=='backup':
                    self.buttons[1].configure(state='normal');self.open_folder(self.directory/'backups')
                elif kind=='error':
                    self.status.set(value)
                    with (self.log/'launcher.log').open('a',encoding='utf-8') as stream:stream.write(value+'\n')
                elif kind=='closed':self.window.destroy();return
        except queue.Empty:pass
        if self.ready and not self.closing and any(p.poll() is not None for p in self.processes.children.values()):
            self.ready=False;self.status.set('CRM или сборщик остановились. Проверьте журналы, затем закройте и снова запустите программу.')
            for button in self.buttons:button.configure(state='disabled')
        self.window.after(150,self.poll)
    def cleanup(self):
        if self.processes:self.processes.stop()
        if self.lock:self.lock.close()
    def close(self):
        if self.closing:return
        self.closing=True;self.cancel.set();self.status.set('Останавливаю сбор и сохраняю работу. Дождитесь закрытия окна…');self.stop.configure(state='disabled')
        def finish():
            self.starter.join()
            self.cleanup();self.emit('closed',None)
        threading.Thread(target=finish,daemon=True).start()
    def run(self):self.window.mainloop()


def main():
    # Keep the startup thread available for an orderly shutdown.
    app=Application();app.run()
