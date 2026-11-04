from datetime import timedelta
from django.db.models import Count, Q
from django.utils import timezone
from .models import Candidate, Company, ImportDay, CollectionJob, CollectorSettings, SourceControl

def collection_progress():
    admitted = Company.objects.filter(admitted_at__isnull=False)
    admitted_unps = admitted.values('unp')
    counts = {row['day_id']:row for row in Candidate.objects.values('day_id').annotate(
        total=Count('pk'),completed=Count('pk',filter=Q(state='done')),
        errors=Count('pk',filter=Q(state='error')),
        ready=Count('pk',filter=Q(state='done',unp__in=admitted_unps)))}
    days=[]
    for day in ImportDay.objects.order_by('-day')[:100]:
        stats=counts.get(day.pk,{'total':0,'completed':0,'errors':0,'ready':0})
        days.append({'id':day.pk,'date':day.day.strftime('%d.%m.%Y'),'state':day.state,
                     'discovered':day.discovered,'error':day.error,
                     **{key:stats[key] for key in ('total','completed','errors','ready')}})
    active=[{'id':job.pk,'unp':job.current_unp or job.unp,
             'day':job.day.day.strftime('%d.%m.%Y') if job.day else '',
             'message':job.message} for job in CollectionJob.objects.filter(state='running').select_related('day')]
    config=CollectorSettings.objects.filter(pk=1).first()
    source=SourceControl.objects.filter(pk=1).first()
    result = {'candidates':Candidate.objects.values('unp').distinct().count(),
            'processed':Candidate.objects.filter(state='done').values('unp').distinct().count(),
            'crm':admitted.count(),'without_contacts':Company.objects.filter(admitted_at__isnull=True).count(),
            'days_done':ImportDay.objects.filter(state='done').count(),'days_total':ImportDay.objects.count(),
            'pending_jobs':CollectionJob.objects.filter(state__in=['pending','running']).count(),
            'error_jobs':CollectionJob.objects.filter(state='error').count(),
            'online':bool(config and config.heartbeat and timezone.now()-config.heartbeat<timedelta(minutes=15)),
            'paused':bool(source and source.paused),'reason':source.reason if source else '',
            'active':active,'days':days,'as_of':timezone.localtime().strftime('%H:%M:%S')}

    if result['paused']:
        result['status'] = 'Источник на паузе: '+result['reason']
    elif not result['online']:
        result['status'] = 'Сборщик не отвечает — перезапустите start-windows.bat'
    elif result['active']:
        result['status'] = 'Идёт сбор данных'
    elif result['pending_jobs']:
        result['status'] = 'Задания в очереди — сборщик ожидает следующего запроса'
    elif result['error_jobs']:
        result['status'] = 'Сбор остановлен на ошибках. Проверьте задания и повторите после устранения причины.'
    else:
        result['status'] = 'Сборщик запущен и ожидает задания. Выберите период в разделе загрузки — активного сбора сейчас нет.'
    return result
