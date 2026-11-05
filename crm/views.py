from .access import allowed,owner_only
from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from .models import Company, Audit, ImportDay,RunCompany
from .business import status_choices,team,PHONE_RESULTS

def company_query(request):
    from datetime import date
    rows = Company.objects.filter(admitted_at__isnull=False).select_related('owner').prefetch_related('contacts','activities')
    q = request.GET.get('q','').strip()
    from .normalization import search_contact
    if q: rows = rows.filter(Q(unp__icontains=q)|Q(search_name__contains=q.casefold())|Q(contacts__raw__icontains=q)|Q(contacts__normalized__contains=search_contact(q))).distinct()
    if request.GET.get('new'): rows = rows.filter(viewed_at__isnull=True)
    if request.GET.get('kind') in ('1','2'): rows=rows.filter(kind=int(request.GET['kind']))
    if request.GET.get('status'): rows=rows.filter(status=request.GET['status'])
    if request.GET.get('allowed')=='1' or request.GET.get('outreach')=='1':
        rows=rows.filter(do_not_contact=False).exclude(contacts__do_not_contact=True)
    for key in ['region','city','district','egr_state']:
        if request.GET.get(key):
            choices=Company.objects.exclude(**{key:''}).values_list(key,flat=True).distinct()
            matched=[value for value in choices if value.casefold()==request.GET[key].strip().casefold()]
            rows=rows.filter(**{key+'__in':matched})
    if request.GET.get('owner','').isdigit(): rows=rows.filter(owner_id=int(request.GET['owner']))
    if request.GET.get('verification'): rows=rows.filter(contacts__verification=request.GET['verification']).distinct()
    if request.GET.get('task')=='overdue': rows=rows.filter(tasks__completed_at__isnull=True,tasks__due_at__lt=timezone.now()).distinct()
    elif request.GET.get('task')=='open': rows=rows.filter(tasks__completed_at__isnull=True,tasks__pk__isnull=False).distinct()
    if request.GET.get('changed')=='1':
        from django.db.models import F
        rows=rows.filter(source_changed_at__isnull=False).filter(Q(source_change_seen_at__isnull=True)|Q(source_changed_at__gt=F('source_change_seen_at')))

    for key,lookup in [('start','registered_on__gte'),('end','registered_on__lte')]:
        try: value=date.fromisoformat(request.GET.get(key,''))
        except ValueError: continue
        rows=rows.filter(**{lookup:value})
    if request.GET.get('contact') in ('phone','email','site') or request.GET.get('current')=='1':
        filters={}
        if request.GET.get('contact') in ('phone','email','site'): filters['contacts__kind']=request.GET['contact']
        if request.GET.get('current') == '1': filters['contacts__current']=True
        rows=rows.filter(**filters).distinct()
    from .forms import ActivityFilterForm
    from .models import Activity, Candidate
    activity_form=ActivityFilterForm(request.GET)
    if not activity_form.is_valid(): return rows.none()
    data=activity_form.cleaned_data
    matching=Activity.objects.filter(current=True)
    if data['oked']:
        codes=Q()
        for code in data['oked']: codes |= Q(code__startswith=code)
        matching=matching.filter(codes)
    if data['activity']: matching=matching.filter(search_name__contains=data['activity'].strip().casefold())
    if data['oked'] or data['activity']: rows=rows.filter(pk__in=matching.values('company_id'))
    if data['exclude_oked']:
        codes=Q()
        for code in data['exclude_oked']: codes |= Q(code__startswith=code)
        rows=rows.exclude(pk__in=Activity.objects.filter(codes,current=True).values('company_id'))
    if request.GET.get('day'):
        try: day_id=int(request.GET['day'])
        except ValueError: return rows.none()
        rows=rows.filter(unp__in=Candidate.objects.filter(day_id=day_id,state='done').values('unp'))
    if request.GET.get('run','').isdigit():
        from .models import CollectionRun
        run=CollectionRun.objects.filter(pk=int(request.GET['run'])).first()
        if run and run.finished_at: rows=rows.filter(pk__in=RunCompany.objects.filter(run=run).values('company_id'))
        elif run: rows=rows.filter(unp__in=Candidate.objects.filter(day__runday__run=run,state='done').values('unp'))
        else: return rows.none()
    sorting={'name':'search_name','registration':'registered_on','added':'-admitted_at','oldest':'admitted_at'}
    return rows.order_by(sorting.get(request.GET.get('sort'),'-admitted_at'),'pk')

