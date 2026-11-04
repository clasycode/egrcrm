from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from .models import Company, Audit, ImportDay
allowed = user_passes_test(lambda u: u.is_active and u.is_staff)

def company_query(request):
    from datetime import date
    rows = Company.objects.filter(admitted_at__isnull=False).prefetch_related('contacts','activities')
    q = request.GET.get('q','').strip()
    if q: rows = rows.filter(Q(unp__icontains=q)|Q(name__icontains=q)|Q(contacts__raw__icontains=q)).distinct()
    if request.GET.get('new'): rows = rows.filter(viewed_at__isnull=True)
    if request.GET.get('kind') in ('1','2'): rows=rows.filter(kind=int(request.GET['kind']))
    if request.GET.get('status'): rows=rows.filter(status=request.GET['status'])
    if request.GET.get('allowed') == '1': rows=rows.filter(do_not_contact=False)
    for key,lookup in [('start','registered_on__gte'),('end','registered_on__lte')]:
        try: value=date.fromisoformat(request.GET.get(key,''))
        except ValueError: continue
        rows=rows.filter(**{lookup:value})
    if request.GET.get('contact') in ('phone','email','site'):
        filters={'contacts__kind':request.GET['contact']}
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
    return rows.order_by('-admitted_at','pk')

@allowed
def companies(request):
    from django.core.paginator import Paginator
    rows = Paginator(company_query(request),50).get_page(request.GET.get('page'))
    params=request.GET.copy()
    params.pop('page',None)
    from .forms import ActivityFilterForm
    from .progress import collection_progress
    return render(request,'crm/list.html',{'progress':collection_progress(),'activity_form':ActivityFilterForm(request.GET),'rows':rows,'q':request.GET.get('q',''),'statuses':Company._meta.get_field('status').choices,'query':params.urlencode()})

@allowed
def companies_export(request):
    import csv
    from django.http import HttpResponse
    from .forms import ActivityFilterForm
    if not ActivityFilterForm(request.GET).is_valid():
        return HttpResponse('Некорректный фильтр ОКЭД. Вернитесь к списку компаний и исправьте его.',status=400,content_type='text/plain; charset=utf-8')
    response=HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition']='attachment; filename="companies.csv"'
    response.write('\ufeff')
    writer=csv.writer(response,delimiter=';')
    writer.writerow(['УНП','Название','Регистрация','Текущие телефоны','Исторические телефоны','Email','Сайт','CRM-статус','Не связываться','ОКЭД (текущие)','Виды деятельности (текущие)','Текущий адрес'])
    def safe(value):
        text=str(value or '')
        if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')): return "'"+text
        return text
    for row in company_query(request):
        contacts=list(row.contacts.all())
        def values(kind,current=None): return ', '.join(c.raw for c in contacts if c.kind==kind and (current is None or c.current==current))
        writer.writerow(["'"+row.unp,safe(row.name),str(row.registered_on or ''),"'"+values('phone',True),"'"+values('phone',False),safe(values('email')),safe(values('site')),safe(row.status),'Да' if row.do_not_contact else 'Нет',"'"+', '.join(a.code for a in row.current_activities),safe('; '.join(a.name for a in row.current_activities)),safe(row.current_address)])
    return response

@allowed
def company(request,pk):
    with transaction.atomic():
        row = get_object_or_404(Company.objects.select_for_update(),pk=pk,admitted_at__isnull=False)
        if request.method == 'POST':
            changes = {}
            for field,value in [('notes',request.POST.get('notes','')),('do_not_contact',request.POST.get('do_not_contact')=='on'),('status',request.POST.get('status',row.status))]:
                if field == 'status' and value not in dict(Company._meta.get_field('status').choices): continue
                if getattr(row,field) != value:
                    changes[field] = {'before':getattr(row,field),'after':value}
                    setattr(row,field,value)
            if changes:
                row.save(update_fields=list(changes))
                Audit.objects.create(company=row,actor=request.user,changes=changes)
            return redirect(''+request.path)
        if row.viewed_at is None:
            row.viewed_at = timezone.now()
            row.save(update_fields=['viewed_at'])
    return render(request,'crm/detail.html',{'row':row,'statuses':Company._meta.get_field('status').choices})

