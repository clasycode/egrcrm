import logging,time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from crm.models import CollectorSettings
from crm.jobs import run_one,schedule_due
class Command(BaseCommand):
    def add_arguments(self,p): p.add_argument('--once',action='store_true')
    def handle(self,*args,**o):
        self.stdout.write('EGR worker started. Ctrl+C to stop.')
        try:
            while True:
                from crm.local_runtime import stop_requested
                if stop_requested(): break
                worked=False
                try:
                    close_old_connections()
                    config,_=CollectorSettings.objects.get_or_create(pk=1)
                    CollectorSettings.objects.filter(pk=1).update(heartbeat=timezone.now())
                    if config.backup_enabled and config.last_backup!=timezone.localdate():
                        from crm.backups import create_archive
                        try:
                            create_archive('daily')
                            CollectorSettings.objects.filter(pk=1).update(last_backup=timezone.localdate())
                        except Exception:
                            logging.exception('Daily backup failed')
                            CollectorSettings.objects.filter(pk=1).update(last_error='Ошибка резервной копии. Проверьте диск и журнал сервера.')
                    schedule_due();worked=run_one()
                    if stop_requested(): break
                    from crm.websites import run_search_once
                    run_search_once()
                except Exception:
                    logging.exception('Worker iteration failed; retry after 10 seconds')
                    try:
                        from crm.models import CollectionJob
                        CollectorSettings.objects.filter(pk=1).update(last_error='Ошибка цикла сборщика. Подробности в окне запуска / журнале сервера.')
                        CollectionJob.objects.filter(state='running').update(state='error',message='Внутренняя ошибка; требуется ручной повтор после проверки журнала',retry_at=None)
                    except Exception: logging.exception('Could not persist worker error')
                    if not o['once']: time.sleep(10)
                if o['once']: break
                time.sleep(1 if worked else 3)
        except KeyboardInterrupt: pass
