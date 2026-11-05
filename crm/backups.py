"""Versioned, checked business-data archives; never execute uploaded content."""
import hashlib, json, tempfile, zipfile
from pathlib import Path
from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.db import connection, transaction
from django.contrib.auth import get_user_model
from django.utils import timezone

FORMAT=1
# Explicit topological order, independent of model declaration order.
MODELS=['CRMStatus','Company','Contact','Audit','ImportDay','Candidate','Receipt','CollectorSettings','Activity','CollectionReset','Task','Interaction','ContactCheck','SourceSnapshot','SavedSelection','CollectionJob','CollectionRun','RunDay','RunCompany','JobAttempt','WebsiteSearch','WorkPreference','WorkDay','CompanyReview']
LIMIT=500*1024*1024

def create_archive(reason='manual'):
    settings.BACKUP_DIR.mkdir(parents=True,exist_ok=True)
    from uuid import uuid4
    name='egrcrm-'+timezone.now().strftime('%Y%m%d-%H%M%S-%f')+'-'+uuid4().hex[:8]+'.zip'
    target=settings.BACKUP_DIR/name
    with tempfile.TemporaryDirectory() as directory:
        data=Path(directory)/'data.json'
        nested=connection.in_atomic_block
        with transaction.atomic():
            if connection.vendor=='postgresql' and not nested:
                with connection.cursor() as cursor: cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            def objects():
                for name_ in MODELS:
                    yield from apps.get_model('crm',name_).objects.order_by('pk').iterator(chunk_size=500)
            with data.open('w',encoding='utf-8') as stream:
                serializers.serialize('json',objects(),stream=stream,ensure_ascii=False)
            users=list(get_user_model().objects.values('id','username','first_name','last_name'))
        digest=hashlib.file_digest(data.open('rb'),'sha256').hexdigest()
        from django.db.migrations.recorder import MigrationRecorder
        manifest={'format':FORMAT,'version':settings.APP_VERSION,'created_at':timezone.now().isoformat(),'sha256':digest,'users':users,'migrations':list(MigrationRecorder.Migration.objects.filter(app='crm').values_list('name',flat=True))}
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.write(data,'data.json')
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
    from .models import BackupRecord
    checksum=hashlib.file_digest(target.open('rb'),'sha256').hexdigest()
    import os,shutil
    extra=os.environ.get('CRM_BACKUP_COPY_DIR')
    if extra:
        destination=Path(extra).expanduser();destination.mkdir(parents=True,exist_ok=True)
        if destination.resolve()!=settings.BACKUP_DIR.resolve(): shutil.copy2(target,destination/target.name)
    record=BackupRecord.objects.create(filename=target.name,sha256=checksum,reason=reason)
    return record

def inspect_archive(upload):
    try:
        with zipfile.ZipFile(upload) as archive:
            if sorted(archive.namelist())!=['data.json','manifest.json']: raise ValueError('Неверный состав архива')
            if archive.getinfo('manifest.json').file_size>1024*1024 or archive.getinfo('data.json').file_size>LIMIT: raise ValueError('Архив слишком велик')
            manifest=json.loads(archive.read('manifest.json'))
            payload=archive.read('data.json')
        if manifest.get('format')!=FORMAT: raise ValueError('Неподдерживаемая версия архива; обновите CRM')
        from django.db.migrations.recorder import MigrationRecorder
        known=set(MigrationRecorder.Migration.objects.filter(app='crm').values_list('name',flat=True))
        if not set(manifest.get('migrations',[]))<=known: raise ValueError('Архив от более новой версии. Сначала обновите программу.')
        if hashlib.sha256(payload).hexdigest()!=manifest.get('sha256'): raise ValueError('Контрольная сумма не совпала: архив повреждён')
        records=json.loads(payload)
        allowed={'crm.'+name.lower() for name in MODELS}
        if not isinstance(records,list) or any(not isinstance(record,dict) or record.get('model') not in allowed or not isinstance(record.get('fields'),dict) for record in records): raise ValueError('В архиве посторонние таблицы')
        return manifest,records
    except (zipfile.BadZipFile,KeyError,UnicodeError,json.JSONDecodeError,TypeError) as exc:
        raise ValueError('Файл не является корректной резервной копией CRM') from exc

