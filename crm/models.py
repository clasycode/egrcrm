from django.conf import settings
from django.db import models

class Company(models.Model):
    country = models.CharField(max_length=2, default='BY')
    unp = models.CharField(max_length=32)
    name = models.CharField(max_length=500, blank=True)
    kind = models.PositiveSmallIntegerField(choices=[(1,'Юридическое лицо'),(2,'ИП')])
    registered_on = models.DateField(null=True)
    admitted_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    source_data = models.JSONField(default=dict)
    status = models.CharField(max_length=32, default='Новая', choices=[(x,x) for x in ['Новая','В работе','Переговоры','Клиент','Вернуться позже','Не подходит','Отказ']])
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)
    do_not_contact = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['country','unp'],name='unique_country_unp')]
    def __str__(self): return f'{self.unp} — {self.name}'

class Contact(models.Model):
    company = models.ForeignKey(Company,on_delete=models.CASCADE,related_name='contacts')
    kind = models.CharField(max_length=10,choices=[('phone','Телефон'),('email','Email'),('site','Сайт')])
    raw = models.TextField()
    source = models.CharField(max_length=100)
    current = models.BooleanField(default=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    source_data = models.JSONField(default=dict)
    verification = models.CharField(max_length=40,default='Не проверен')
    do_not_contact = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['company','kind','raw','source'],name='unique_source_contact')]

class Audit(models.Model):
    company = models.ForeignKey(Company,on_delete=models.CASCADE,related_name='audit')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    changes = models.JSONField()

class ImportDay(models.Model):
    day = models.DateField(unique=True)
    state = models.CharField(max_length=16,default='pending')
    error = models.TextField(blank=True)
    discovered = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

class Candidate(models.Model):
    day = models.ForeignKey(ImportDay,on_delete=models.CASCADE,related_name='candidates')
    unp = models.CharField(max_length=32)
    state = models.CharField(max_length=16,default='pending')
    error = models.TextField(blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['day','unp'],name='unique_day_candidate')]

class Receipt(models.Model):
    url = models.URLField(max_length=1000)
    received_at = models.DateTimeField(auto_now_add=True)
    status = models.PositiveSmallIntegerField()
    body = models.TextField()
    version = models.CharField(max_length=20,default='egr-v2')

class SourceControl(models.Model):
    # A singleton also supplies the cross-process collector lock.
    paused = models.BooleanField(default=False)
    reason = models.TextField(blank=True)
    locked_until = models.DateTimeField(null=True)
