"""Read-only dashboard: explicit time ranges, cohorts and accessible chart data."""
from datetime import timedelta
from urllib.parse import urlencode
from django import forms
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.utils import timezone
from .models import Company, Interaction, Task, Activity, Candidate
from .business import DEFAULT_STATUSES
from .templatetags.ui import STAGES


class DashboardPeriod(forms.Form):
    start=forms.DateField(label='С',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    end=forms.DateField(label='По',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))

    def clean(self):
        data=super().clean()
        start,end=data.get('start'),data.get('end')
        if start and end:
            if start>end: raise forms.ValidationError('Дата «С» должна быть не позже даты «По».')
            if (end-start).days>365: raise forms.ValidationError('Выберите период не больше 366 дней.')
            if end>timezone.localdate(): raise forms.ValidationError('Последний день периода не может быть в будущем.')
        return data


def bars(items,denominator=None):
    items=list(items)
    maximum=max([item['total'] for item in items] or [0])
    for item in items:
        scale=denominator if denominator is not None else maximum
        item['width']=round(100*item['total']/scale,2) if scale else 0
        item['share']=round(100*item['total']/denominator,1) if denominator else 0
    return items


def dashboard_context(params):
    today=timezone.localdate()
    days=int(params.get('days','30')) if params.get('days') in ('7','30','90') else 30
    data={'start':(today-timedelta(days=days-1)).isoformat(),'end':today.isoformat()}
    if 'start' in params or 'end' in params: data={key:params.get(key,'') for key in ('start','end')}
    form=DashboardPeriod(data)
    valid=form.is_valid()
    start,end=(form.cleaned_data['start'],form.cleaned_data['end']) if valid else (today-timedelta(days=29),today)
    rows=Company.objects.filter(admitted_at__isnull=False)
    total=rows.count()
    new=rows.filter(viewed_at__isnull=True).count()
    overdue=Task.objects.filter(company__admitted_at__isnull=False,completed_at__isnull=True,due_at__lt=timezone.now()).count()
    current_clients=rows.filter(status='Клиент').count()
    blocked=rows.filter(Q(do_not_contact=True)|Q(contacts__do_not_contact=True)).distinct().count()
    phones=rows.filter(contacts__kind='phone').distinct().count()
    emails=rows.filter(contacts__kind='email').distinct().count()
    current_phones=rows.filter(contacts__kind='phone',contacts__current=True).distinct().count()
    historical_only=rows.filter(contacts__kind='phone').exclude(pk__in=rows.filter(contacts__kind='phone',contacts__current=True)).distinct().count()
    events=Interaction.objects.filter(company__admitted_at__isnull=False,occurred_at__date__gte=start,occurred_at__date__lte=end)
    additions=rows.filter(admitted_at__date__gte=start,admitted_at__date__lte=end)
    if not valid: events=events.none();additions=additions.none()
    outgoing=events.filter(kind__in=['call','message'])
    contacted=outgoing.values('company_id').distinct()
    contacted_count=contacted.count()
    first_contact=outgoing.filter(company_id=OuterRef('company_id')).order_by('occurred_at').values('occurred_at')[:1]
    outcomes=events.filter(occurred_at__gte=Subquery(first_contact)).exclude(kind='note')
    answered=outcomes.filter(Q(kind='reply')|Q(result__in=['answered','negotiation','client'])).values('company_id').distinct().count()
    negotiations=outcomes.filter(result='negotiation').values('company_id').distinct().count()
    clients=outcomes.filter(result='client').values('company_id').distinct().count()
    refused=outcomes.filter(result='refusal').values('company_id').distinct().count()
    results=bars([
        {'label':'Обратились к компаниям','total':contacted_count,'tone':'new'},
        {'label':'Получили ответ','total':answered,'tone':'later'},
        {'label':'Дошли до переговоров','total':negotiations,'tone':'negotiating'},
        {'label':'Стали клиентами','total':clients,'tone':'client'},
        {'label':'Зафиксировали отказ','total':refused,'tone':'refused'},
    ],contacted_count)
    statuses=list(rows.values('status').annotate(total=Count('pk')))
    statuses.sort(key=lambda item:DEFAULT_STATUSES.index(item['status']) if item['status'] in DEFAULT_STATUSES else len(DEFAULT_STATUSES))
    status_bars=bars([{'label':item['status'],'total':item['total'],'tone':STAGES.get(item['status'],('custom',))[0],'url':'/?'+urlencode({'status':item['status']})} for item in statuses],total)
    regions=list(rows.values('region').annotate(total=Count('pk')).order_by('-total','region'))
    region_bars=[{'label':item['region'] or 'Регион не получен','total':item['total'],'url':'/?'+urlencode({'region':item['region']}) if item['region'] else ''} for item in regions[:6]]
    if len(regions)>6: region_bars.append({'label':'Другие регионы','total':sum(item['total'] for item in regions[6:])})
    activity_bars=bars([{'label':f"{item['code']} · {item['name']}",'total':item['total'],'url':'/?'+urlencode({'oked':item['code']})} for item in Activity.objects.filter(company__admitted_at__isnull=False,current=True).values('code','name').annotate(total=Count('company',distinct=True)).order_by('-total','code')[:6]],total)
    ages=bars([
        {'label':'До 30 дней','total':rows.filter(registered_on__gte=today-timedelta(days=30)).count()},
        {'label':'31–90 дней','total':rows.filter(registered_on__lt=today-timedelta(days=30),registered_on__gte=today-timedelta(days=90)).count()},
        {'label':'91–365 дней','total':rows.filter(registered_on__lt=today-timedelta(days=90),registered_on__gte=today-timedelta(days=365)).count()},
        {'label':'Старше года','total':rows.filter(registered_on__lt=today-timedelta(days=365)).count()},
        {'label':'Дата не получена','total':rows.filter(registered_on__isnull=True).count()},
    ],total)
    quality=bars([
        {'label':'Есть телефон','total':phones,'url':'/?contact=phone'},
        {'label':'Телефон текущий по ЕГР','total':current_phones,'url':'/?contact=phone&current=1'},
        {'label':'Только исторические телефоны','total':historical_only},
        {'label':'Есть email','total':emails,'url':'/?contact=email'},
    ],total)
    # Two independent series, with exact values available in the accompanying table.
    added=dict(additions.annotate(date=TruncDate('admitted_at')).values('date').annotate(total=Count('pk')).values_list('date','total'))
    calls=dict(outgoing.annotate(date=TruncDate('occurred_at')).values('date').annotate(total=Count('pk')).values_list('date','total'))
    monthly=(end-start).days>61
    buckets={}
    cursor=start
    while cursor<=end:
        key=cursor.replace(day=1) if monthly else cursor
        if key not in buckets: buckets[key]={'label':key.strftime('%m.%Y' if monthly else '%d.%m'),'added':0,'outgoing':0}
        buckets[key]['added']+=added.get(cursor,0)
        buckets[key]['outgoing']+=calls.get(cursor,0)
        cursor+=timedelta(days=1)
    trend=list(buckets.values())
    peak=max([max(item['added'],item['outgoing']) for item in trend] or [0])
    for item in trend:
        item['added_height']=round(100*item['added']/peak,2) if peak else 0
        item['outgoing_height']=round(100*item['outgoing']/peak,2) if peak else 0
    return {
        'period_form':form,'period_valid':valid,'period_start':start,'period_end':end,'period_days':(end-start).days+1,
        'quick_periods':[{'days':n,'active':valid and start==today-timedelta(days=n-1) and end==today} for n in (7,30,90)],
        'total':total,'new':new,'overdue':overdue,'current_clients':current_clients,'blocked':blocked,
        'without':Company.objects.filter(admitted_at__isnull=True).count(),'candidates':Candidate.objects.values('unp').distinct().count(),
        'added':additions.count(),'outgoing_events':outgoing.count(),'contacted':contacted_count,'answered':answered,'clients':clients,
        'response_conversion':round(100*answered/contacted_count,1) if contacted_count else None,
        'client_conversion':round(100*clients/contacted_count,1) if contacted_count else None,
        'completed_tasks':Task.objects.filter(company__admitted_at__isnull=False,completed_at__date__gte=start,completed_at__date__lte=end).count() if valid else 0,
        'result_bars':results,'status_bars':status_bars,'region_bars':bars(region_bars,total),'activity_bars':activity_bars,
        'age_bars':ages,'quality_bars':quality,'trend':trend,'trend_peak':peak,'trend_monthly':monthly,
        'reasons':outcomes.filter(result='refusal').exclude(note='').select_related('company').order_by('-occurred_at')[:5],
    }