@allowed
def companies(request):
    from django.core.paginator import Paginator
    rows = Paginator(company_query(request),50).get_page(request.GET.get('page'))
    params=request.GET.copy()
    params.pop('page',None)
    from .forms import ActivityFilterForm
    from .progress import collection_progress
    from .presentation import list_context
    return render(request,'crm/list.html',{**list_context(request),'phone_results':PHONE_RESULTS,'activity_options':__import__('crm.models',fromlist=['Activity']).Activity.objects.filter(current=True).values('code','name').distinct().order_by('code')[:1000],'team':team(),'regions':Company.objects.exclude(region='').values_list('region',flat=True).distinct(),'states':Company.objects.exclude(egr_state='').values_list('egr_state',flat=True).distinct(),'progress':collection_progress(),'activity_form':ActivityFilterForm(request.GET),'rows':rows,'q':request.GET.get('q',''),'statuses':status_choices(),'query':params.urlencode()})

@allowed
def companies_export(request):
    import csv
    from django.http import HttpResponse
    from .forms import ActivityFilterForm
    if not ActivityFilterForm(request.GET).is_valid():
        return HttpResponse('Некорректный фильтр ОКЭД. Вернитесь к списку компаний и исправьте его.',status=400,content_type='text/plain; charset=utf-8')
    from .exports import export_rows
    return export_rows(request,company_query(request))

@owner_only
def imports(request):
    from datetime import timedelta
    from django.contrib import messages
    from .forms import LookupForm, PeriodForm, ScheduleForm
    from .models import CollectionJob, CollectorSettings, SourceControl
    from .jobs import enqueue_lookup, enqueue_day,enqueue_period
    config,_ = CollectorSettings.objects.get_or_create(pk=1)
    source,_ = SourceControl.objects.get_or_create(pk=1)
    yesterday = timezone.localdate()-timedelta(days=1)
    lookup = LookupForm()
    period = PeriodForm(initial={'start':yesterday,'end':yesterday})
    schedule = ScheduleForm(instance=config)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'lookup':
            lookup = LookupForm(request.POST)
            if lookup.is_valid():
                job = enqueue_lookup(lookup.cleaned_data['unp'])
                return redirect(f'/jobs/{job.pk}/')
            messages.error(request,'Проверка УНП не запущена. '+' '.join(str(error) for errors in lookup.errors.values() for error in errors))
        elif action == 'period':
            period = PeriodForm(request.POST)
            if period.is_valid():
                enqueue_period(period.cleaned_data['start'],period.cleaned_data['end'],actor=request.user)
                messages.success(request,f'Период {period.cleaned_data["start"]:%d.%m.%Y}–{period.cleaned_data["end"]:%d.%m.%Y} принят в очередь. Сборщик обработает новые и незавершённые задания; завершённые дни повторно не загружаются. Прогресс ниже обновляется автоматически.')
                return redirect('/imports/?tab=progress')
            messages.error(request,' '.join(str(error) for errors in period.errors.values() for error in errors))
        elif action == 'fill_activities':
            count=0
            for unp in Company.objects.filter(activity_checked_at__isnull=True).values_list('unp',flat=True).iterator():
                enqueue_lookup(unp)
                count+=1
            messages.success(request,f'В очередь обновления поставлено компаний: {count}. Существующие заметки и статусы сохраняются.')
            return redirect('/imports/?tab=progress')
        elif action == 'initialize':
            enqueue_period(timezone.localdate()-timedelta(days=365),timezone.localdate()-timedelta(days=1),actor=request.user,title='Годовая загрузка')
            messages.success(request,'В очередь добавлены последние 365 завершённых дней. Уже завершённые дни не загружаются повторно.')
            return redirect('/imports/?tab=progress')
        elif action == 'schedule':
            schedule = ScheduleForm(request.POST,instance=config)
            if schedule.is_valid():
                schedule.save()
                messages.success(request,'Расписание сохранено. Оно выполняется только при работающем сборщике.')
                return redirect('/imports/?tab=progress')
            messages.error(request,'Расписание не сохранено. '+' '.join(str(error) for errors in schedule.errors.values() for error in errors))
        elif action in ('pause','resume'):
            source.paused = action == 'pause'
            if action == 'resume': source.reason = ''
            else: source.reason = 'Пауза включена пользователем'
            source.save(update_fields=['paused','reason'])
            messages.success(request,'Настройка источника сохранена. Текущий запрос может ещё завершаться.')
            return redirect('/imports/?tab=progress')
        elif action == 'retry':
            job = get_object_or_404(CollectionJob,pk=request.POST.get('job'))
            if job.state == 'error' or (job.day and job.day.state=='partial'):
                if job.day: enqueue_day(job.day.day)
                else: enqueue_lookup(job.unp)
                messages.success(request,'Задание поставлено на повтор. Если источник на паузе, сначала устраните причину и возобновите его.')
            return redirect('/imports/?tab=progress')
    online = config.heartbeat and timezone.now()-config.heartbeat < timedelta(minutes=15)
    from .progress import collection_progress
    tab=request.GET.get('tab','start')
    if request.method=='POST' and request.POST.get('action')=='schedule': tab='schedule'
    return render(request,'crm/imports.html',{'tab':tab,'progress':collection_progress(),'jobs':CollectionJob.objects.select_related('company','day').order_by('-updated_at')[:50],'lookup':lookup,'period':period,'schedule':schedule,'source':source,'reset_paused':source.reason.startswith('Сбор сброшен'),'online':online,'heartbeat':config.heartbeat})

