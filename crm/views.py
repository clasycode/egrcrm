from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from .models import Company, Audit, ImportDay
allowed = user_passes_test(lambda u: u.is_active and u.is_staff)

def company_query(request):
    from datetime import date
    rows = Company.objects.filter(admitted_at__isnull=False).prefetch_related('contacts')
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
    return rows.order_by('-admitted_at','pk')

@allowed
def companies(request):
    from django.core.paginator import Paginator
    rows = Paginator(company_query(request),50).get_page(request.GET.get('page'))
    params=request.GET.copy()
    params.pop('page',None)
    return render(request,'crm/list.html',{'rows':rows,'q':request.GET.get('q',''),'statuses':Company._meta.get_field('status').choices,'query':params.urlencode()})

@allowed
def companies_export(request):
    import csv
    from django.http import HttpResponse
    response=HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition']='attachment; filename="companies.csv"'
    response.write('\ufeff')
    writer=csv.writer(response,delimiter=';')
    writer.writerow(['УНП','Название','Регистрация','Текущие телефоны','Исторические телефоны','Email','Сайт','CRM-статус','Не связываться'])
    def safe(value):
        text=str(value or '')
        if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')): return "'"+text
        return text
    for row in company_query(request):
        contacts=list(row.contacts.all())
        def values(kind,current=None): return ', '.join(c.raw for c in contacts if c.kind==kind and (current is None or c.current==current))
        writer.writerow(["'"+row.unp,safe(row.name),str(row.registered_on or ''),"'"+values('phone',True),"'"+values('phone',False),safe(values('email')),safe(values('site')),safe(row.status),'Да' if row.do_not_contact else 'Нет'])
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
        elif action == 'period':
            period = PeriodForm(request.POST)
            if period.is_valid():
                day = period.cleaned_data['start']
                while day <= period.cleaned_data['end']:
                    enqueue_day(day)
                    day += timedelta(days=1)
                messages.success(request,'Дни поставлены в очередь. Прогресс показан ниже; обновите страницу через несколько секунд.')
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
    return render(request,'crm/imports.html',{'rows':ImportDay.objects.order_by('-day')[:100],'jobs':CollectionJob.objects.select_related('company','day').order_by('-updated_at')[:50],'lookup':lookup,'period':period,'schedule':schedule,'source':source,'online':online,'heartbeat':config.heartbeat})

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
        writer.writerow(["'"+item.unp,day.day.isoformat(),{'done':'Обработан','pending':'В очереди','error':'Ошибка'}.get(item.state,item.state),'Да' if item.unp in admitted else 'Нет'])
    return response

@allowed
def statistics(request):
    from .models import Candidate,Contact
    from django.db.models import Count
    rows=Company.objects.filter(admitted_at__isnull=False)
    return render(request,'crm/statistics.html',{'total':rows.count(),'new':rows.filter(viewed_at__isnull=True).count(),'candidates':Candidate.objects.values('unp').distinct().count(),'without':Company.objects.filter(admitted_at__isnull=True).count(),'statuses':rows.values('status').annotate(total=Count('id')).order_by('status'),'phones':Contact.objects.filter(company__admitted_at__isnull=False,kind='phone').values('company_id').distinct().count(),'emails':Contact.objects.filter(company__admitted_at__isnull=False,kind='email').values('company_id').distinct().count()})
