import hashlib, json, sys
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, connection
from django.db.models import Q

def owners():
    return get_user_model().objects.filter(is_active=True).filter(Q(is_superuser=True)|Q(crm_profile__role='owner')|(Q(is_staff=True)&Q(crm_profile__isnull=True)))
from django.db.migrations.executor import MigrationExecutor

class Command(BaseCommand):
    def add_arguments(self,p):p.add_argument('action',choices=['inspect','create','reset'])
    def handle(self,*args,**options):
        if not settings.LOCAL_MODE:raise CommandError('Desktop mode is required')
        action=options['action']
        if action=='inspect':
            executor=MigrationExecutor(connection)
            pending=bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))
            owner_names=[]
            if not pending:
                owner_names=list(owners().values_list('username',flat=True))
            self.stdout.write(json.dumps({'pending':pending,'owners':owner_names}));return
        payload=json.loads(sys.stdin.read(16384))
        username=str(payload.get('username','')).strip();password=payload.get('password','')
        User=get_user_model()
        try:
            if not username or len(username)>150:raise ValidationError('Укажите логин длиной до 150 символов.')
            validate_password(password)
            with transaction.atomic():
                if action=='create':
                    if owners().exists():raise ValidationError('Владелец уже существует.')
                    user=User.objects.create_superuser(username=username,password=password)
                    from crm.models import AccessProfile
                    AccessProfile.objects.update_or_create(user=user,defaults={'role':'owner'})
                else:
                    user=owners().filter(username=username).first()
                    if user is None:raise ValidationError('Активный владелец с таким логином не найден.')
                    user.set_password(password);user.save(update_fields=['password'])
                    from crm.models import LoginAttempt
                    for address in ('127.0.0.1','::1'):
                        key=hashlib.sha256((username.casefold()+'|'+address).encode()).hexdigest()
                        LoginAttempt.objects.filter(key=key).delete()
            self.stdout.write(json.dumps({'ok':True}))
        except ValidationError as error:self.stdout.write(json.dumps({'ok':False,'error':' '.join(error.messages)},ensure_ascii=False))