@allowed
def job_status(request,pk):
    from .models import CollectionJob
    return render(request,'crm/job.html',{'job':get_object_or_404(CollectionJob.objects.prefetch_related('history'),pk=pk)})

@allowed
def day_export(request,pk):
    import csv
    from django.http import HttpResponse
    day = get_object_or_404(ImportDay,pk=pk)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="egr-unp-{day.day.isoformat()}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response,delimiter=';')
    writer.writerow(['УНП (текст)','Дата регистрации','Обработка','Включён в CRM'])
    admitted = set(Company.objects.filter(admitted_at__isnull=False,unp__in=day.candidates.values('unp')).values_list('unp',flat=True))
    for item in day.candidates.order_by('unp'):
        # Prefix text fields with apostrophe; no spreadsheet formulas.
        writer.writerow(["'"+item.unp,day.day.isoformat(),{'done':'Обработан','pending':'В очереди','error':'Ошибка','cancelled':'Отменено'}.get(item.state,item.state),'Да' if item.unp in admitted else 'Нет'])
    return response

@allowed
def statistics(request):
    from .models import Candidate,Contact,Interaction,Task,Activity
    from django.db.models import Count
    rows=Company.objects.filter(admitted_at__isnull=False)
    events=Interaction.objects.filter(company__admitted_at__isnull=False)
    from datetime import date,timedelta
    for key,lookup in [('start','occurred_at__date__gte'),('end','occurred_at__date__lte')]:
        try: value=date.fromisoformat(request.GET.get(key,''))
        except ValueError: continue
        events=events.filter(**{lookup:value})
    outgoing=events.filter(kind__in=['call','message'])
    answered=events.filter(Q(kind='reply')|Q(kind__in=['call','message'],result__in=['answered','negotiation','client']))
    contacted=outgoing.values('company_id').distinct()
    contacted_count=contacted.count()
    answered_after_contact=answered.filter(company_id__in=contacted).values('company_id').distinct().count()
    clients=events.filter(result='client',company_id__in=contacted).exclude(kind='note').values('company_id').distinct().count()
    today=timezone.localdate()
    ages=[('До 30 дней',rows.filter(registered_on__gte=today-timedelta(days=30)).count()),('31–90 дней',rows.filter(registered_on__lt=today-timedelta(days=30),registered_on__gte=today-timedelta(days=90)).count()),('91–365 дней',rows.filter(registered_on__lt=today-timedelta(days=90),registered_on__gte=today-timedelta(days=365)).count()),('Старше года',rows.filter(registered_on__lt=today-timedelta(days=365)).count())]
    context={'total':rows.count(),'new':rows.filter(viewed_at__isnull=True).count(),'candidates':Candidate.objects.values('unp').distinct().count(),'without':Company.objects.filter(admitted_at__isnull=True).count(),'statuses':rows.values('status').annotate(total=Count('id')).order_by('status'),'phones':Contact.objects.filter(company__admitted_at__isnull=False,kind='phone').values('company_id').distinct().count(),'emails':Contact.objects.filter(company__admitted_at__isnull=False,kind='email').values('company_id').distinct().count(),'current_phones':rows.filter(contacts__kind='phone',contacts__current=True).distinct().count(),'historical_phones':rows.filter(contacts__kind='phone',contacts__current=False).distinct().count(),'contacted':contacted_count,'outgoing_events':outgoing.count(),'answered':answered.values('company_id').distinct().count(),'negotiations':events.filter(result='negotiation').exclude(kind='note').values('company_id').distinct().count(),'clients':clients,'refused':events.filter(result='refusal').exclude(kind='note').values('company_id').distinct().count(),'response_conversion':round(100*answered_after_contact/contacted_count,1) if contacted_count else None,'client_conversion':round(100*clients/contacted_count,1) if contacted_count else None,'done_tasks':Task.objects.filter(completed_at__isnull=False).count(),'overdue':Task.objects.filter(completed_at__isnull=True,due_at__lt=timezone.now()).count(),'regions':rows.values('region').annotate(total=Count('pk')).order_by('-total')[:30],'types':rows.values('kind').annotate(total=Count('pk')),'ages':ages,'activities':Activity.objects.filter(company__admitted_at__isnull=False,current=True).values('code','name').annotate(total=Count('company',distinct=True)).order_by('-total')[:30],'reasons':events.filter(result='refusal').exclude(note='').order_by('-occurred_at')[:30]}
    return render(request,'crm/statistics.html',context)

