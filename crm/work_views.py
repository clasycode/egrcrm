from datetime import timedelta
from django.shortcuts import render,redirect,get_object_or_404
from django.http import JsonResponse,HttpResponseNotAllowed
from django.contrib import messages
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from .access import allowed
from .models import Company,CompanyReview,WorkDay,WorkPreference
from .reviewing import GoalForm,ReviewForm,summary,report

def work_context(user,goal=None,review=None):
    stats=summary(user)
    reviews=CompanyReview.objects.filter(user=user)
    queue=Company.objects.filter(admitted_at__isnull=False).exclude(pk__in=reviews.values('company_id')).select_related('owner').prefetch_related('contacts','activities').order_by('-admitted_at','pk')[:20]
    return {'work':stats,'goal_form':goal if goal is not None else GoalForm(initial={'target':stats['target']}),'review_form':review if review is not None else ReviewForm(),'queue':queue,'work_report':report(user,timezone.localdate()-timedelta(days=13),timezone.localdate()),'today_reviews':reviews.filter(completed_at__date=timezone.localdate()).select_related('company').order_by('-completed_at')}

@allowed
def workday(request):
    goal=GoalForm(request.POST) if request.method=='POST' else None
    if goal is not None and goal.is_valid():
        WorkPreference.objects.update_or_create(user=request.user,defaults={'daily_target':goal.cleaned_data['target']})
        WorkDay.objects.update_or_create(user=request.user,day=timezone.localdate(),defaults={'target':goal.cleaned_data['target']})
        messages.success(request,'Цель сохранена на сегодня и для следующих дней. Прошлые дни не изменены.')
        return redirect('/work/')
    return render(request,'crm/workday.html',work_context(request.user,goal=goal))

@allowed
def complete(request):
    if request.method!='POST':return HttpResponseNotAllowed(['POST'])
    form=ReviewForm(request.POST)
    if form.is_valid():
        row=get_object_or_404(Company,pk=form.cleaned_data['company'],admitted_at__isnull=False)
        moment=timezone.now()
        today=timezone.localdate(moment)
        stats=summary(request.user,today)
        WorkDay.objects.get_or_create(user=request.user,day=today,defaults={'target':stats['target']})
        review,created=CompanyReview.objects.get_or_create(company=row,user=request.user,defaults={'result':form.cleaned_data['result'],'note':form.cleaned_data['note'],'completed_at':moment})
        if created:
            if row.viewed_at is None:
                row.viewed_at=moment;row.save(update_fields=['viewed_at'])
                from django.core.cache import cache
                cache.delete('crm-nav-counts-v31')
            from .work import log
            log(row,request.user,review=f'Обработка завершена: {review.get_result_display()}. {review.note}')
            messages.success(request,'Обработка сохранена. Компания засчитана в ваш дневной план.')
        else:messages.info(request,'Эта компания уже обработана вами. Повторно в план не засчитана; исходный итог сохранён.')
    else:
        messages.error(request,'Обработка не засчитана. Исправьте поля; ваш текст сохранён в форме.')
        return render(request,'crm/workday.html',work_context(request.user,review=form),status=400)
    target=request.POST.get('next','/work/')
    if not url_has_allowed_host_and_scheme(target,{request.get_host()},require_https=request.is_secure()):target='/work/'
    return redirect(target)

@allowed
def copy_company(request,pk):
    if request.method!='GET':return HttpResponseNotAllowed(['GET'])
    from .company_copy import company_brief
    row=get_object_or_404(Company.objects.select_related('owner').prefetch_related('contacts','activities'),pk=pk)
    response=JsonResponse({'text':company_brief(row)})
    response['Cache-Control']='no-store'
    return response
