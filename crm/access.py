from functools import wraps
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.db import transaction

def is_owner(user):
    if not user.is_authenticated or not user.is_active: return False
    if user.is_superuser: return True
    try: return user.crm_profile.role=='owner'
    except __import__('django.core.exceptions',fromlist=['ObjectDoesNotExist']).ObjectDoesNotExist: return user.is_staff

def permitted(user):
    if not user.is_authenticated or not user.is_active: return False
    if is_owner(user) or user.is_staff: return True
    try: return user.crm_profile.role=='manager'
    except Exception: return False

def allowed(view):
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        if not permitted(request.user): return redirect_to_login(request.get_full_path())
        if request.method=='POST':
            from .models import SourceControl
            with transaction.atomic():
                SourceControl.objects.get_or_create(pk=1)
                SourceControl.objects.select_for_update().get(pk=1)
                return view(request,*args,**kwargs)
        return view(request,*args,**kwargs)
    return wrapped

def owner_only(view):
    @allowed
    @wraps(view)
    def wrapped(request,*args,**kwargs):
        if not is_owner(request.user): return HttpResponseForbidden('Доступно только владельцу CRM')
        return view(request,*args,**kwargs)
    return wrapped
