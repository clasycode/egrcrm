"""Durable queue shared by web, Windows worker and server deployment."""
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from .models import CollectionJob, CollectorSettings, ImportDay, SourceControl, Candidate
from .importer import discover, enrich
from .egr import Client, SourceError
from .leases import acquire, release

def enqueue_lookup(unp):
    job,created = CollectionJob.objects.get_or_create(key='unp:'+unp, defaults={'kind':'unp','unp':unp})
    if not created and job.state in ('done','error'):
        job.state,job.message = 'pending',''
        job.save(update_fields=['state','message','updated_at'])
    return job

def enqueue_day(day,refresh=False):
    interval,_ = ImportDay.objects.get_or_create(day=day)
    job,created = CollectionJob.objects.get_or_create(key='day:'+day.isoformat(),defaults={'kind':'day','day':interval})
    if not created and (job.state == 'error' or (job.state == 'done' and refresh)):
        if refresh:
            interval.discovered = False
            interval.candidates.update(state='pending',error='')
        interval.state,interval.error = 'pending',''
        interval.save()
        job.state,job.message = 'pending',''
        job.save(update_fields=['state','message','updated_at'])
    return job

def schedule_due(now=None):
    now = timezone.localtime(now or timezone.now())
    with transaction.atomic():
        config,_ = CollectorSettings.objects.get_or_create(pk=1)
        config = CollectorSettings.objects.select_for_update().get(pk=1)
        if config.enabled and config.last_scheduled != now.date() and now.time().replace(tzinfo=None) >= config.run_at:
            for n in range(1,config.lookback+1): enqueue_day(now.date()-timedelta(days=n),refresh=True)
            config.last_scheduled = now.date()
            config.save(update_fields=['last_scheduled'])

def run_one():
    token = acquire()
    if not token: return False
    job = None
    try:
        # An expired process lease permits recovery after interrupted requests.
        CollectionJob.objects.filter(state='running').update(state='pending',message='Продолжение после остановки сборщика')
        job = CollectionJob.objects.filter(state='pending').order_by('-kind','created_at','pk').first()
        if not job: return False
        job.state = 'running'; job.current_unp = job.unp if job.kind == 'unp' else ''; job.save(update_fields=['state','current_unp','updated_at'])
        client = Client(lease=token)
        if job.kind == 'unp':
            company = enrich(client,job.unp)
            job.company = company
            job.message = 'Карточка добавлена/обновлена в CRM' if company.admitted_at else 'Сведения получены, но подходящих контактов нет. В рабочую CRM не включён.'
            job.state = 'done'
        else:
            day = job.day
            if not day.discovered: discover(client,day)
            # Each invocation enriches one candidate; the UI stays responsive.
            item = day.candidates.exclude(state='done').order_by('pk').first()
            if item:
                job.current_unp = item.unp
                job.message = f'Получение сведений по УНП {item.unp}'
                job.save(update_fields=['current_unp','message','updated_at'])
                try:
                    enrich(client,item.unp)
                    item.state,item.error = 'done',''; item.save()
                except (SourceError,ValueError) as exc:
                    item.state,item.error = 'error',str(exc); item.save(); raise
            remaining = day.candidates.exclude(state='done').exists()
            day.state,day.error = ('pending' if remaining else 'done'),''; day.save()
            job.state = 'pending' if remaining else 'done'
            job.message = f'Обработано {day.candidates.filter(state="done").count()} из {day.candidates.count()} УНП'
        job.current_unp = ''
        job.save()
        return True
    except (SourceError,ValueError) as exc:
        if job:
            job.state,job.message = 'error',str(exc); job.save()
            if job.day:
                job.day.state,job.day.error = 'error',str(exc); job.day.save()
        return True
    finally:
        release(token)