def restore_archive(upload,actor,replace=False):
    manifest,records=inspect_archive(upload)
    from .models import Company,SourceControl,CollectorSettings
    if Company.objects.exists() and not replace: raise ValueError('Для замены существующей базы требуется явное подтверждение')
    # Lock ordering matches all UI mutations and importer persistence.
    with transaction.atomic():
        SourceControl.objects.get_or_create(pk=1)
        source=SourceControl.objects.select_for_update().get(pk=1)
        if source.locked_until and source.locked_until>timezone.now(): raise ValueError('Сначала остановите/сбросьте сбор и дождитесь завершения активного запроса')
        # This copy is retained even if validation/restoration below rolls back.
        safety=create_archive('before-restore')
        mapping={}
        for person in manifest.get('users',[]):
            username=person.get('username')
            if not isinstance(username,str) or not 1<=len(username)<=150: raise ValueError('Некорректный пользователь в архиве')
            user=get_user_model().objects.filter(username=username).first()
            if user is None:
                user=get_user_model().objects.create(username=username,first_name=person.get('first_name',''),last_name=person.get('last_name',''),is_active=False)
                user.set_unusable_password();user.save(update_fields=['password'])
            mapping[person['id']]=user.pk
        # Strict deserialization catches unknown future fields before business deletion.
        for record in records:
            model=apps.get_model(record['model'])
            fields=record.get('fields',{})
            for field in model._meta.fields:
                if field.is_relation and field.related_model==get_user_model() and fields.get(field.name) is not None:
                    if fields[field.name] not in mapping: raise ValueError('Неизвестный владелец записи')
                    fields[field.name]=mapping[fields[field.name]]
            if model._meta.model_name=='collectorsettings': fields.update(enabled=False,heartbeat=None,last_backup=None)
            if model._meta.model_name=='websitesearch' and fields.get('state') in ('pending','running'):
                fields.update(state='not_checked',evidence='Незавершённый поиск перенесён из копии; требуется новая ручная команда с подтверждением расходов.')
            if model._meta.model_name in ('collectionjob','candidate','importday','collectionrun') and fields.get('state') in ('pending','running'):
                fields['state']='cancelled'
                if model._meta.model_name=='collectionrun': fields['finished_at']=timezone.now().isoformat()
        list(serializers.deserialize('json',json.dumps(records)))
        with connection.constraint_checks_disabled():
            for name in reversed(MODELS): apps.get_model('crm',name).objects.all().delete()
            for item in serializers.deserialize('json',json.dumps(records)):
                # Field/schema validation without querying not-yet-restored foreign keys.
                for field in item.object._meta.fields:
                    if not field.is_relation:
                        value=field.to_python(field.value_from_object(item.object))
                        if value is None and not field.null and not field.primary_key: raise ValueError('Отсутствует обязательное поле '+field.name)
                        if value not in field.empty_values:
                            field.validate(value,item.object);field.run_validators(value)
                item.save()
        connection.check_constraints()
        from .normalization import normalized_contact
        for company in Company.objects.all().iterator():
            company.search_name=company.name.casefold();company.save(update_fields=['search_name'])
        Contact=apps.get_model('crm','Contact')
        for contact in Contact.objects.filter(normalized='').iterator():
            contact.normalized=normalized_contact(contact.kind,contact.raw);contact.original_line=contact.original_line or contact.raw
            contact.save(update_fields=['normalized','original_line'])
        source.generation+=1;source.paused=True;source.reason='База восстановлена. Проверьте данные и настройте расписание.'
        source.lease_owner='';source.locked_until=None;source.save()
        CollectorSettings.objects.get_or_create(pk=1)
        # Reset PostgreSQL sequences after importing explicit primary keys.
        from django.core.management.color import no_style
        for sql in connection.ops.sequence_reset_sql(no_style(),[apps.get_model('crm',n) for n in MODELS]):
            with connection.cursor() as cursor: cursor.execute(sql)
        return safety
