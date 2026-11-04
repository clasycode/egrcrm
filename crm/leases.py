from datetime import timedelta
from uuid import uuid4
from django.db.models import Q
from django.utils import timezone
from .models import SourceControl

def acquire():
    SourceControl.objects.get_or_create(pk=1)
    now=timezone.now()
    token=str(uuid4())
    won=SourceControl.objects.filter(pk=1,paused=False).filter(Q(locked_until__isnull=True)|Q(locked_until__lt=now)).update(lease_owner=token,locked_until=now+timedelta(minutes=5))
    return token if won else None

def renew(token):
    return SourceControl.objects.filter(pk=1,lease_owner=token,paused=False).update(locked_until=timezone.now()+timedelta(minutes=5))

def release(token):
    SourceControl.objects.filter(pk=1,lease_owner=token).update(locked_until=None,lease_owner='')
