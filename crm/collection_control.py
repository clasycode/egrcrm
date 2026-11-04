from django.db import transaction
from .models import SourceControl, CollectorSettings, CollectionJob, ImportDay, Candidate, CollectionReset,CollectionRun

def reset_collection(actor):
    """Cancel outstanding work. Never delete source history or CRM/manual data."""
    with transaction.atomic():
        SourceControl.objects.get_or_create(pk=1)
        source=SourceControl.objects.select_for_update().get(pk=1)
        CollectorSettings.objects.get_or_create(pk=1)
        config=CollectorSettings.objects.select_for_update().get(pk=1)
        source.generation+=1
        source.paused=True
        source.reason='Сбор сброшен пользователем. Выберите новый период и возобновите источник.'
        # Keep the old lease until its in-flight HTTP call exits or expires.
        source.save(update_fields=['generation','paused','reason'])
        config.enabled=False
        config.save(update_fields=['enabled'])
        count=CollectionJob.objects.filter(state__in=['pending','running','error']).update(state='cancelled',current_unp='',message='Отменено при сбросе сбора')
        ImportDay.objects.exclude(state__in=['done','cancelled']).update(state='cancelled')
        Candidate.objects.exclude(state__in=['done','cancelled']).update(state='cancelled')
        CollectionRun.objects.filter(finished_at__isnull=True).update(state='cancelled',finished_at=__import__('django.utils.timezone',fromlist=['now']).now())
        CollectionReset.objects.create(actor=actor,cancelled_jobs=count)
    return count
