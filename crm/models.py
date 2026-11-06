from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator,MaxValueValidator
from django.utils import timezone

class Company(models.Model):
    country = models.CharField(max_length=2, default='BY')
    unp = models.CharField(max_length=32)
    name = models.CharField(max_length=500, blank=True)
    kind = models.PositiveSmallIntegerField(choices=[(1,'Юридическое лицо'),(2,'ИП')])
    registered_on = models.DateField(null=True)
    admitted_at = models.DateTimeField(null=True, blank=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    source_data = models.JSONField(default=dict)
    search_name = models.TextField(blank=True)
    region = models.CharField(max_length=300,blank=True,db_index=True)
    city = models.CharField(max_length=300,blank=True,db_index=True)
    district = models.CharField(max_length=300,blank=True,db_index=True)
    egr_state = models.CharField(max_length=100,blank=True,db_index=True)
    first_no_contacts = models.DateTimeField(null=True,blank=True)
    next_contact_check = models.DateTimeField(null=True,blank=True,db_index=True)
    contact_retry_stage = models.PositiveSmallIntegerField(default=0)
    source_changed_at = models.DateTimeField(null=True,blank=True)
    source_change_seen_at = models.DateTimeField(null=True,blank=True)
    activity_checked_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=32, default='Новая')
    reply_state = models.CharField(max_length=16,blank=True,default='',choices=[('', 'Ответ не отмечен'),('waiting','Ждём ответа'),('answered','Ответил'),('no_reply','Не ответил')],db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)
    do_not_contact = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    def save(self,*args,**kwargs):
        self.search_name=self.name.casefold()
        if kwargs.get('update_fields') and 'name' in kwargs['update_fields']:
            kwargs['update_fields']=set(kwargs['update_fields'])|{'search_name'}
        super().save(*args,**kwargs)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['country','unp'],name='unique_country_unp')]
    def __str__(self): return f'{self.unp} — {self.name}'
    @property
    def has_unseen_source_change(self):
        return self.source_changed_at and (not self.source_change_seen_at or self.source_changed_at>self.source_change_seen_at)
    @property
    def current_address(self):
        from .company_data import address_text
        records = self.source_data.get('current_addresses',[])
        return '; '.join(dict.fromkeys(text for record in records if isinstance(record,dict) and (text := address_text(record))))
    @property
    def current_activities(self):
        return [activity for activity in self.activities.all() if activity.current]


class Contact(models.Model):
    # Legacy 3.5 archive fields only; availability checks are disabled.
    MESSENGER_STATES=[('unknown','Не проверено'),('present','Найден вручную'),('absent','Не найден при ручной проверке')]
    telegram_state = models.CharField(max_length=10,choices=MESSENGER_STATES,default='unknown')
    viber_state = models.CharField(max_length=10,choices=MESSENGER_STATES,default='unknown')
    company = models.ForeignKey(Company,on_delete=models.CASCADE,related_name='contacts')
    kind = models.CharField(max_length=10,choices=[('phone','Телефон'),('email','Email'),('site','Сайт')])
    raw = models.TextField()
    normalized = models.CharField(max_length=300,blank=True,db_index=True)
    original_line = models.TextField(blank=True)
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)
    checked_at = models.DateTimeField(null=True,blank=True)
    comment = models.TextField(blank=True)
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
    unp = models.CharField(max_length=32, db_index=True)
    state = models.CharField(max_length=16,default='pending')
    attempts = models.PositiveSmallIntegerField(default=0)
    retry_at = models.DateTimeField(null=True,blank=True,db_index=True)
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
    lease_owner = models.CharField(max_length=64, blank=True)
    generation = models.PositiveIntegerField(default=0)

class CollectionJob(models.Model):
    key = models.CharField(max_length=80, unique=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    retry_at = models.DateTimeField(null=True,blank=True,db_index=True)
    priority = models.PositiveSmallIntegerField(default=10)
    kind = models.CharField(max_length=10)
    current_unp = models.CharField(max_length=32, blank=True)
    unp = models.CharField(max_length=32, blank=True)
    day = models.ForeignKey(ImportDay, null=True, blank=True, on_delete=models.CASCADE)
    state = models.CharField(max_length=16, default='pending', choices=[('pending','В очереди'),('running','Выполняется'),('done','Завершено'),('error','Ошибка'),('cancelled','Отменено')])
    message = models.TextField(blank=True)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

class CollectorSettings(models.Model):
    enabled = models.BooleanField(default=False)
    run_at = models.TimeField(default='09:00')
    lookback = models.PositiveSmallIntegerField(default=7)
    last_scheduled = models.DateField(null=True, blank=True)
    heartbeat = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    backup_enabled = models.BooleanField(default=True)
    last_backup = models.DateField(null=True,blank=True)

class Activity(models.Model):
    company = models.ForeignKey(Company,on_delete=models.CASCADE,related_name='activities')
    code = models.CharField(max_length=20,db_index=True)
    name = models.CharField(max_length=1000,blank=True)
    search_name = models.CharField(max_length=1000,blank=True,db_index=True)
    current = models.BooleanField(default=True,db_index=True)
    source_data = models.JSONField(default=dict)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['company','code','name'],name='unique_company_activity')]
        ordering = ['code','name']

class CollectionReset(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_jobs = models.PositiveIntegerField(default=0)

class CRMStatus(models.Model):
    name = models.CharField(max_length=32,unique=True)
    position = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)
    class Meta: ordering=['position','name']
    def __str__(self): return self.name

