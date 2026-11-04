import io,json,tempfile,zipfile
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.management import call_command
from .models import *
from .jobs import enqueue_day,enqueue_period,run_one,schedule_due
from .backups import create_archive,restore_archive,inspect_archive
from .tests import Fake,response_body

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class CompleteWorkflowTests(TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override=override_settings(BACKUP_DIR=Path(self.directory.name),PROGRESS_CACHE_SECONDS=0)
        self.override.enable();self.addCleanup(self.override.disable)
        self.owner=get_user_model().objects.create_superuser('owner','owner@example.test','Long-test-password-843!')
        self.client.force_login(self.owner)
        self.company=Company.objects.create(unp='193787017',kind=1,name='АЛЬФА',admitted_at=timezone.now(),notes='Старая заметка',region='Минская',city='Минск')

    def archive_bytes(self):
        record=create_archive()
        return (Path(self.directory.name)/record.filename).read_bytes()

    def test_backup_roundtrip_restores_notes_contacts_tasks_and_history(self):
        contact=Contact.objects.create(company=self.company,kind='phone',raw='+375291234567',source='Вручную',do_not_contact=True)
        task=Task.objects.create(company=self.company,title='Перезвонить',due_at=timezone.now()+timedelta(days=1),owner=self.owner)
        Interaction.objects.create(company=self.company,actor=self.owner,kind='call',result='answered',note='Есть ответ')
        Audit.objects.create(company=self.company,actor=self.owner,changes={'notes':'Сохранить историю'})
        SourceSnapshot.objects.create(company=self.company,fingerprint='abc',data={'current_addresses':[{'vnp':'Минск'}]})
        CollectionRun.objects.create(title='Незавершённый запуск')
        payload=self.archive_bytes()
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            body=z.read('data.json').decode();manifest=z.read('manifest.json').decode()
            self.assertNotIn(self.owner.password,body+manifest)
            self.assertNotIn('django_session',body)
        self.company.notes='Новая заметка';self.company.save(update_fields=['notes'])
        safety=restore_archive(io.BytesIO(payload),self.owner,replace=True)
        self.company.refresh_from_db();self.assertEqual(self.company.notes,'Старая заметка')
        self.assertTrue(Contact.objects.get(pk=contact.pk).do_not_contact)
        self.assertEqual(Task.objects.get(pk=task.pk).owner_id,self.owner.pk)
        self.assertEqual(Interaction.objects.get().note,'Есть ответ')
        self.assertEqual(SourceSnapshot.objects.count(),1)
        self.assertEqual(CollectionRun.objects.get().state,'cancelled')
        self.assertIsNotNone(CollectionRun.objects.get().finished_at)
        self.assertTrue((Path(self.directory.name)/safety.filename).exists())
        self.assertTrue(SourceControl.objects.get(pk=1).paused)
        self.assertFalse(CollectorSettings.objects.get(pk=1).enabled)

    def test_restore_requires_explicit_replace(self):
        payload=self.archive_bytes()
        with self.assertRaises(ValueError): restore_archive(io.BytesIO(payload),self.owner)
        self.assertEqual(Company.objects.count(),1)

    def test_corrupted_archive_is_rejected_without_changes(self):
        payload=self.archive_bytes()
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            manifest=z.read('manifest.json');data=z.read('data.json')
        altered=io.BytesIO()
        with zipfile.ZipFile(altered,'w') as z:
            z.writestr('manifest.json',manifest);z.writestr('data.json',data+b' ')
        altered.seek(0)
        with self.assertRaises(ValueError): restore_archive(altered,self.owner,replace=True)
        self.assertEqual(Company.objects.get().notes,'Старая заметка')

    def test_unknown_table_rejected_even_with_valid_checksum(self):
        import hashlib
        payload=self.archive_bytes()
        with zipfile.ZipFile(io.BytesIO(payload)) as z: manifest=json.loads(z.read('manifest.json'))
        data=json.dumps([{'model':'auth.user','pk':123,'fields':{}}]).encode()
        manifest['sha256']=hashlib.sha256(data).hexdigest()
        altered=io.BytesIO()
        with zipfile.ZipFile(altered,'w') as z:
            z.writestr('manifest.json',json.dumps(manifest));z.writestr('data.json',data)
        altered.seek(0)
        with self.assertRaises(ValueError): inspect_archive(altered)

    def test_bad_foreign_key_rolls_back_whole_restore(self):
        import hashlib
        payload=self.archive_bytes()
        with zipfile.ZipFile(io.BytesIO(payload)) as z: manifest=json.loads(z.read('manifest.json'));records=json.loads(z.read('data.json'))
        records.append({'model':'crm.contact','pk':999,'fields':{'company':987654,'kind':'phone','raw':'123','source':'Вручную','current':True,'first_seen':timezone.now().isoformat(),'last_seen':timezone.now().isoformat(),'source_data':{},'verification':'Не проверен','do_not_contact':False,'normalized':'123','original_line':'123','checked_by':None,'checked_at':None,'comment':''}})
        data=json.dumps(records).encode();manifest['sha256']=hashlib.sha256(data).hexdigest()
        altered=io.BytesIO()
        with zipfile.ZipFile(altered,'w') as z: z.writestr('manifest.json',json.dumps(manifest));z.writestr('data.json',data)
        altered.seek(0)
        with self.assertRaises(Exception): restore_archive(altered,self.owner,replace=True)
        self.assertEqual(Company.objects.get().notes,'Старая заметка')

    def test_backup_ui_download_and_all_new_pages(self):
        response=self.client.post('/backups/',{'action':'create'})
        self.assertEqual(response.status_code,302)
        file=self.client.get(response.url)
        self.assertTrue(response_body(file).startswith(b'PK'))
        for page in ['/tasks/','/candidates/','/selections/','/runs/','/settings/','/diagnostics/','/backups/','/companies/fragment/',f'/companies/{self.company.pk}/website/']:
            self.assertEqual(self.client.get(page).status_code,200,page)

    def test_cyrillic_search_and_geography(self):
        self.assertContains(self.client.get('/?q=альфа'),'АЛЬФА')
        self.assertContains(self.client.get('/?city=минск'),'АЛЬФА')
        self.assertNotContains(self.client.get('/?city=Брест'),'Старая заметка')

    def test_current_filter_without_type(self):
        Contact.objects.create(company=self.company,kind='phone',raw='1',source='ЕГР',current=False)
        self.assertEqual(self.client.get('/?current=1').context['rows'].paginator.count,0)
        Contact.objects.create(company=self.company,kind='email',raw='a@b.by',source='ЕГР',current=True)
        self.assertEqual(self.client.get('/?current=1').context['rows'].paginator.count,1)

    def test_selected_xlsx_has_text_cells_and_only_selected_companies(self):
        from openpyxl import load_workbook
        other=Company.objects.create(unp='002',kind=1,name='Другая',admitted_at=timezone.now())
        self.company.name='=HYPERLINK("bad")';self.company.save(update_fields=['name'])
        response=self.client.post('/companies/export/?format=xlsx',{'selected':[self.company.pk]})
        workbook=load_workbook(io.BytesIO(response_body(response)))
        rows=list(workbook.active.rows)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[1][0].value,'193787017')
        self.assertEqual(rows[1][1].data_type,'s')
        self.assertEqual(rows[1][1].value,'=HYPERLINK("bad")')

    def test_tasks_interactions_checks_and_readable_history(self):
        self.client.post(f'/companies/{self.company.pk}/',{'action':'task','title':'Позвонить','due_at':'2026-11-06T10:00','owner':self.owner.pk})
        task=Task.objects.get();self.assertEqual(task.title,'Позвонить')
        self.client.post('/tasks/',{'task':task.pk,'action':'complete'})
        task.refresh_from_db();self.assertIsNotNone(task.completed_at)
        self.client.post(f'/companies/{self.company.pk}/',{'action':'interaction','kind':'call','result':'answered','note':'Ответил сотрудник','occurred_at':'2026-11-05T12:00'})
        self.assertEqual(Interaction.objects.get().result,'answered')
        contact=Contact.objects.create(company=self.company,kind='phone',raw='123',source='Вручную')
        self.client.post(f'/contacts/{contact.pk}/check/',{'verification':'ЛПР','comment':'Подтвердил роль','do_not_contact':'on'})
        contact.refresh_from_db();self.assertEqual(contact.checked_by,self.owner)
        self.assertTrue(contact.do_not_contact)
        self.assertContains(self.client.get(f'/companies/{self.company.pk}/'),'Ответил сотрудник')
        self.assertContains(self.client.get('/statistics/'),'100')

    def test_ban_blocks_outreach_record_and_filtered_export(self):
        self.company.do_not_contact=True;self.company.save(update_fields=['do_not_contact'])
        self.client.post(f'/companies/{self.company.pk}/',{'action':'interaction','kind':'call','result':'answered','occurred_at':'2026-11-05T12:00'})
        self.assertFalse(Interaction.objects.exists())
        self.assertEqual(self.client.get('/?outreach=1').context['rows'].paginator.count,0)

    def test_saved_selection_and_bulk(self):
        self.client.post('/selections/',{'action':'save','name':'Минск','filters':'city=Минск&oked=62'})
        self.assertEqual(SavedSelection.objects.get().filters,{'city':'Минск','oked':'62'})
        self.client.post('/bulk/',{'selected':[self.company.pk],'action':'status','status':'Клиент'})
        self.company.refresh_from_db();self.assertEqual(self.company.status,'Клиент')
        self.assertTrue(Audit.objects.filter(company=self.company).exists())

    def test_manager_can_work_but_cannot_restore_or_start_collection(self):
        manager=get_user_model().objects.create_user('manager',password='Manager-password-123!')
        AccessProfile.objects.create(user=manager,role='manager')
        self.client.force_login(manager)
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/tasks/').status_code,200)
        self.assertEqual(self.client.post('/candidates/',{'selected':[self.company.pk]}).status_code,403)
        for path in ['/backups/','/settings/','/imports/','/imports/reset/']:
            self.assertEqual(self.client.get(path).status_code,403,path)
        self.assertEqual(self.client.post('/backups/',{'action':'create'}).status_code,403)

    def test_unconfigured_search_is_not_fake_result(self):
        with patch.dict('os.environ',{},clear=True):
            self.client.post(f'/companies/{self.company.pk}/website/',{'cost_confirm':'on'})
        self.assertEqual(WebsiteSearch.objects.get().state,'unconfigured')

    def test_website_rejects_private_addresses(self):
        from .websites import safe_url
        for url in ['http://127.0.0.1/','http://10.0.0.1/','http://localhost/','javascript:alert(1)','http://[::1]/']:
            with self.assertRaises(ValueError): safe_url(url)
        self.assertEqual(safe_url('https://example.org/'),'https://example.org/')

    def test_login_rate_limit(self):
        self.client.logout()
        for _ in range(8): self.client.post('/login/',{'username':'owner','password':'bad'})
        self.assertEqual(self.client.post('/login/',{'username':'owner','password':'bad'}).status_code,429)

