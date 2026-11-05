from django import forms
from django.utils import timezone
from .models import CollectorSettings

class LookupForm(forms.Form):
    unp = forms.RegexField(r'^[0-9]{9}$', label='УНП', max_length=9, min_length=9, error_messages={'invalid':'Введите УНП: ровно 9 цифр.'}, widget=forms.TextInput(attrs={'placeholder':'193787017','inputmode':'numeric'}))

class PeriodForm(forms.Form):
    start = forms.DateField(label='С', widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    end = forms.DateField(label='По', widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    def __init__(self,*args,**kwargs):
        from datetime import timedelta
        super().__init__(*args,**kwargs)
        latest=(timezone.localdate()-timedelta(days=1)).isoformat()
        for field in ('start','end'):
            self.fields[field].widget.attrs['max']=latest
    def clean(self):
        data = super().clean()
        start,end = data.get('start'),data.get('end')
        if start and end:
            if end < start: raise forms.ValidationError('Дата окончания раньше начала.')
            if end >= timezone.localdate():
                from datetime import timedelta
                latest=(timezone.localdate()-timedelta(days=1)).strftime('%d.%m.%Y')
                raise forms.ValidationError(f'Сбор не запущен: доступны только завершённые дни, по {latest} включительно (Europe/Minsk). Сегодняшний день ещё не завершён. Исправьте дату «По» и нажмите загрузку снова.')
            if (end-start).days >= 365: raise forms.ValidationError('Одна загрузка — максимум 365 дней.')
        return data

class ScheduleForm(forms.ModelForm):
    class Meta:
        model = CollectorSettings
        fields = ['enabled','run_at','lookback','backup_enabled']
        labels = {'enabled':'Ежедневное обновление','run_at':'Время Europe/Minsk','lookback':'Перепроверять последних дней','backup_enabled':'Ежедневная резервная копия'}
        widgets = {'run_at':forms.TimeInput(format='%H:%M',attrs={'type':'time'})}
    def clean_lookback(self):
        value = self.cleaned_data['lookback']
        if not 1 <= value <= 30: raise forms.ValidationError('От 1 до 30 дней.')
        return value

class ActivityFilterForm(forms.Form):
    oked = forms.CharField(required=False,widget=forms.TextInput(attrs={'list':'oked-options'}),label='ОКЭД: код или группа',help_text='Например: 62010 или 62. Несколько через запятую: 62, 63.')
    exclude_oked = forms.CharField(required=False,label='Исключить ОКЭД',help_text='Коды или группы через запятую.')
    activity = forms.CharField(required=False,max_length=200,label='Название вида деятельности')
    def codes(self,field):
        import re
        raw = self.cleaned_data.get(field,'')
        values = [value.strip() for value in raw.split(',') if value.strip()]
        if len(values)>30 or any(not re.fullmatch(r'[0-9]{1,5}',value) for value in values):
            raise forms.ValidationError('Введите до 30 кодов из 1–5 цифр через запятую.')
        return values
    def clean_oked(self): return self.codes('oked')
    def clean_exclude_oked(self): return self.codes('exclude_oked')

from .models import Task,Interaction,Contact,CRMStatus
from .business import PHONE_RESULTS,team

class TaskForm(forms.ModelForm):
    class Meta:
        model=Task
        fields=['title','due_at','owner']
        labels={'title':'Задача','due_at':'Когда (Europe/Minsk)','owner':'Ответственный'}
        widgets={'due_at':forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'})}
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self.fields['owner'].queryset=team();self.fields['owner'].required=False

class InteractionForm(forms.ModelForm):
    def __init__(self,*a,company=None,**kw):
        super().__init__(*a,**kw)
        self.fields['contact'].queryset=Contact.objects.filter(company=company) if company else Contact.objects.none()
        self.fields['contact'].label='Контакт (если известен)'
    class Meta:
        model=Interaction
        fields=['kind','result','contact','note','occurred_at']
        labels={'kind':'Действие','result':'Результат','note':'Комментарий / причина отказа','occurred_at':'Когда'}
        widgets={'occurred_at':forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'})}

class ManualContactForm(forms.ModelForm):
    class Meta:
        model=Contact
        fields=['kind','raw','comment']
        labels={'kind':'Тип контакта','raw':'Значение','comment':'Комментарий'}

class ContactCheckForm(forms.Form):
    telegram_state=forms.ChoiceField(choices=Contact.MESSENGER_STATES,required=False,label='Telegram — ручная проверка',help_text='«Не найден» скрывает иконку. Это может зависеть от приватности, а не от наличия регистрации.')
    viber_state=forms.ChoiceField(choices=Contact.MESSENGER_STATES,required=False,label='Viber — ручная проверка',help_text='Если вы не проверяли номер, оставьте «Не проверено».')
    verification=forms.ChoiceField(choices=[(v,v) for v in PHONE_RESULTS],label='Результат проверки')
    comment=forms.CharField(required=False,widget=forms.Textarea,label='Комментарий')
    do_not_contact=forms.BooleanField(required=False,label='Не связываться с этим контактом')

class StatusForm(forms.ModelForm):
    class Meta:
        model=CRMStatus
        fields=['name','position','active']
        labels={'name':'Название статуса','position':'Порядок','active':'Использовать'}
