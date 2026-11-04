"""One queue for web/CLI, isolated candidates, bounded retries and durable run history."""
from datetime import timedelta
import time
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import CollectionJob,CollectorSettings,ImportDay,SourceControl,Candidate,Company,CollectionRun,RunDay,RunCompany,JobAttempt
from .importer import discover,enrich
from .egr import Client,SourceError,SourceCancelled
from .leases import acquire,release,assert_current,is_current

MAX_ATTEMPTS=3

def enqueue_lookup(unp,priority=0):
    job,created=CollectionJob.objects.get_or_create(key='unp:'+unp,defaults={'kind':'unp','unp':unp,'priority':priority})
    if job.priority!=priority:
        job.priority=priority;job.save(update_fields=['priority'])
    if not created and job.state in ('done','error','cancelled'):
        job.state,job.message,job.attempts,job.retry_at='pending','',0,None
        job.save(update_fields=['state','message','attempts','retry_at','updated_at'])
    return job

def enqueue_day(day,refresh=False):
    interval,_=ImportDay.objects.get_or_create(day=day)
    job,created=CollectionJob.objects.get_or_create(key='day:'+day.isoformat(),defaults={'kind':'day','day':interval,'priority':10})
    if not created and (job.state in ('error','cancelled') or interval.state=='partial' or (job.state=='done' and refresh)):
        if refresh:
            interval.discovered=False
            interval.candidates.update(state='pending',error='',attempts=0,retry_at=None)
        else:
            interval.candidates.filter(state__in=['cancelled','error']).update(state='pending',error='',attempts=0,retry_at=None)
        interval.state,interval.error='pending','';interval.save()
        job.state,job.message,job.attempts,job.retry_at='pending','',0,None;job.save()
    return job

def enqueue_period(start,end,actor=None,title=None,refresh_recent=False):
    run=CollectionRun.objects.create(title=title or f'{start:%d.%m.%Y}–{end:%d.%m.%Y}',actor=actor)
    day=start
    while day<=end:
        depth=7 if refresh_recent is True else int(refresh_recent or 0)
        job=enqueue_day(day,refresh=bool(depth and day>=timezone.localdate()-timedelta(days=depth)))
        RunDay.objects.create(run=run,day=job.day)
        day+=timedelta(days=1)
    update_run(run)
    return run

def update_run(run):
    if run.finished_at: return
    ids=run.days.values('day_id')
    candidates=Candidate.objects.filter(day_id__in=ids)
    ready=Company.objects.filter(admitted_at__isnull=False,unp__in=candidates.filter(state='done').values('unp'))
    missing=ready.exclude(pk__in=run.companies.values('company_id')).values_list('pk',flat=True)
    RunCompany.objects.bulk_create([RunCompany(run=run,company_id=pk) for pk in missing],ignore_conflicts=True,batch_size=500)
    days=ImportDay.objects.filter(pk__in=ids)
    if run.progress_at and timezone.now()-run.progress_at<timedelta(seconds=10) and days.exclude(state__in=['done','partial','cancelled']).exists(): return
    counts={'found':candidates.values('unp').distinct().count(),'processed':candidates.filter(state='done').values('unp').distinct().count(),'ready':run.companies.count(),'errors':candidates.filter(state='error').count(),'days':days.count(),'done':days.filter(state='done').count()}
    if days.exists() and not days.exclude(state__in=['done','partial','cancelled']).exists():
        run.state='cancelled' if days.filter(state='cancelled').exists() else ('partial' if counts['errors'] else 'done')
        run.finished_at=timezone.now()
    else: run.state='running'
    run.snapshot=counts;run.progress_at=timezone.now();run.save(update_fields=['state','finished_at','snapshot','progress_at'])

def update_runs(day):
    for run in CollectionRun.objects.filter(days__day=day,finished_at__isnull=True): update_run(run)

def schedule_due(now=None):
    now=timezone.localtime(now or timezone.now())
    with transaction.atomic():
        config,_=CollectorSettings.objects.get_or_create(pk=1)
        config=CollectorSettings.objects.select_for_update().get(pk=1)
        if config.enabled and config.last_scheduled!=now.date() and now.time().replace(tzinfo=None)>=config.run_at:
            start=now.date()-timedelta(days=config.lookback)
            if config.last_scheduled: start=min(start,config.last_scheduled)
            enqueue_period(start,now.date()-timedelta(days=1),title='Автоматическое обновление '+now.strftime('%d.%m.%Y'),refresh_recent=config.lookback)
            config.last_scheduled=now.date();config.save(update_fields=['last_scheduled'])
        for row in Company.objects.filter(admitted_at__isnull=True,next_contact_check__lte=now,contact_retry_stage__lt=2).order_by('next_contact_check')[:100]:
            existing=CollectionJob.objects.filter(key='unp:'+row.unp,state__in=['pending','running']).exists()
            if existing: continue
            enqueue_lookup(row.unp,priority=5)
            row.contact_retry_stage+=1
            row.next_contact_check=row.first_no_contacts+timedelta(days=30) if row.contact_retry_stage==1 else None
            row.save(update_fields=['contact_retry_stage','next_contact_check'])