class ReliableCollectorTests(TestCase):
    @patch('crm.jobs.Client',return_value=Fake())
    def test_failed_candidate_does_not_block_next_company(self,client):
        job=enqueue_day(date(2026,11,3));job.day.discovered=True;job.day.save()
        bad=Candidate.objects.create(day=job.day,unp='111');good=Candidate.objects.create(day=job.day,unp='123')
        from .importer import enrich as original
        def enrich_(transport,unp):
            if unp=='111': raise __import__('crm.egr',fromlist=['SourceError']).SourceError('503')
            return original(Fake(),unp)
        with patch('crm.jobs.enrich',side_effect=enrich_):
            run_one();run_one()
        bad.refresh_from_db();good.refresh_from_db();job.refresh_from_db()
        self.assertEqual(bad.state,'error');self.assertEqual(good.state,'done')
        self.assertIsNotNone(bad.retry_at)
        self.assertEqual(job.state,'pending')
        self.assertEqual(JobAttempt.objects.filter(state='error').count(),1)

    def test_contact_retries_at_seven_and_thirty_days(self):
        now=timezone.now()
        row=Company.objects.create(unp='123',kind=1,first_no_contacts=now-timedelta(days=8),next_contact_check=now-timedelta(days=1))
        schedule_due(now)
        row.refresh_from_db();self.assertEqual(row.contact_retry_stage,1)
        self.assertEqual(row.next_contact_check.date(),(row.first_no_contacts+timedelta(days=30)).date())
        job=CollectionJob.objects.get();job.state='done';job.save()
        row.next_contact_check=now-timedelta(seconds=1);row.save(update_fields=['next_contact_check'])
        schedule_due(now)
        row.refresh_from_db();self.assertEqual(row.contact_retry_stage,2);self.assertIsNone(row.next_contact_check)

    def test_catchup_includes_all_missed_days(self):
        from datetime import datetime,timezone as utc
        CollectorSettings.objects.create(pk=1,enabled=True,run_at='09:00',lookback=7,last_scheduled=date(2026,10,1))
        schedule_due(datetime(2026,11,5,10,0,tzinfo=utc.utc))
        self.assertEqual(ImportDay.objects.earliest('day').day,date(2026,10,1))
        self.assertEqual(ImportDay.objects.latest('day').day,date(2026,11,4))

    @patch('crm.jobs.Client',return_value=Fake())
    def test_run_keeps_results(self,client):
        run=enqueue_period(date(2026,11,3),date(2026,11,3))
        day=ImportDay.objects.get();day.discovered=True;day.save()
        Candidate.objects.create(day=day,unp='123')
        run_one();run.refresh_from_db()
        self.assertEqual(run.state,'done');self.assertEqual(run.snapshot['ready'],1)
        self.assertEqual(RunCompany.objects.get().company.unp,'123')

    def test_import_saves_only_source_fields(self):
        from .importer import enrich
        row=Company.objects.create(unp='123',kind=1,notes='До',status='Клиент',do_not_contact=True)
        saved=[]
        original=Company.save
        def spy(instance,*args,**kwargs):
            if instance.pk==row.pk: saved.append(set(kwargs.get('update_fields',[])))
            return original(instance,*args,**kwargs)
        with patch.object(Company,'save',spy): enrich(Fake(),'123')
        self.assertTrue(saved)
        self.assertTrue(all('notes' not in fields and 'status' not in fields and 'do_not_contact' not in fields for fields in saved))

    def test_contact_normalization_and_split_without_guessing(self):
        from .normalization import normalized_contact,contact_parts
        self.assertEqual(normalized_contact('phone','+375 (29) 123-45-67'),'+375291234567')
        self.assertEqual(len(contact_parts('phone','+375291234567; +375331234567')),2)
        self.assertEqual(contact_parts('phone','123 / неизвестно'),['123 / неизвестно'])