@allowed
def imports(request):
    from datetime import timedelta
    from django.contrib import messages
    from .forms import LookupForm, PeriodForm, ScheduleForm
    from .models import CollectionJob, CollectorSettings, SourceControl
    from .jobs import enqueue_lookup, enqueue_day
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
                day = period.cleaned_data['start']
                while day <= period.cleaned_data['end']:
                    enqueue_day(day)
                    day += timedelta(days=1)
                messages.success(request,f'Период {period.cleaned_data["start"]:%d.%m.%Y}–{period.cleaned_data["end"]:%d.%m.%Y} принят в очередь. Сборщик обработает новые и незавершённые задания; завершённые дни повторно не загружаются. Прогресс ниже обновляется автоматически.')
                return redirect('/imports/')
            messages.error(request,' '.join(str(error) for errors in period.errors.values() for error in errors))
        elif action == 'fill_activities':
            count=0
            for unp in Company.objects.filter(activity_checked_at__isnull=True).values_list('unp',flat=True).iterator():
                enqueue_lookup(unp)
                count+=1
            messages.success(request,f'В очередь обновления поставлено компаний: {count}. Существующие заметки и статусы сохраняются.')
            return redirect('/imports/')
        elif action == 'initialize':
            for n in range(1,366): enqueue_day(timezone.localdate()-timedelta(days=n))
            messages.success(request,'В очередь добавлены последние 365 завершённых дней. Уже завершённые дни не загружаются повторно.')
            return redirect('/imports/')
        elif action == 'schedule':
            schedule = ScheduleForm(request.POST,instance=config)
            if schedule.is_valid():
                schedule.save()
                messages.success(request,'Расписание сохранено. Оно выполняется только при работающем сборщике.')
                return redirect('/imports/')
            messages.error(request,'Расписание не сохранено. '+' '.join(str(error) for errors in schedule.errors.values() for error in errors))
        elif action in ('pause','resume'):
            source.paused = action == 'pause'
            if action == 'resume': source.reason = ''
            else: source.reason = 'Пауза включена пользователем'
            source.save(update_fields=['paused','reason'])
            messages.success(request,'Настройка источника сохранена. Текущий запрос может ещё завершаться.')
            return redirect('/imports/')
        elif action == 'retry':
            job = get_object_or_404(CollectionJob,pk=request.POST.get('job'))
            if job.state == 'error':
                if job.day: enqueue_day(job.day.day)
                else: enqueue_lookup(job.unp)
                messages.success(request,'Задание поставлено на повтор. Если источник на паузе, сначала устраните причину и возобновите его.')
            return redirect('/imports/')
    online = config.heartbeat and timezone.now()-config.heartbeat < timedelta(minutes=15)
    from .progress import collection_progress
    return render(request,'crm/imports.html',{'progress':collection_progress(),'jobs':CollectionJob.objects.select_related('company','day').order_by('-updated_at')[:50],'lookup':lookup,'period':period,'schedule':schedule,'source':source,'reset_paused':source.reason.startswith('Сбор сброшен'),'online':online,'heartbeat':config.heartbeat})

@allowed
def job_status(request,pk):
    from .models import CollectionJob
    return render(request,'crm/job.html',{'job':get_object_or_404(CollectionJob,pk=pk)})

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
    from .models import Candidate,Contact
    from django.db.models import Count
    rows=Company.objects.filter(admitted_at__isnull=False)
    return render(request,'crm/statistics.html',{'total':rows.count(),'new':rows.filter(viewed_at__isnull=True).count(),'candidates':Candidate.objects.values('unp').distinct().count(),'without':Company.objects.filter(admitted_at__isnull=True).count(),'statuses':rows.values('status').annotate(total=Count('id')).order_by('status'),'phones':Contact.objects.filter(company__admitted_at__isnull=False,kind='phone').values('company_id').distinct().count(),'emails':Contact.objects.filter(company__admitted_at__isnull=False,kind='email').values('company_id').distinct().count()})

@allowed
def import_progress(request):
    from django.http import JsonResponse
    from .progress import collection_progress
    response=JsonResponse(collection_progress())
    response['Cache-Control']='no-store'
    return response

@allowed
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
