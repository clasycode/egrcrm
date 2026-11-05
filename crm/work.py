import json
from datetime import timedelta
from django.shortcuts import render,get_object_or_404,redirect
from django.contrib import messages
from django.db.models import Q,Count,Max
from django.utils import timezone
from django.http import JsonResponse,HttpResponseBadRequest,FileResponse,HttpResponseNotAllowed
from .access import allowed,owner_only,is_owner
from .models import *
from .forms import TaskForm,InteractionForm,ManualContactForm,ContactCheckForm,StatusForm
from .business import status_choices,team
from .normalization import normalized_contact

def log(company,actor,**changes): Audit.objects.create(company=company,actor=actor,changes=changes)

@allowed
def company(request,pk):
    row=get_object_or_404(Company,pk=pk)
    task_form=TaskForm(auto_id='task_%s');interaction_form=InteractionForm(initial={'occurred_at':timezone.localtime()},company=row,auto_id='interaction_%s');manual_form=ManualContactForm(auto_id='manual_%s')
    if request.method=='POST':
        action=request.POST.get('action','save')
        if action=='save':
            changes={}
            for key,value in [('notes',request.POST.get('notes','')),('status',request.POST.get('status',row.status)),('do_not_contact',request.POST.get('do_not_contact')=='on')]:
                if key=='status' and value not in dict(status_choices()): continue
                if getattr(row,key)!=value: changes[key]={'before':getattr(row,key),'after':value};setattr(row,key,value)
            owner=request.POST.get('owner')
            if owner is not None:
                target=team().filter(pk=int(owner)).first() if owner.isdigit() else None
                if row.owner_id!=(target.pk if target else None): changes['owner']={'before':row.owner_id,'after':target.pk if target else None};row.owner=target
            if changes:
                row.save(update_fields=list(changes));log(row,request.user,**changes)
            return redirect(request.path)
        if action=='task':
            task_form=TaskForm(request.POST,auto_id='task_%s')
            if task_form.is_valid():
                task=task_form.save(commit=False);task.company=row;task.created_by=request.user;task.owner=task.owner or request.user;task.save()
                log(row,request.user,task=f'Создана задача «{task.title}»');return redirect(request.path)
        if action=='interaction':
            interaction_form=InteractionForm(request.POST,company=row,auto_id='interaction_%s')
            if row.do_not_contact and request.POST.get('kind') in ('call','message'):
                messages.error(request,'У компании запрет обращений. Сначала проверьте основание запрета. Запись не сохранена.')
            elif interaction_form.is_valid():
                contact=interaction_form.cleaned_data.get('contact')
                if contact and contact.do_not_contact and interaction_form.cleaned_data['kind'] in ('call','message'):
                    messages.error(request,'Для контакта установлен запрет обращений');return redirect(request.path)
                event=interaction_form.save(commit=False);event.company=row;event.actor=request.user;event.save()
                log(row,request.user,interaction=f'{event.get_kind_display()}: {event.get_result_display()} — {event.note}');return redirect(request.path)
        if action=='manual':
            manual_form=ManualContactForm(request.POST,auto_id='manual_%s')
            if manual_form.is_valid():
                data=manual_form.cleaned_data
                contact,_=Contact.objects.get_or_create(company=row,kind=data['kind'],raw=data['raw'],source='Вручную',defaults={'current':True,'normalized':normalized_contact(data['kind'],data['raw']),'original_line':data['raw'],'comment':data['comment']})

                if row.admitted_at is None and (row.contacts.filter(kind='phone').exists() or (row.kind==1 and row.contacts.filter(kind='email').exists())):
                    row.admitted_at=timezone.now();row.save(update_fields=['admitted_at'])
                log(row,request.user,contact=f'Добавлен ручной контакт {contact.raw}');return redirect(request.path)
        if action=='ack_changes':
            row.source_change_seen_at=timezone.now();row.save(update_fields=['source_change_seen_at']);return redirect(request.path)
    if row.viewed_at is None:
        row.viewed_at=timezone.now();row.save(update_fields=['viewed_at'])
    contacts=row.contacts.all()
    common=set(Contact.objects.exclude(company=row).filter(normalized__in=contacts.exclude(normalized='').values('normalized')).values_list('normalized',flat=True))
    events=[]
    labels={'notes':'Заметки','status':'Статус','do_not_contact':'Запрет обращения','owner':'Ответственный'}
    for entry in row.audit.select_related('actor').order_by('-created_at')[:100]:
        parts=[]
        for key,value in entry.changes.items():
            parts.append(f'{labels.get(key,key)}: {value.get("before","")} → {value.get("after","")}' if isinstance(value,dict) else str(value))
        events.append({'at':entry.created_at,'actor':entry.actor,'text':'; '.join(parts)})
    return render(request,'crm/detail.html',{'row':row,'statuses':status_choices()+([] if row.status in dict(status_choices()) else [(row.status,row.status)]),'team':team(),'task_form':task_form,'interaction_form':interaction_form,'manual_form':manual_form,'contacts':contacts,'common':common,'events':events,'recent_outreach':row.interactions.filter(kind__in=['call','message'],occurred_at__gte=timezone.now()-timedelta(days=1)).exists(),'last_outreach':row.interactions.filter(kind__in=['call','message']).aggregate(at=Max('occurred_at'))['at'],'snapshots':row.snapshots.all()[:30]})