from django.test import TransactionTestCase,SimpleTestCase
from django.db import connection
from unittest import skipUnless

@skipUnless(connection.vendor=='postgresql','Concurrent row-lock integration requires PostgreSQL')
class ConcurrentImportTests(TransactionTestCase):
    def test_manual_edit_waits_and_survives_source_import(self):
        import threading
        from django.db import close_old_connections,transaction
        from .importer import enrich
        row=Company.objects.create(unp='123',kind=1,notes='До импорта')
        SourceControl.objects.create(pk=1)
        entered=threading.Event();release=threading.Event();edited=threading.Event();errors=[]
        original=Company.save
        def controlled(instance,*args,**kwargs):
            if 'name' in kwargs.get('update_fields',[]):
                entered.set()
                if not release.wait(5): raise RuntimeError('Timed out waiting for concurrency test')
            return original(instance,*args,**kwargs)
        def importer():
            close_old_connections()
            try: enrich(Fake(),'123')
            except Exception as exc: errors.append(exc)
            finally: close_old_connections()
        def editor():
            close_old_connections()
            try:
                with transaction.atomic():
                    SourceControl.objects.select_for_update().get(pk=1)
                    Company.objects.filter(pk=row.pk).update(notes='Ручная правка параллельно импорту')
                edited.set()
            except Exception as exc: errors.append(exc)
            finally: close_old_connections()
        with patch.object(Company,'save',controlled):
            first=threading.Thread(target=importer);first.start()
            self.assertTrue(entered.wait(5))
            second=threading.Thread(target=editor);second.start()
            release.set();first.join(8);second.join(8)
        self.assertFalse(first.is_alive());self.assertFalse(second.is_alive());self.assertFalse(errors,errors)
        self.assertTrue(edited.is_set());row.refresh_from_db()
        self.assertEqual(row.notes,'Ручная правка параллельно импорту')

