from datetime import date,timedelta
from django.core.management.base import BaseCommand,CommandError
from crm.egr import Client
from crm.leases import acquire,release
from crm.importer import local_date
class Command(BaseCommand):
    help='Compare bounded period and daily candidate sets before enabling batch optimizations.'
    def add_arguments(self,p): p.add_argument('start',type=date.fromisoformat);p.add_argument('end',type=date.fromisoformat)
    def handle(self,*a,**o):
        start,end=o['start'],o['end']
        if not 0<=(end-start).days<=6: raise CommandError('Choose 1..7 days')
        token=acquire()
        if not token: raise CommandError('Collector busy or paused')
        try:
            client=Client(lease=token)
            def ids(records): return {str(r['ngrn']) for r in records if (r.get('nsi00208') or {}).get('nkscrt')==1 and (r.get('nsi00211') or {}).get('nkvob') in (1,2) and local_date(r.get('dfrom')) and start<=local_date(r['dfrom'])<=end}
            batch=ids(client.get('getBaseInfoByPeriod',start.strftime('%d.%m.%Y'),end.strftime('%d.%m.%Y')))
            daily=set();day=start
            while day<=end:
                daily|=ids(client.get('getBaseInfoByPeriod',day.strftime('%d.%m.%Y'),day.strftime('%d.%m.%Y')));day+=timedelta(days=1)
            self.stdout.write(f'Daily={len(daily)}, period={len(batch)}; missing={sorted(daily-batch)}; extra={sorted(batch-daily)}')
            if daily!=batch: raise CommandError('Sets differ; batch optimization must stay disabled')
        finally: release(token)
