from datetime import timedelta
from django.shortcuts import render,redirect,get_object_or_404
from django.http import HttpResponse,HttpResponseNotAllowed
from django.contrib import messages
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from .access import allowed
from .models import Company,CompanyReview,WorkDay,WorkPreference
from .reviewing import GoalForm,ReviewForm,summary,report

def work_context(user,goal=None,review=None,tab="new",q="",page=1):
    from django.core.paginator import Paginator
    from django.db.models import Q
    stats=summary(user)
    reviews=CompanyReview.objects.filter(user=user)
    companies=Company.objects.filter(admitted_at__isnull=False).select_related('owner').prefetch_related('contacts','activities')
    tabs=[('new','Новые',companies.filter(status='Новая')),('working','В работе',companies.filter(status='В работе')),('answered','Ответили',companies.filter(status='В работе',reply_state='answered')),('no_reply','Не ответили',companies.filter(status='В работе',reply_state='no_reply')),('negotiation','Переговоры',companies.filter(status='Переговоры'))]
    selected=tab if tab in [item[0] for item in tabs] else 'new'
    choices=[{'key':key,'label':label,'count':rows.count()} for key,label,rows in tabs]
    rows=next(rows for key,label,rows in tabs if key==selected)
    if q:rows=rows.filter(Q(search_name__contains=q.casefold())|Q(unp__icontains=q))
    queue=Paginator(rows.order_by('-admitted_at','pk'),20).get_page(page)
    from .models import Interaction
    today=timezone.localdate()
    return {'work':stats,'goal_form':goal if goal is not None else GoalForm(initial={'target':stats['target']}),'review_form':review if review is not None else ReviewForm(),'queue':queue,'lead_tabs':choices,'lead_tab':selected,'q':q,'work_report':report(user,today-timedelta(days=13),today),'today_reviews':reviews.filter(completed_at__date=today).select_related('company').order_by('-completed_at'),'outreach_today':Interaction.objects.filter(actor=user,kind='message',occurred_at__date=today).values('company_id').distinct().count(),'replies_today':Interaction.objects.filter(actor=user,kind='reply',occurred_at__date=today).values('company_id').distinct().count()}

@allowed
def workday(request):
    goal=GoalForm(request.POST) if request.method=='POST' else None
    if goal is not None and goal.is_valid():
        WorkPreference.objects.update_or_create(user=request.user,defaults={'daily_target':goal.cleaned_data['target']})
        WorkDay.objects.update_or_create(user=request.user,day=timezone.localdate(),defaults={'target':goal.cleaned_data['target']})
        messages.success(request,'Цель сохранена на сегодня и для следующих дней. Прошлые дни не изменены.')
        return redirect('/work/')
    return render(request,'crm/workday.html',work_context(request.user,goal=goal,tab=request.GET.get('tab'),q=request.GET.get('q','').strip(),page=request.GET.get('page')))

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
    response=HttpResponse(company_brief(row),content_type='text/plain; charset=utf-8')
    response['Cache-Control']='no-store'
    return response

@allowed
def lead_action(request,pk):
    from django.core.exceptions import ValidationError
    from django.http import JsonResponse
    from django.template.loader import render_to_string
    from .lead_actions import change_status,mark_reply
    from .business import status_choices
    if request.method!='POST':return HttpResponseNotAllowed(['POST'])
    row=get_object_or_404(Company.objects.select_for_update(),pk=pk,admitted_at__isnull=False)
    action=request.POST.get('action')
    targets={'start':'В работе','not_fit':'Не подходит','negotiation':'Переговоры','client':'Клиент','refusal':'Отказ'}
    wants_json=request.headers.get('Accept')=='application/json'
    error='';code=400
    if request.POST.get('previous_status')!=row.status or request.POST.get('previous_reply')!=row.reply_state:
        error='Компания уже изменена. Обновите список, чтобы увидеть актуальный статус.';code=409
    elif action not in targets and action not in ('answered','no_reply'):error='Неизвестное действие.'
    elif action in targets and targets[action] not in dict(status_choices()):error='Этот статус выключен в настройках.'
    elif action=='start' and row.status!='Новая':error='Первое сообщение можно отметить только для новой компании.'
    elif action=='negotiation' and row.status!='В работе':error='Сначала переведите компанию в работу.'
    elif action in ('client','refusal') and row.status not in ('В работе','Переговоры'):error='Сначала переведите компанию в работу.'
    counted=False
    if not error:
        try:
            if action in targets:counted=change_status(row,request.user,targets[action])
            else:mark_reply(row,request.user,action)
        except ValidationError as exc:error=' '.join(exc.messages)
    if error:
        if wants_json:return JsonResponse({'error':error},status=code)
        messages.error(request,error)
    else:
        if wants_json:
            from .templatetags.ui import stage_badge
            return JsonResponse({'status':row.status,'reply_state':row.reply_state,'actions':render_to_string('crm/components/lead_actions.html',{'row':row},request=request),'badge':render_to_string('crm/components/status.html',stage_badge(row.status)),**live_metrics(request.user),'counted':counted,'work':summary(request.user),'clients':Company.objects.filter(admitted_at__isnull=False,status='Клиент').count()})
        messages.success(request,'Сохранено. '+('Компания засчитана в дневной план.' if counted else ''))
    target=request.POST.get('next','/work/')
    if not url_has_allowed_host_and_scheme(target,{request.get_host()},require_https=request.is_secure()):target='/work/'
    return redirect(target)


def live_metrics(user):
    from .models import Interaction
    today=timezone.localdate()
    rows=Company.objects.filter(admitted_at__isnull=False)
    from django.template.loader import render_to_string
    today_html=render_to_string('crm/components/today_decisions.html',{'today_reviews':CompanyReview.objects.filter(user=user,completed_at__date=today).select_related('company').order_by('-completed_at')})
    history=report(user,today-timedelta(days=13),today)
    history_html=render_to_string('crm/components/work_history.html',{'processing':history})
    return {'history_total':history['total'],'history_met_days':history['met_days'],'history_html':history_html,'today_html':today_html,'lead_counts':{'new':rows.filter(status='Новая').count(),'working':rows.filter(status='В работе').count(),'answered':rows.filter(status='В работе',reply_state='answered').count(),'no_reply':rows.filter(status='В работе',reply_state='no_reply').count(),'negotiation':rows.filter(status='Переговоры').count()},'outreach_today':Interaction.objects.filter(actor=user,kind='message',occurred_at__date=today).values('company_id').distinct().count(),'replies_today':Interaction.objects.filter(actor=user,kind='reply',occurred_at__date=today).values('company_id').distinct().count()}
