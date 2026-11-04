from django import forms
from django.utils import timezone
from .models import CollectorSettings

class LookupForm(forms.Form):
    unp = forms.RegexField(r'^[0-9]{9}$', label='УНП', max_length=9, min_length=9, error_messages={'invalid':'Введите УНП: ровно 9 цифр.'}, widget=forms.TextInput(attrs={'placeholder':'193787017','inputmode':'numeric'}))

class PeriodForm(forms.Form):
    start = forms.DateField(label='С', widget=forms.DateInput(attrs={'type':'date'}))
    end = forms.DateField(label='По', widget=forms.DateInput(attrs={'type':'date'}))
    def clean(self):
        data = super().clean()
        start,end = data.get('start'),data.get('end')
        if start and end:
            if end < start: raise forms.ValidationError('Дата окончания раньше начала.')
            if end >= timezone.localdate(): raise forms.ValidationError('Выберите завершённые дни: не позднее вчерашнего.')
            if (end-start).days >= 365: raise forms.ValidationError('Одна загрузка — максимум 365 дней.')
        return data

class ScheduleForm(forms.ModelForm):
    class Meta:
        model = CollectorSettings
        fields = ['enabled','run_at','lookback']
        labels = {'enabled':'Ежедневное обновление','run_at':'Время Europe/Minsk','lookback':'Перепроверять последних дней'}
        widgets = {'run_at':forms.TimeInput(format='%H:%M',attrs={'type':'time'})}
    def clean_lookback(self):
        value = self.cleaned_data['lookback']
        if not 1 <= value <= 30: raise forms.ValidationError('От 1 до 30 дней.')
        return value
