"""Supervise both local processes, including Windows Ctrl+C cleanup."""
import subprocess
import sys
import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    help = 'Start local web and EGR queue worker together; not a production server.'
    def handle(self,*args,**options):
        if not settings.DEBUG: raise CommandError('run_local requires DEBUG=1')
        processes=[]
        try:
            for command in [['egr_worker'],['runserver','127.0.0.1:8000','--noreload']]:
                processes.append(subprocess.Popen([sys.executable,str(settings.BASE_DIR/'manage.py'),*command]))
            while all(p.poll() is None for p in processes): time.sleep(.5)
            raise CommandError('Один из процессов остановился; смотрите ошибку выше.')
        except KeyboardInterrupt: pass
        finally:
            for p in processes:
                if p.poll() is None:
                    p.terminate()
                    try: p.wait(timeout=5)
                    except subprocess.TimeoutExpired: p.kill(); p.wait()