@allowed
def company_status(request,pk):
    """Update only the CRM stage; never overwrite card notes or contact bans."""
    from django.core.cache import cache
    from django.utils.http import url_has_allowed_host_and_scheme
    from django.template.loader import render_to_string
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    row=get_object_or_404(Company.objects.select_for_update(),pk=pk,admitted_at__isnull=False)
    wants_json=request.headers.get('Accept')=='application/json'
    status=request.POST.get('status','')
    error=''
    code=400
    if status not in dict(status_choices()):
        error='Этот статус недоступен. Обновите список и выберите действующий статус.'
    elif request.POST.get('previous_status') != row.status:
        error=f'Статус уже изменён: «{row.status}». Обновите список перед повторной правкой.'
        code=409
    if error:
        if wants_json: return JsonResponse({'error':error},status=code)
        messages.error(request,error)
    else:
        before=row.status
        if before != status:
            row.status=status
            row.save(update_fields=['status'])
            log(row,request.user,status={'before':before,'after':status})
            cache.delete('crm-nav-counts-v31')
        if wants_json:
            from .templatetags.ui import stage_badge
            return JsonResponse({'status':row.status,'badge':render_to_string('crm/components/status.html',stage_badge(row.status)),'clients':Company.objects.filter(admitted_at__isnull=False,status='Клиент').count()})
        messages.success(request,f'Статус компании изменён: {row.status}')
    target=request.POST.get('next','/')
    if not url_has_allowed_host_and_scheme(target,allowed_hosts={request.get_host()},require_https=request.is_secure()): target='/'
    return redirect(target)

@allowed
def contact_check(request,pk):
    contact=get_object_or_404(Contact.objects.select_related('company'),pk=pk)
    form=ContactCheckForm(request.POST or None,initial={'verification':contact.verification,'comment':contact.comment,'do_not_contact':contact.do_not_contact})
    if request.method=='POST' and form.is_valid():
        before=contact.do_not_contact
        for field,value in form.cleaned_data.items(): setattr(contact,field,value)
        contact.checked_by=request.user;contact.checked_at=timezone.now();contact.save(update_fields=['verification','comment','do_not_contact','checked_by','checked_at'])
        ContactCheck.objects.create(contact=contact,actor=request.user,result=contact.verification,note=contact.comment)
        log(contact.company,request.user,contact_check=f'{contact.raw}: {contact.verification}; запрет {before} → {contact.do_not_contact}; {contact.comment}')
        return redirect(f'/companies/{contact.company_id}/')
    return render(request,'crm/form.html',{'title':f'Проверка контакта {contact.raw}','form':form})

