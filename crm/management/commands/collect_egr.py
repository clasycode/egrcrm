from datetime import timedelta, date
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from crm.models import ImportDay,SourceControl
from crm.egr import Client,SourceError,SourcePaused
from crm.importer import discover,enrich

class Command(BaseCommand):
    help = 'Bounded EGR worker; run repeatedly. No overlapping workers.'
    def add_arguments(self,p):
        p.add_argument('--initialize',action='store_true')
        p.add_argument('--day',type=date.fromisoformat)
        p.add_argument('--limit',type=int,default=10)
        p.add_argument('--unp')
    def handle(self,*args,**o):
        if not 1<=o['limit']<=100: raise CommandError('limit must be 1..100')
        SourceControl.objects.get_or_create(pk=1)
        now = timezone.now()
        # Atomic conditional update works on PostgreSQL and local SQLite.
        from django.db.models import Q
        locked = SourceControl.objects.filter(pk=1,paused=False).filter(Q(locked_until__isnull=True)|Q(locked_until__lt=now)).update(locked_until=now+timedelta(hours=3))
        if not locked: raise CommandError('Источник на паузе или сборщик уже запущен')
        try:
            client = Client()
            if o['unp']:
                if not o['unp'].isdigit(): raise CommandError('УНП должен содержать цифры')
                row = enrich(client,o['unp']); self.stdout.write(f'{row.unp}: admitted={bool(row.admitted_at)}'); return
            today = timezone.localdate()
            if o['initialize']:
                for n in range(1,366): ImportDay.objects.get_or_create(day=today-timedelta(days=n))
            if o['day']: ImportDay.objects.get_or_create(day=o['day'])
            day = ImportDay.objects.exclude(state='done').order_by('day').first()
            if not day: self.stdout.write('Нет ожидающих интервалов'); return
            try:
                if not day.discovered: discover(client,day)
                for item in day.candidates.exclude(state='done').order_by('pk')[:o['limit']]:
                    try:
                        enrich(client,item.unp)
                        item.state,item.error = 'done',''
                        item.save()
                    except SourceError as exc:
                        item.state,item.error = 'error',str(exc); item.save(); raise
                day.state = 'pending' if day.candidates.exclude(state='done').exists() else 'done'
                day.error = ''; day.save()
                self.stdout.write(f'{day.day}: {day.state}')
            except (SourceError,ValueError) as exc:
                day.state,day.error = 'error',str(exc); day.save()
                raise CommandError(str(exc)) from exc
        except SourceError as exc: raise CommandError(str(exc)) from exc
        finally:
            SourceControl.objects.filter(pk=1).update(locked_until=None)
