from datetime import timedelta
from uuid import uuid4
from django.db.models import Q
from django.utils import timezone
from .models import SourceControl

def acquire():
    source,_=SourceControl.objects.get_or_create(pk=1)
    now=timezone.now()
    token=f'{source.generation}:{uuid4()}'
    won=SourceControl.objects.filter(pk=1,paused=False,generation=source.generation).filter(Q(locked_until__isnull=True)|Q(locked_until__lt=now)).update(lease_owner=token,locked_until=now+timedelta(minutes=5))
    return token if won else None

def renew(token):
    if not is_current(token): return False
    return SourceControl.objects.filter(pk=1,lease_owner=token,generation=int(token.split(':',1)[0]),paused=False).update(locked_until=timezone.now()+timedelta(minutes=5))

def release(token):
    SourceControl.objects.filter(pk=1,lease_owner=token).update(locked_until=None,lease_owner='')

def is_current(token,lock=False,allow_paused=False):
    try: generation=int(token.split(':',1)[0])
    except (ValueError,AttributeError): return False
    rows=SourceControl.objects.select_for_update() if lock else SourceControl.objects.all()
    rows=rows.filter(pk=1,lease_owner=token,generation=generation)
    if not allow_paused: rows=rows.filter(paused=False)
    return rows.exists()

def assert_current(token,lock=False):
    from .egr import SourceCancelled
    if not is_current(token,lock=lock): raise SourceCancelled('Сбор отменён или приостановлен пользователем')