@allowed
def tasks(request):
    if request.method=='POST':
        task=get_object_or_404(Task,pk=request.POST.get('task'))
        task.completed_at=timezone.now() if request.POST.get('action')=='complete' else None
        task.save(update_fields=['completed_at']);log(task.company,request.user,task=f'Задача «{task.title}»: '+('выполнена' if task.completed_at else 'возвращена в работу'))
        return redirect('/tasks/')
    today=timezone.localdate();mode=request.GET.get('mode','today')
    rows=Task.objects.select_related('company','owner').order_by('due_at')
    if mode=='completed': rows=rows.filter(completed_at__isnull=False)
    elif mode=='overdue': rows=rows.filter(completed_at__isnull=True,due_at__lt=timezone.now())
    elif mode=='upcoming': rows=rows.filter(completed_at__isnull=True,due_at__date__gt=today)
    else: rows=rows.filter(completed_at__isnull=True,due_at__date=today)
    return render(request,'crm/tasks.html',{'rows':rows[:300],'mode':mode})

@allowed
def candidates(request):
    rows=Company.objects.filter(admitted_at__isnull=True).order_by('next_contact_check','pk')
    if request.method=='POST':
        if not is_owner(request.user):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden('Повторные запросы источника запускает владелец')
        from .jobs import enqueue_lookup
        ids=request.POST.getlist('selected')
        for row in rows.filter(pk__in=[int(v) for v in ids if v.isdigit()])[:300]: enqueue_lookup(row.unp)
        messages.success(request,'Выбранные кандидаты поставлены на проверку');return redirect('/candidates/')
    return render(request,'crm/candidates.html',{'rows':rows[:300]})

@allowed
def selections(request):
    if request.method=='POST':
        action=request.POST.get('action')
        if action=='save':
            filters=request.POST.get('filters','')
            from urllib.parse import parse_qs
            parsed={k:v[-1] for k,v in parse_qs(filters).items() if k not in ('page','selected','format')}
            SavedSelection.objects.create(owner=request.user,name=request.POST.get('name','Подборка')[:200],filters=parsed)
        elif action=='delete': SavedSelection.objects.filter(pk=request.POST.get('selection'),owner=request.user).delete()
        return redirect('/selections/')
    from urllib.parse import urlencode
    rows=[{'id':s.pk,'name':s.name,'query':urlencode(s.filters)} for s in SavedSelection.objects.filter(owner=request.user)]
    return render(request,'crm/selections.html',{'rows':rows})

