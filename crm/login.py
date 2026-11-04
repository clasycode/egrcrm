import hashlib
from datetime import timedelta
from django.contrib.auth.views import LoginView
from django.http import HttpResponse
from django.db.models import F
from django.utils import timezone
from .models import LoginAttempt
class ProtectedLoginView(LoginView):
    def post(self,request,*args,**kwargs):
        identity=request.POST.get('username','').casefold()+'|'+request.META.get('REMOTE_ADDR','')
        self.key=hashlib.sha256(identity.encode()).hexdigest()
        row,_=LoginAttempt.objects.get_or_create(key=self.key)
        if row.window_start<timezone.now()-timedelta(minutes=15):
            row.attempts=0;row.window_start=timezone.now();row.save()
        if row.attempts>=8:
            response=HttpResponse('Слишком много попыток входа. Повторите через 15 минут.',status=429)
            response['Retry-After']='900';return response
        return super().post(request,*args,**kwargs)
    def form_invalid(self,form):
        LoginAttempt.objects.filter(key=self.key).update(attempts=F('attempts')+1)
        return super().form_invalid(form)
    def form_valid(self,form):
        LoginAttempt.objects.filter(key=self.key).delete()
        return super().form_valid(form)
