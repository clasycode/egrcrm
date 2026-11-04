import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from crm.models import CollectorSettings
from crm.jobs import run_one, schedule_due

class Command(BaseCommand):
    help = 'Process the durable UI queue and daily schedule. Keep running separately from web.'
    def add_arguments(self,p): p.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        self.stdout.write('EGR worker started. Ctrl+C to stop.')
        try:
            while True:
                close_old_connections()
                CollectorSettings.objects.get_or_create(pk=1)
                CollectorSettings.objects.filter(pk=1).update(heartbeat=timezone.now())
                schedule_due()
                worked = run_one()
                if options['once']: break
                time.sleep(1 if worked else 3)
        except KeyboardInterrupt: pass