@allowed
def bulk(request):
    if request.method!='POST': return HttpResponseBadRequest('POST required')
    from .views import company_query
    rows=company_query(request)
    ids=[int(v) for v in request.POST.getlist('selected') if v.isdigit()]
    if request.POST.get('all_filtered')!='on': rows=rows.filter(pk__in=ids)
    action=request.POST.get('action')
    if action=='website' and not is_owner(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Платный поиск запускает владелец')
    owner=team().filter(pk=request.POST.get('owner')).first() if request.POST.get('owner','').isdigit() else None
    if request.POST.get('owner') and owner is None:
        messages.error(request,'Ответственный не найден или не имеет доступа');return redirect('/?'+request.GET.urlencode())
    due=timezone.now()+timedelta(days=1)
    if action=='task' and request.POST.get('due_at'):
        from django.utils.dateparse import parse_datetime
        try: value=parse_datetime(request.POST['due_at'])
        except ValueError: value=None
        if value is None:
            messages.error(request,'Некорректный срок задачи');return redirect('/?'+request.GET.urlencode())
        due=timezone.make_aware(value) if timezone.is_naive(value) else value
    count=0
    for row in rows.iterator(chunk_size=100):
        if action=='status' and request.POST.get('status') in dict(status_choices()):
            before=row.status;row.status=request.POST['status'];row.save(update_fields=['status']);log(row,request.user,status={'before':before,'after':row.status})
        elif action=='owner':
            before=row.owner_id;row.owner=owner;row.save(update_fields=['owner']);log(row,request.user,owner={'before':before,'after':row.owner_id})
        elif action=='task':
            title=request.POST.get('title','Связаться с компанией')[:300]
            Task.objects.create(company=row,title=title,due_at=due,owner=owner,created_by=request.user);log(row,request.user,task='Массово создана задача: '+title)
        elif action=='website':
            from .websites import search_website
            search_website(row,request.user,confirmed=request.POST.get('cost_confirm')=='on')
        else: continue
        count+=1
    messages.success(request,f'Обработано карточек: {count}');return redirect('/?'+request.GET.urlencode())

@allowed
def runs(request):
    return render(request,'crm/runs.html',{'runs':CollectionRun.objects.order_by('-pk')[:100]})

@owner_only
def backups(request):
    if request.method=='POST':
        from .backups import create_archive,restore_archive
        try:
            if request.POST.get('action')=='create':
                record=create_archive();return redirect(f'/backups/{record.pk}/download/')
            if request.POST.get('action')=='restore':
                if request.POST.get('confirm')!='ВОССТАНОВИТЬ': raise ValueError('Введите ВОССТАНОВИТЬ для подтверждения')
                upload=request.FILES.get('archive')
                if not upload or upload.size>100*1024*1024: raise ValueError('Выберите архив CRM до 100 МБ')
                safety=restore_archive(upload,request.user,replace=request.POST.get('replace')=='on')
                messages.success(request,f'База восстановлена. Копия предыдущего состояния: {safety.filename}. Сбор и расписание остановлены.')
        except Exception as exc:
            # A failed import rolls back the entire business transaction.
            messages.error(request,'Восстановление не выполнено: '+str(exc)[:300])
        return redirect('/backups/')
    return render(request,'crm/backups.html',{'records':BackupRecord.objects.order_by('-pk')[:50]})

@owner_only
def backup_download(request,pk):
    from django.conf import settings
    record=get_object_or_404(BackupRecord,pk=pk)
    path=settings.BACKUP_DIR/record.filename
    if path.parent.resolve()!=settings.BACKUP_DIR.resolve() or not path.is_file(): return HttpResponseBadRequest('Файл недоступен')
    return FileResponse(path.open('rb'),as_attachment=True,filename=record.filename)

@owner_only
def settings_view(request):
    status_form=StatusForm()
    if request.method=='POST':
        action=request.POST.get('action')
        if action=='status':
            status_form=StatusForm(request.POST)
            if status_form.is_valid(): status_form.save();return redirect('/settings/')
        if action=='toggle_status':
            status=get_object_or_404(CRMStatus,pk=request.POST.get('status_id'))
            if status.active and CRMStatus.objects.filter(active=True).count()<=1:
                messages.error(request,'Оставьте хотя бы один активный статус');return redirect('/settings/')
            status.active=not status.active;status.save(update_fields=['active']);return redirect('/settings/')
        if action=='grant':
            from django.contrib.auth import get_user_model
            user=get_object_or_404(get_user_model(),pk=request.POST.get('user_id'))
            user.is_active=True;user.save(update_fields=['is_active'])
            AccessProfile.objects.update_or_create(user=user,defaults={'role':'manager'})
            messages.success(request,'Доступ менеджера предоставлен. При необходимости задайте ему пароль через администратора.');return redirect('/settings/')
        if action=='password':
            from django.contrib.auth import get_user_model
            from django.contrib.auth.forms import SetPasswordForm
            user=get_object_or_404(get_user_model(),pk=request.POST.get('user_id'))
            form=SetPasswordForm(user,request.POST)
            if form.is_valid(): form.save();messages.success(request,'Пароль изменён');return redirect('/settings/')
            for error in form.errors.values(): messages.error(request,str(error))
        if action=='user':
            from django.contrib.auth.forms import UserCreationForm
            form=UserCreationForm(request.POST)
            if form.is_valid():
                user=form.save();AccessProfile.objects.create(user=user,role='manager');messages.success(request,'Менеджер создан');return redirect('/settings/')
            for error in form.errors.values(): messages.error(request,str(error))
    from django.contrib.auth.forms import UserCreationForm
    return render(request,'crm/settings.html',{'status_form':status_form,'statuses':CRMStatus.objects.all(),'user_form':UserCreationForm(),'team':team(),'accounts':__import__('django.contrib.auth',fromlist=['get_user_model']).get_user_model().objects.order_by('username')})

@allowed
def diagnostics(request):
    from django.conf import settings
    from django.db import connection
    import os
    from .progress import collection_progress
    with connection.cursor() as cursor: cursor.execute('SELECT 1');cursor.fetchone()
    return render(request,'crm/diagnostics.html',{'version':settings.APP_VERSION,'database':connection.vendor,'progress':collection_progress(),'data_dir':settings.DATA_DIR if is_owner(request.user) else '', 'provider':bool(os.environ.get('BRAVE_API_KEY')),'backups':BackupRecord.objects.count()})

@allowed
def website(request,pk):
    row=get_object_or_404(Company,pk=pk)
    if request.method=='POST':
        if request.POST.get('action')!='confirm' and not is_owner(request.user):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden('Платный поиск запускает владелец')
        from .websites import search_website,safe_url
        if request.POST.get('action')=='confirm':
            try:
                url=safe_url(request.POST.get('url',''))
                WebsiteSearch.objects.create(company=row,actor=request.user,state='confirmed',url=url,provider='Вручную',evidence=request.POST.get('evidence','')[:4000])
                log(row,request.user,website='Вручную подтверждён сайт '+url)
                return redirect(request.path)
            except ValueError as exc: messages.error(request,str(exc))
        else: search_website(row,request.user,request.POST.get('cost_confirm')=='on');return redirect(request.path)
    rows=[]
    for entry in row.website_searches.select_related('actor').order_by('-pk')[:20]:
        hits=[]
        try:
            value=json.loads(entry.evidence)
            if isinstance(value,list): hits=value
        except ValueError: pass
        rows.append({'entry':entry,'hits':hits})
    import os
    return render(request,'crm/website.html',{'row':row,'rows':rows,'configured':bool(os.environ.get('BRAVE_API_KEY'))})

@allowed
def edit_manual_contact(request,pk):
    contact=get_object_or_404(Contact,pk=pk,source='Вручную')
    before={'raw':contact.raw,'kind':contact.kind,'comment':contact.comment}
    form=ManualContactForm(request.POST or None,instance=contact)
    if request.method=='POST' and form.is_valid():
        if Contact.objects.filter(company=contact.company,source='Вручную',kind=form.cleaned_data['kind'],raw=form.cleaned_data['raw']).exclude(pk=contact.pk).exists():
            form.add_error('raw','Такой ручной контакт уже существует')
        else:
            value=form.save(commit=False)
            value.normalized=normalized_contact(value.kind,value.raw);value.original_line=value.raw
            value.save(update_fields=['raw','kind','comment','normalized','original_line'])
            log(contact.company,request.user,manual_contact={'before':before,'after':form.cleaned_data})
            return redirect(f'/companies/{contact.company_id}/')
    return render(request,'crm/form.html',{'title':'Изменить ручной контакт','form':form})

@allowed
def edit_task(request,pk):
    task=get_object_or_404(Task,pk=pk)
    before={'title':task.title,'due_at':task.due_at.isoformat(),'owner':task.owner_id}
    form=TaskForm(request.POST or None,instance=task)
    if request.method=='POST' and form.is_valid():
        task=form.save()
        log(task.company,request.user,task={'before':before,'after':{'title':task.title,'due_at':task.due_at.isoformat(),'owner':task.owner_id}})
        return redirect('/tasks/')
    return render(request,'crm/form.html',{'title':'Изменить задачу','form':form})

@owner_only
def edit_status(request,pk):
    status=get_object_or_404(CRMStatus,pk=pk)
    old=status.name
    form=StatusForm(request.POST or None,instance=status)
    if request.method=='POST' and form.is_valid():
        if not form.cleaned_data['active'] and CRMStatus.objects.filter(active=True).exclude(pk=pk).count()==0:
            form.add_error('active','Оставьте хотя бы один активный статус')
        else:
            status=form.save()
            if old!=status.name:
                ids=list(Company.objects.filter(status=old).values_list('pk',flat=True))
                Company.objects.filter(pk__in=ids).update(status=status.name)
                Audit.objects.bulk_create([Audit(company_id=pk,actor=request.user,changes={'status':{'before':old,'after':status.name},'reason':'Переименование справочника'}) for pk in ids],batch_size=500)
            return redirect('/settings/')
    return render(request,'crm/form.html',{'title':'Изменить статус (переименование применяется к карточкам)','form':form})