class LocalStorageTests(SimpleTestCase):
    def test_prepare_local_copies_old_database_and_retains_existing_stable_data(self):
        import sqlite3,shutil,subprocess,os
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'program';root.mkdir()
            stable=Path(directory)/'stable'
            script=Path(__file__).resolve().parent.parent/'prepare_local.py'
            shutil.copy2(script,root/'prepare_local.py')
            with sqlite3.connect(root/'dev.sqlite3') as db:
                db.execute('CREATE TABLE notes(value TEXT)');db.execute("INSERT INTO notes VALUES ('original')")
            env=dict(os.environ,CRM_DATA_DIR=str(stable))
            import sys
            subprocess.run([sys.executable,'-c',"import runpy,sys; from unittest.mock import patch\nwith patch('socket.create_connection',side_effect=ConnectionRefusedError): runpy.run_path(sys.argv[1],run_name='__main__')",str(root/'prepare_local.py')],env=env,check=True,capture_output=True)
            self.assertTrue((root/'dev.sqlite3').exists());self.assertTrue((stable/'.local-key').exists())
            with sqlite3.connect(stable/'dev.sqlite3') as db: db.execute("UPDATE notes SET value='new work'")
            subprocess.run([sys.executable,'-c',"import runpy,sys; from unittest.mock import patch\nwith patch('socket.create_connection',side_effect=ConnectionRefusedError): runpy.run_path(sys.argv[1],run_name='__main__')",str(root/'prepare_local.py')],env=env,check=True,capture_output=True)
            with sqlite3.connect(stable/'dev.sqlite3') as db: self.assertEqual(db.execute('SELECT value FROM notes').fetchone()[0],'new work')
            self.assertEqual(len(list((stable/'backups').glob('*.sqlite3'))),2)

    def test_refuses_copy_while_old_app_is_running(self):
        import runpy,shutil,os
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'old';root.mkdir()
            stable=Path(directory)/'stable'
            script=Path(__file__).resolve().parent.parent/'prepare_local.py'
            shutil.copy2(script,root/'prepare_local.py')
            (root/'dev.sqlite3').write_text('must-not-be-read')
            with patch.dict(os.environ,{'CRM_DATA_DIR':str(stable)}),patch('socket.create_connection'):
                with self.assertRaises(SystemExit): runpy.run_path(str(root/'prepare_local.py'),run_name='__main__')
            self.assertFalse(stable.exists())
            self.assertEqual((root/'dev.sqlite3').read_text(),'must-not-be-read')

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class AdditionalTransferTests(TestCase):
    setUp=CompleteWorkflowTests.setUp
    archive_bytes=CompleteWorkflowTests.archive_bytes
    def test_future_schema_refused(self):
        data=self.archive_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as z: manifest=json.loads(z.read('manifest.json'));payload=z.read('data.json')
        manifest['migrations'].append('9999_future_version')
        file=io.BytesIO()
        with zipfile.ZipFile(file,'w') as z: z.writestr('manifest.json',json.dumps(manifest));z.writestr('data.json',payload)
        file.seek(0)
        with self.assertRaisesMessage(ValueError,'более новой версии'): restore_archive(file,self.owner,replace=True)
        self.assertEqual(Company.objects.get().notes,'Старая заметка')

    def test_http_restore_roundtrip(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        data=self.archive_bytes()
        self.company.notes='После копии';self.company.save(update_fields=['notes'])
        response=self.client.post('/backups/',{'action':'restore','confirm':'ВОССТАНОВИТЬ','replace':'on','archive':SimpleUploadedFile('backup.zip',data,content_type='application/zip')},follow=True)
        self.assertContains(response,'База восстановлена')
        self.company.refresh_from_db();self.assertEqual(self.company.notes,'Старая заметка')

    def test_old_archive_missing_derived_search_field_is_upgraded(self):
        import hashlib
        data=self.archive_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as z: manifest=json.loads(z.read('manifest.json'));records=json.loads(z.read('data.json'))
        for record in records:
            if record['model']=='crm.company': record['fields'].pop('search_name',None)
        payload=json.dumps(records).encode();manifest['sha256']=hashlib.sha256(payload).hexdigest()
        file=io.BytesIO()
        with zipfile.ZipFile(file,'w') as z: z.writestr('manifest.json',json.dumps(manifest));z.writestr('data.json',payload)
        file.seek(0);restore_archive(file,self.owner,replace=True)
        self.assertContains(self.client.get('/?q=альфа'),'АЛЬФА')

    def test_paid_provider_returns_review_candidates_not_confirmation(self):
        from .websites import search_website,run_search_once
        with patch.dict('os.environ',{'BRAVE_API_KEY':'test-key-not-a-real-secret'}):
            job=search_website(self.company,self.owner,confirmed=True)
            with patch('crm.websites.urlopen') as transport:
                transport.return_value.__enter__.return_value.read.return_value=json.dumps({'web':{'results':[{'url':'https://example.org','title':'АЛЬФА','description':'УНП 193787017'},{'url':'http://127.0.0.1','title':'bad'}]}}).encode()
                run_search_once()
        job.refresh_from_db();self.assertEqual(job.state,'review')
        candidates=json.loads(job.evidence);self.assertEqual(len(candidates),1);self.assertTrue(candidates[0]['unp_match'])


class ScheduleDepthTests(TestCase):
    def test_configured_ten_day_lookback_rechecks_eighth_day(self):
        now=timezone.localtime()
        day=now.date()-timedelta(days=8)
        job=enqueue_day(day);job.state='done';job.save()
        job.day.discovered=True;job.day.state='done';job.day.save()
        Candidate.objects.create(day=job.day,unp='123',state='done')
        CollectorSettings.objects.create(pk=1,enabled=True,run_at='00:00',lookback=10)
        schedule_due(now)
        job.day.refresh_from_db();self.assertFalse(job.day.discovered)
        self.assertEqual(Candidate.objects.get(unp='123').state,'pending')

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class RestorePaidJobTests(TestCase):
    def test_restore_does_not_resume_paid_search(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        with override_settings(BACKUP_DIR=Path(directory.name)):
            user=get_user_model().objects.create_superuser('backup-owner','a@b.test','Strong-password-765!')
            company=Company.objects.create(unp='193787017',kind=1)
            WebsiteSearch.objects.create(company=company,state='pending',actor=user)
            record=create_archive()
            with open(Path(directory.name)/record.filename,'rb') as stream: restore_archive(stream,user,replace=True)
            self.assertEqual(WebsiteSearch.objects.get().state,'not_checked')

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class EditingTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('edit-owner','a@b.test','A-strong-password-123!')
        self.client.force_login(self.owner)
        self.company=Company.objects.create(unp='193787017',kind=1,admitted_at=timezone.now())
    def test_edit_task_deadline_and_journal(self):
        task=Task.objects.create(company=self.company,title='Старая задача',due_at=timezone.now(),owner=self.owner)
        response=self.client.post(f'/tasks/{task.pk}/edit/',{'title':'Новая задача','due_at':'2026-11-07T14:00','owner':self.owner.pk})
        self.assertEqual(response.status_code,302)
        task.refresh_from_db();self.assertEqual(task.title,'Новая задача')
        self.assertEqual(timezone.localtime(task.due_at).hour,14)
        self.assertTrue(Audit.objects.filter(company=self.company).exists())
    def test_rename_status_preserves_cards_and_is_audited(self):
        status=CRMStatus.objects.get(name='Новая')
        response=self.client.post(f'/settings/statuses/{status.pk}/edit/',{'name':'Новые лиды','position':0,'active':'on'})
        self.assertEqual(response.status_code,302)
        self.company.refresh_from_db();self.assertEqual(self.company.status,'Новые лиды')
        self.assertTrue(Audit.objects.filter(company=self.company).exists())