@allowed
def import_progress(request):
    from django.http import JsonResponse
    from .progress import collection_progress
    response=JsonResponse(collection_progress())
    response['Cache-Control']='no-store'
    return response

@owner_only
def reset_imports(request):
    from django.contrib import messages
    from .models import CollectionJob
    if request.method == 'POST':
        if request.POST.get('confirm') != 'reset':
            messages.error(request,'Подтвердите сброс на странице ниже.')
        else:
            from .collection_control import reset_collection
            count=reset_collection(request.user)
            messages.success(request,f'Сбор сброшен. Отменено заданий: {count}. Компании и ручные правки сохранены. Расписание выключено, источник на паузе. Для нового сбора выберите период и возобновите источник.')
            return redirect('/imports/')
    return render(request,'crm/reset.html',{'pending':CollectionJob.objects.filter(state__in=['pending','running','error']).count(),'companies':Company.objects.count()})

@allowed
def company_fragment(request):
    from django.core.paginator import Paginator
    params=request.GET.copy();params.pop('page',None)
    return render(request,'crm/company_table.html',{'rows':Paginator(company_query(request),50).get_page(request.GET.get('page')),'query':params.urlencode()})

@owner_only
def job_fragment(request):
    from .models import CollectionJob
    return render(request,'crm/job_table.html',{'jobs':CollectionJob.objects.select_related('day','company').order_by('-updated_at')[:50]})
