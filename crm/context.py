from django.conf import settings

def ui_context(request):
    from .access import is_owner
    return {'app_version':settings.APP_VERSION,'crm_owner':is_owner(request.user)}
