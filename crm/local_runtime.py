import os
from pathlib import Path
from django.conf import settings
from django.http import JsonResponse, Http404
from django.views.decorators.cache import never_cache


def stop_requested():
    return settings.LOCAL_MODE and bool(os.environ.get('CRM_LOCAL_STOP_FILE')) and Path(os.environ['CRM_LOCAL_STOP_FILE']).exists()


@never_cache
def status(request):
    if not settings.LOCAL_MODE or request.META.get('REMOTE_ADDR') not in ('127.0.0.1','::1'):
        raise Http404
    from desktop_app.storage import data_identity
    return JsonResponse({'application':'egrcrm','data_id':data_identity(settings.DATA_DIR),'version':settings.APP_VERSION})