class Task(models.Model):
    company = models.ForeignKey(Company,on_delete=models.CASCADE,related_name='tasks')
    title = models.CharField(max_length=300)
    due_at = models.DateTimeField(db_index=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL,related_name='+')
    completed_at = models.DateTimeField(null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Interaction(models.Model):
    contact = models.ForeignKey(Contact,null=True,blank=True,on_delete=models.SET_NULL)
    company = models.ForeignKey(Company,on_delete=models.CASCADE,related_name='interactions')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    kind = models.CharField(max_length=20,choices=[('call','Звонок'),('message','Сообщение вручную'),('reply','Получен ответ'),('note','Заметка')])
    result = models.CharField(max_length=20,choices=[('attempt','Попытка'),('answered','Ответ получен'),('negotiation','Переговоры'),('client','Стал клиентом'),('refusal','Отказ'),('other','Другое')])
    note = models.TextField(blank=True)
    occurred_at = models.DateTimeField(default=timezone.now,db_index=True)

class ContactCheck(models.Model):
    contact = models.ForeignKey(Contact,on_delete=models.CASCADE,related_name='checks')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    result = models.CharField(max_length=80)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class SourceSnapshot(models.Model):
    company = models.ForeignKey(Company,on_delete=models.CASCADE,related_name='snapshots')
    fingerprint = models.CharField(max_length=64)
    data = models.JSONField(default=dict)
    observed_at = models.DateTimeField(default=timezone.now)
    class Meta: ordering=['-observed_at','-pk']

class SavedSelection(models.Model):
    name = models.CharField(max_length=200)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    filters = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

class CollectionRun(models.Model):
    def get_state_display(self):
        return {'pending':'Ожидает','running':'Выполняется','partial':'Завершён с ошибками','done':'Завершён','cancelled':'Отменён'}.get(self.state,self.state)
    progress_at = models.DateTimeField(null=True,blank=True)
    snapshot = models.JSONField(default=dict)
    title = models.CharField(max_length=250)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True,blank=True)
    state = models.CharField(max_length=20,default='pending')
    class Meta: ordering=['-created_at','-pk']

class RunDay(models.Model):
    run = models.ForeignKey(CollectionRun,on_delete=models.CASCADE,related_name='days')
    day = models.ForeignKey(ImportDay,on_delete=models.CASCADE)
    class Meta: constraints=[models.UniqueConstraint(fields=['run','day'],name='unique_run_day')]

class RunCompany(models.Model):
    run = models.ForeignKey(CollectionRun,on_delete=models.CASCADE,related_name='companies')
    company = models.ForeignKey(Company,on_delete=models.CASCADE)
    class Meta: constraints=[models.UniqueConstraint(fields=['run','company'],name='unique_run_company')]

class JobAttempt(models.Model):
    def get_state_display(self): return {'done':'Успешно','error':'Ошибка'}.get(self.state,self.state)
    elapsed_seconds = models.FloatField(null=True,blank=True)
    job = models.ForeignKey(CollectionJob,on_delete=models.CASCADE,related_name='history')
    unp = models.CharField(max_length=32,blank=True)
    state = models.CharField(max_length=20)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AccessProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='crm_profile')
    role = models.CharField(max_length=20,choices=[('owner','Владелец'),('manager','Менеджер')],default='manager')

class LoginAttempt(models.Model):
    key = models.CharField(max_length=64,unique=True)
    attempts = models.PositiveIntegerField(default=0)
    window_start = models.DateTimeField(default=timezone.now)

class BackupRecord(models.Model):
    filename = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=100,default='manual')

class WebsiteSearch(models.Model):
    def get_state_display(self):
        return {'pending':'В очереди','running':'Поиск выполняется','review':'Требует проверки','not_found':'Не найден при этом поиске','unconfigured':'Не настроено','error':'Ошибка','confirmed':'Подтверждён вручную','not_checked':'Не проверен'}.get(self.state,self.state)
    company = models.ForeignKey(Company,on_delete=models.CASCADE,related_name='website_searches')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,on_delete=models.SET_NULL)
    state = models.CharField(max_length=30,default='not_checked')
    url = models.URLField(max_length=1000,blank=True)
    evidence = models.TextField(blank=True)
    provider = models.CharField(max_length=50,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class WorkPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    daily_target = models.PositiveSmallIntegerField(default=15,validators=[MinValueValidator(1),MaxValueValidator(200)])

class WorkDay(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    day = models.DateField()
    target = models.PositiveSmallIntegerField(default=15,validators=[MinValueValidator(1),MaxValueValidator(200)])
    class Meta:
        constraints=[models.UniqueConstraint(fields=['user','day'],name='unique_user_work_day')]

class CompanyReview(models.Model):
    RESULTS=[('in_work','Взята в работу'),('negotiation','Переговоры'),('client','Клиент'),('refusal','Отказ'),('changed','Статус изменён'),('ready','Готова к обращению'),('follow_up','Вернуться позже'),('not_fit','Не подходит'),('other','Другой результат')]
    company = models.ForeignKey(Company,on_delete=models.CASCADE,related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    result = models.CharField(max_length=20,choices=RESULTS)
    note = models.TextField()
    completed_at = models.DateTimeField(default=timezone.now,db_index=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['user','company'],name='unique_user_company_review')]