def _finish_day(job):
    day=job.day
    candidates=day.candidates.all()
    pending=candidates.filter(state='pending').exists()
    retry=candidates.filter(state='error',attempts__lt=MAX_ATTEMPTS)
    if pending:
        job.state,job.retry_at='pending',None;day.state='pending'
    elif retry.exists():
        job.state,job.retry_at='pending',retry.order_by('retry_at').first().retry_at;day.state='pending'
    else:
        errors=candidates.filter(state='error').exists()
        job.state,job.retry_at='done',None;day.state='partial' if errors else 'done'
    job.message=f'Обработано {candidates.filter(state="done").count()} из {candidates.count()} УНП; ошибок: {candidates.filter(state="error").count()}'
    job.current_unp='';job.save()
    day.error='' if not candidates.filter(state='error').exists() else 'Ошибочные компании изолированы. Остальные обработаны; доступен ручной повтор.'
    day.save();update_runs(day)

def run_one(target=None):
    token=acquire()
    if not token: return False
    job=None
    started=time.monotonic()
    try:
        with transaction.atomic():
            assert_current(token,lock=True)
            CollectionJob.objects.filter(state='running').update(state='pending',current_unp='',message='Продолжение после остановки сборщика')
            due=Q(retry_at__isnull=True)|Q(retry_at__lte=timezone.now())
            jobs=CollectionJob.objects.filter(due).filter(Q(state='pending')|Q(state='error',attempts__lt=MAX_ATTEMPTS,retry_at__isnull=False))
            if target: jobs=jobs.filter(pk=target)
            job=jobs.order_by('priority','created_at','pk').first()
            if not job: return False
            job.state='running';job.current_unp=job.unp if job.kind=='unp' else '';job.save()
        client=Client(lease=token)
        if job.kind=='unp':
            company=enrich(client,job.unp)
            with transaction.atomic():
                assert_current(token,lock=True)
                job.company=company;job.state='done';job.current_unp='';job.retry_at=None
                job.message='Карточка добавлена/обновлена в CRM' if company.admitted_at else 'Сведения получены, но подходящих контактов нет. В рабочую CRM не включён.'
                job.save();JobAttempt.objects.create(job=job,unp=job.unp,state='done',elapsed_seconds=time.monotonic()-started)
        else:
            day=job.day
            if not day.discovered: discover(client,day)
            item=day.candidates.filter(Q(state='pending')|Q(state='error',attempts__lt=MAX_ATTEMPTS,retry_at__lte=timezone.now())).order_by('pk').first()
            if item:
                with transaction.atomic():
                    assert_current(token,lock=True)
                    job.current_unp=item.unp;job.message=f'Получение сведений по УНП {item.unp}';job.save()
                try:
                    enrich(client,item.unp)
                except SourceCancelled: raise
                except (SourceError,ValueError) as exc:
                    with transaction.atomic():
                        if is_current(token,lock=True,allow_paused=True):
                            item.attempts+=1;item.state='error';item.error=str(exc)
                            item.retry_at=timezone.now()+timedelta(seconds=30*2**(item.attempts-1)) if item.attempts<MAX_ATTEMPTS else None
                            item.save();JobAttempt.objects.create(job=job,unp=item.unp,state='error',message=str(exc))
                            _finish_day(job)
                    return True
            with transaction.atomic():
                assert_current(token,lock=True)
                if item:
                    item.state='done';item.error='';item.retry_at=None;item.save()
                    JobAttempt.objects.create(job=job,unp=item.unp,state='done',elapsed_seconds=time.monotonic()-started)
                _finish_day(job)
        return True
    except SourceCancelled:
        if job: CollectionJob.objects.filter(pk=job.pk,state='running').update(state='pending',current_unp='')
        return False
    except (SourceError,ValueError) as exc:
        if job:
            with transaction.atomic():
                if is_current(token,lock=True,allow_paused=True):
                    job.attempts+=1;job.state='error';job.message=str(exc)
                    job.retry_at=timezone.now()+timedelta(seconds=30*2**(job.attempts-1)) if job.attempts<MAX_ATTEMPTS else None
                    job.save();JobAttempt.objects.create(job=job,unp=job.current_unp,state='error',message=str(exc))
                    if job.day: job.day.state='error';job.day.error=str(exc);job.day.save()
        return True
    finally: release(token)
