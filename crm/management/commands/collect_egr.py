from datetime import date,timedelta
from django.core.management.base import BaseCommand,CommandError
from django.utils import timezone
from crm.jobs import enqueue_lookup,enqueue_day,enqueue_period,run_one
class Command(BaseCommand):
    help='Use the same durable queue as the web interface; process only the requested target.'
    def add_arguments(self,p):
        p.add_argument('--initialize',action='store_true');p.add_argument('--day',type=date.fromisoformat)
        p.add_argument('--limit',type=int,default=10);p.add_argument('--unp')
    def handle(self,*args,**o):
        if not 1<=o['limit']<=100: raise CommandError('limit must be 1..100')
        target=None
        if o['unp']:
            if not o['unp'].isdigit(): raise CommandError('УНП должен содержать цифры')
            target=enqueue_lookup(o['unp']).pk
        elif o['day']:
            if o['day']>=timezone.localdate(): raise CommandError('Выберите завершённый день')
            target=enqueue_day(o['day']).pk
        elif o['initialize']:
            enqueue_period(timezone.localdate()-timedelta(days=365),timezone.localdate()-timedelta(days=1),title='Начальная загрузка')
        for _ in range(o['limit']):
            if not run_one(target=target): break
        self.stdout.write('Порция обработана. Статус и ошибки — в разделе загрузки.')
