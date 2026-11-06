"""Daily first decisions; subsequent replies do not inflate the primary plan."""
from datetime import timedelta
from django import forms
from django.db.models import Count
from django.utils import timezone
from .models import Company,CompanyReview,WorkDay,WorkPreference

class ReviewForm(forms.Form):
    company=forms.IntegerField(min_value=1,widget=forms.HiddenInput)
    result=forms.ChoiceField(choices=CompanyReview.RESULTS,label='Итог обработки')
    note=forms.CharField(label='Краткий итог и следующий шаг',max_length=4000,widget=forms.Textarea(attrs={'rows':4,'placeholder':'Что выяснили и что планируете сделать дальше'}))

class GoalForm(forms.Form):
    target=forms.IntegerField(min_value=1,max_value=200,label='Компаний за день',widget=forms.NumberInput(attrs={'min':1,'max':200}))


def summary(user,day=None):
    today=day or timezone.localdate()
    preference=WorkPreference.objects.filter(user=user).first()
    workday=WorkDay.objects.filter(user=user,day=today).first()
    target=workday.target if workday else preference.daily_target if preference else 15
    own=CompanyReview.objects.filter(user=user,company__admitted_at__isnull=False)
    done=own.filter(completed_at__date=today).count()
    return {'target':target,'done':done,'remaining':max(0,target-done),'percent':min(100,round(100*done/target)),'queue':Company.objects.filter(admitted_at__isnull=False,status='Новая').exclude(pk__in=own.values('company_id')).count()}


def report(user,start,end):
    reviews=CompanyReview.objects.filter(user=user,company__admitted_at__isnull=False,completed_at__date__range=(start,end))
    results=dict(reviews.values('result').annotate(total=Count('pk')).values_list('result','total'))
    counts=dict(reviews.values('completed_at__date').annotate(total=Count('pk')).values_list('completed_at__date','total'))
    targets=dict(WorkDay.objects.filter(user=user,day__range=(start,end)).values_list('day','target'))
    days=[];day=start
    while day<=end:
        days.append({'day':day,'done':counts.get(day,0),'target':targets.get(day),'current':day==timezone.localdate(),'met':day in targets and counts.get(day,0)>=targets[day]})
        day+=timedelta(days=1)
    peak=max([max(item['done'],item['target'] or 0) for item in days] or [1]) or 1
    for item in days:
        item['height']=round(100*item['done']/peak)
        item['goal_height']=round(100*item['target']/peak) if item['target'] else 0
    return {'first_day':start,'last_day':end,'peak':peak,'total':reviews.count(),'days':days,'met_days':sum(item['met'] for item in days),'planned_days':len(targets),'results':[{'label':label,'total':results.get(key,0)} for key,label in CompanyReview.RESULTS]}
