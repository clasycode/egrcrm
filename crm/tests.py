from datetime import date
from unittest.mock import patch
from io import BytesIO
from urllib.error import HTTPError
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from .models import Company,Contact,ImportDay,Candidate,Receipt,SourceControl
from .importer import enrich,discover,local_date
from .egr import Client,SourceError,SourcePaused

def response_body(response):
    if not hasattr(response,'_test_body'):
        response._test_body=b''.join(response.streaming_content) if response.streaming else response.content
    return response._test_body

class Fake:
    def __init__(self,kind=1,phone='123',email='',history=None):
        self.kind,self.phone,self.email,self.history = kind,phone,email,history
    def get(self,method,*args):
        if method=='getBaseInfoByRegNum': return [{'ngrn':123,'dfrom':'2026-11-02T21:00:00Z','nsi00211':{'nkvob':self.kind}}]
        if 'Names' in method or 'FIO' in method: return [{'ngrn':123,'vnaim':'Компания <script>'}]
        if method=='getAllAddressByRegNum': return self.history or []
        if method=='getVEDByRegNum': return [{'ngrn':123,'nsi00114':{'vkvdn':'62010','vnvdnp':'Компьютерное программирование'}}]
        return [{'ngrn':123,'vtels':self.phone,'vemail':self.email}]

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class WorkflowTests(TestCase):
    def test_admission_rules(self):
        for kind,phone,email,expected in [(1,'','a@b.by',True),(2,'','a@b.by',False),(2,'123','',True),(1,'','',False)]:
            Company.objects.all().delete()
            self.assertEqual(bool(enrich(Fake(kind,phone,email),'123').admitted_at),expected)
    def test_history_and_manual_work_survive(self):
        row = enrich(Fake(),'123')
        row.status,row.notes,row.do_not_contact = 'Клиент','Ручная заметка',True
        row.save()
        enrich(Fake(phone='456',history=[{'ngrn':123,'vtels':'123'}]),'123')
        row.refresh_from_db()
        self.assertEqual((row.status,row.notes,row.do_not_contact),('Клиент','Ручная заметка',True))
        self.assertFalse(row.contacts.get(raw='123').current)
        self.assertTrue(row.contacts.get(raw='456').current)
        self.assertEqual(Company.objects.count(),1)
        self.assertEqual(row.contacts.count(),2)
    def test_minsk_boundary(self):
        self.assertEqual(local_date('2026-11-02T21:00:00Z'),date(2026,11,3))
    def test_discovery_only_creations(self):
        day = ImportDay.objects.create(day=date(2026,11,3))
        class Daily:
            def get(self,*args): return [{'ngrn':123,'dfrom':'2026-11-02T21:00:00Z','nsi00208':{'nkscrt':1},'nsi00211':{'nkvob':1}},{'ngrn':456,'dfrom':'2026-11-03','nsi00208':{'nkscrt':2}}]
        discover(Daily(),day); discover(Daily(),day)
        self.assertEqual(Candidate.objects.count(),1)
    def test_private_routes(self):
        row = enrich(Fake(),'123')
        for path in ['/',f'/companies/{row.pk}/','/imports/']:
            self.assertEqual(self.client.get(path).status_code,302)
        outsider = get_user_model().objects.create_user('outsider',password='test-password')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get('/').status_code,302)
        self.assertEqual(self.client.post(f'/companies/{row.pk}/',{'notes':'attack'}).status_code,302)
        row.refresh_from_db(); self.assertEqual(row.notes,'')
    def test_owner_render_and_audit(self):
        user = get_user_model().objects.create_user('owner',password='test-password',is_staff=True)
        self.client.force_login(user)
        row = enrich(Fake(),'123')
        response = self.client.get(f'/companies/{row.pk}/')
        self.assertContains(response,'&lt;script&gt;')
        self.client.post(f'/companies/{row.pk}/',{'notes':'заметка','status':'Клиент','do_not_contact':'on'})
        row.refresh_from_db()
        self.assertEqual(row.status,'Клиент'); self.assertTrue(row.do_not_contact)
        self.assertEqual(row.audit.count(),1)
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/imports/').status_code,200)
    def test_worker_initializes_calendar_days(self):
        with patch('crm.jobs.discover',side_effect=SourceError('offline')):
            call_command('collect_egr',initialize=True,limit=1)
        from .models import CollectionJob
        self.assertEqual(CollectionJob.objects.filter(state='error').count(),1)
        self.assertEqual(ImportDay.objects.count(),365)
        self.assertFalse(SourceControl.objects.get(pk=1).locked_until)

class TransportTests(TestCase):
    @patch('crm.egr.time.sleep')
    @patch('crm.egr.urlopen')
    def test_204(self,open_,sleep):
        response = open_.return_value.__enter__.return_value
        response.status=204; response.read.return_value=b''
        self.assertEqual(Client().get('getBaseInfoByRegNum','123'),[])
        self.assertEqual(Receipt.objects.count(),1)
    @patch('crm.egr.time.sleep')
    @patch('crm.egr.urlopen')
    def test_503_is_error(self,open_,sleep):
        open_.side_effect=lambda *a,**kw: (_ for _ in ()).throw(HTTPError('https://egr.gov.by',503,'Unavailable',{},BytesIO(b'error')))
        with self.assertRaises(SourceError): Client().get('x','123')
        self.assertEqual(open_.call_count,3)
    @patch('crm.egr.time.sleep')
    @patch('crm.egr.urlopen')
    def test_429_pauses_source(self,open_,sleep):
        open_.side_effect=HTTPError('https://egr.gov.by',429,'Limit',{},BytesIO(b'limit'))
        with self.assertRaises(SourcePaused): Client().get('x','123')
        self.assertTrue(SourceControl.objects.get(pk=1).paused)
        self.assertEqual(open_.call_count,1)
    @patch('crm.egr.time.sleep')
    @patch('crm.egr.urlopen')
    def test_wrong_json_structure(self,open_,sleep):
        response = open_.return_value.__enter__.return_value
        response.status=200; response.read.return_value=b'{"error":"unexpected"}'
        with self.assertRaises(SourceError): Client().get('x','123')

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class QueueAndUITests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('owner',is_staff=True)
        self.client.force_login(self.user)
    def test_lookup_validates_and_queues_without_network(self):
        from .models import CollectionJob
        with patch('crm.egr.urlopen') as network:
            response=self.client.post('/imports/',{'action':'lookup','unp':'193787017'})
            self.assertEqual(response.status_code,302)
            self.assertEqual(CollectionJob.objects.get().unp,'193787017')
            self.assertFalse(network.called)
        self.client.post('/imports/',{'action':'lookup','unp':'bad'})
        self.assertEqual(CollectionJob.objects.count(),1)
    def test_period_and_export(self):
        from .models import CollectionJob
        from datetime import timedelta
        from django.utils import timezone
        day=timezone.localdate()-timedelta(days=1)
        self.client.post('/imports/',{'action':'period','start':day.isoformat(),'end':day.isoformat()})
        job=CollectionJob.objects.get()
        Candidate.objects.create(day=job.day,unp='193787017')
        response=self.client.get(f'/imports/{job.day_id}/export/')
        self.assertEqual(response.status_code,200)
        self.assertIn("'193787017",response_body(response).decode('utf-8-sig'))
        self.assertContains(self.client.get('/imports/?tab=progress'),'Готовые компании CSV')
    def test_future_and_reversed_period_rejected(self):
        from .models import CollectionJob
        for start,end in [('2099-01-01','2099-01-02'),('2026-01-02','2026-01-01')]:
            self.client.post('/imports/',{'action':'period','start':start,'end':end})
        self.assertFalse(CollectionJob.objects.exists())
    @patch('crm.jobs.Client',return_value=Fake())
    def test_lookup_worker_and_result(self,fake):
        from .jobs import enqueue_lookup,run_one
        job=enqueue_lookup('123')
        self.assertTrue(run_one())
        job.refresh_from_db()
        self.assertEqual(job.state,'done')
        self.assertIsNotNone(job.company.admitted_at)
        self.assertContains(self.client.get(f'/jobs/{job.pk}/'),'Открыть карточку')
        self.assertIsNone(SourceControl.objects.get(pk=1).locked_until)
    @patch('crm.jobs.Client',return_value=Fake(kind=2,phone='',email='a@b.by'))
    def test_ineligible_result_is_visible_but_not_in_crm(self,fake):
        from .jobs import enqueue_lookup,run_one
        job=enqueue_lookup('123');run_one();job.refresh_from_db()
        self.assertIsNone(job.company.admitted_at)
        self.assertContains(self.client.get(f'/jobs/{job.pk}/'),'нет подходящего контакта')
        self.assertNotContains(self.client.get('/'),'Компания &lt;script&gt;')
    @patch('crm.jobs.enrich',side_effect=SourceError('ЕГР недоступен'))
    def test_failure_is_visible_and_retry_is_explicit(self,enrich_):
        from .jobs import enqueue_lookup,run_one
        job=enqueue_lookup('193787017');run_one();job.refresh_from_db()
        self.assertEqual(job.state,'error')
        self.assertContains(self.client.get(f'/jobs/{job.pk}/'),'ЕГР недоступен')
        self.assertFalse(run_one())
        self.client.post('/imports/',{'action':'retry','job':job.pk})
        job.refresh_from_db();self.assertEqual(job.state,'pending')
    def test_paused_source_makes_no_requests(self):
        from .jobs import enqueue_lookup,run_one
        SourceControl.objects.create(pk=1,paused=True)
        enqueue_lookup('193787017')
        with patch('crm.jobs.Client') as client:
            self.assertFalse(run_one());self.assertFalse(client.called)
    def test_completed_day_is_not_reloaded_without_refresh(self):
        from .jobs import enqueue_day
        day=date(2026,1,1)
        job=enqueue_day(day);job.state='done';job.save()
        job.day.discovered=True;job.day.state='done';job.day.save()
        same=enqueue_day(day)
        self.assertEqual(same.state,'done')
        refreshed=enqueue_day(day,refresh=True)
        self.assertEqual(refreshed.state,'pending')
        refreshed.day.refresh_from_db();self.assertFalse(refreshed.day.discovered)
    def test_schedule_uses_minsk_and_is_idempotent(self):
        from datetime import datetime, timezone as utc
        from .models import CollectorSettings,CollectionJob
        from .jobs import schedule_due
        CollectorSettings.objects.create(pk=1,enabled=True,run_at='09:00',lookback=7)
        schedule_due(datetime(2026,11,5,5,59,tzinfo=utc.utc))
        self.assertEqual(CollectionJob.objects.count(),0)
        schedule_due(datetime(2026,11,5,6,0,tzinfo=utc.utc))
        self.assertEqual(CollectionJob.objects.count(),7)
        schedule_due(datetime(2026,11,5,7,0,tzinfo=utc.utc))
        self.assertEqual(CollectionJob.objects.count(),7)
        self.assertEqual(ImportDay.objects.latest('day').day,date(2026,11,4))
    def test_new_endpoints_are_private(self):
        from .jobs import enqueue_lookup,enqueue_day
        job=enqueue_lookup('193787017');day=enqueue_day(date(2026,1,1))
        self.client.logout()
        for path in [f'/jobs/{job.pk}/',f'/imports/{day.day_id}/export/','/companies/export/','/statistics/']:
            self.assertEqual(self.client.get(path).status_code,302)
        self.assertEqual(self.client.post('/imports/',{'action':'lookup','unp':'193787017'}).status_code,302)
    def test_filtered_csv_and_formula_protection(self):
        from django.utils import timezone
        Company.objects.create(unp='193787017',kind=1,name='=HYPERLINK("bad")',admitted_at=timezone.now())
        Company.objects.create(unp='193787018',kind=2,name='ИП',admitted_at=timezone.now())
        response=self.client.get('/companies/export/?kind=1')
        body=response_body(response).decode('utf-8-sig')
        self.assertIn("'=HYPERLINK",body)
        self.assertNotIn('193787018',body)
        self.assertContains(self.client.get('/?kind=2'),'ИП')
        self.assertContains(self.client.get('/statistics/'),'Статистика базы')
    @patch('crm.jobs.Client')
    def test_day_worker_discovers_and_finishes_candidate(self,client):
        from .jobs import enqueue_day,run_one
        fake=Fake()
        def get(method,*args):
            if method=='getBaseInfoByPeriod': return [{'ngrn':123,'dfrom':'2026-11-02T21:00:00Z','nsi00208':{'nkscrt':1},'nsi00211':{'nkvob':1}}]
            return fake.get(method,*args)
        client.return_value.get.side_effect=get
        job=enqueue_day(date(2026,11,3))
        run_one();job.refresh_from_db()
        self.assertEqual(job.state,'done')
        self.assertEqual(job.day.candidates.get().state,'done')
        self.assertIsNotNone(Company.objects.get(unp='123').admitted_at)

class LeaseTests(TestCase):
    def test_exclusion_and_expired_owner_cannot_release_successor(self):
        from datetime import timedelta
        from django.utils import timezone
        from .leases import acquire,release,renew
        first=acquire()
        self.assertTrue(first)
        self.assertIsNone(acquire())
        SourceControl.objects.filter(pk=1).update(locked_until=timezone.now()-timedelta(seconds=1))
        second=acquire()
        self.assertNotEqual(first,second)
        self.assertFalse(renew(first))
        release(first)
        self.assertEqual(SourceControl.objects.get(pk=1).lease_owner,second)
        release(second)
        self.assertIsNone(SourceControl.objects.get(pk=1).locked_until)

    @patch('crm.jobs.Client',return_value=Fake())
    def test_manual_lookup_precedes_old_bulk_queue(self,client):
        from .jobs import enqueue_day,enqueue_lookup,run_one
        day=enqueue_day(date(2026,1,1))
        lookup=enqueue_lookup('123')
        run_one()
        lookup.refresh_from_db();day.refresh_from_db()
        self.assertEqual(lookup.state,'done')
        self.assertEqual(day.state,'pending')

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class ActivitiesAndProgressTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('activity-owner',is_staff=True)
        self.client.force_login(self.user)

    def test_enrichment_stores_current_activities_and_address(self):
        class Source(Fake):
            def get(self,method,*args):
                if method=='getAddressByRegNum': return [{'ngrn':123,'vtels':'123','vregion':'Минская область','vnp':'Минск','vulitsa':'Ленина','vdom':'10','vkorp':'2','vpom':'3'}]
                return super().get(method,*args)
        row=enrich(Source(),'123')
        self.assertEqual(row.activities.get(current=True).code,'62010')
        self.assertEqual(row.activities.get().name,'Компьютерное программирование')
        self.assertIn('Минск, Ленина, д. 10, корп. 2, пом. 3',row.current_address)
        self.assertIsNotNone(row.activity_checked_at)
        self.assertIn('activities',row.source_data)
        self.assertContains(self.client.get(f'/companies/{row.pk}/'),'62010')
        self.assertContains(self.client.get('/'),'Компьютерное программирование')

    def test_activity_change_preserves_old_source_and_manual_data(self):
        row=enrich(Fake(),'123')
        row.notes='Ручная работа';row.status='Клиент';row.do_not_contact=True;row.save()
        class Changed(Fake):
            def get(self,method,*args):
                if method=='getVEDByRegNum': return [{'ngrn':123,'nsi00114':{'vkvdn':'63110','vnvdnp':'Обработка данных'}}]
                return super().get(method,*args)
        enrich(Changed(),'123');row.refresh_from_db()
        self.assertFalse(row.activities.get(code='62010').current)
        self.assertTrue(row.activities.get(code='63110').current)
        self.assertEqual((row.notes,row.status,row.do_not_contact),('Ручная работа','Клиент',True))
        self.assertNotContains(self.client.get('/?oked=62'),'Компания &lt;script&gt;')
        self.assertContains(self.client.get('/?oked=63'),'Компания &lt;script&gt;')

    def test_multiple_activities_include_exclude_and_case_insensitive_name(self):
        from .models import Activity
        from django.utils import timezone
        first=Company.objects.create(unp='1',kind=1,name='Первая',admitted_at=timezone.now())
        second=Company.objects.create(unp='2',kind=1,name='Вторая',admitted_at=timezone.now())
        for company,code,name in [(first,'62010','Программирование'),(first,'63110','Обработка данных'),(second,'47110','Торговля')]:
            Activity.objects.create(company=company,code=code,name=name,search_name=name.casefold())
        for query in ['oked=62','oked=62010','oked=62,63','activity=ПРОГРАММИРОВАНИЕ']:
            response=self.client.get('/?'+query)
            self.assertContains(response,'Первая');self.assertNotContains(response,'Вторая')
        response=self.client.get('/?oked=62&exclude_oked=63')
        self.assertNotContains(response,'Первая')
        self.assertContains(self.client.get('/?oked=47'),'Вторая')
        response=self.client.get('/companies/export/?oked=62')
        self.assertIn('62010',response_body(response).decode('utf-8-sig'))
        self.assertNotIn('Вторая',response_body(response).decode('utf-8-sig'))

    def test_invalid_filter_does_not_export_all_companies(self):
        enrich(Fake(),'123')
        self.assertContains(self.client.get('/?oked=foo'),'Введите до 30 кодов')
        self.assertEqual(self.client.get('/companies/export/?oked=foo').status_code,400)

    def test_old_cards_can_be_refreshed_without_resetting_metadata(self):
        from .models import CollectionJob
        from django.utils import timezone
        old=Company.objects.create(unp='193787017',kind=1,notes='Заметка',status='Клиент',source_data={'current_addresses':[{'vnp':'Минск','vdom':'7'}]})
        self.assertEqual(old.current_address,'Минск, д. 7')
        Company.objects.create(unp='193787018',kind=1,activity_checked_at=timezone.now())
        self.client.post('/imports/',{'action':'fill_activities'})
        self.assertEqual(CollectionJob.objects.count(),1)
        self.assertEqual(CollectionJob.objects.get().unp,old.unp)
        old.refresh_from_db();self.assertEqual((old.notes,old.status),('Заметка','Клиент'))

    def test_activity_failure_does_not_overwrite_prior_success(self):
        row=enrich(Fake(),'123')
        class Failed(Fake):
            def get(self,method,*args):
                if method=='getVEDByRegNum': raise SourceError('503')
                return super().get(method,*args)
        with self.assertRaises(SourceError): enrich(Failed(),'123')
        row.refresh_from_db()
        self.assertEqual(row.activities.filter(current=True).get().code,'62010')
        self.assertTrue(row.contacts.get().current)

    def test_partial_export_and_live_progress_do_not_stop_collection(self):
        from .jobs import enqueue_day,run_one
        from .models import CollectionJob
        job=enqueue_day(date(2026,11,3))
        class TwoCompanies(Fake):
            def get(self,method,*args):
                if method=='getBaseInfoByPeriod':
                    return [{'ngrn':unp,'dfrom':'2026-11-02T21:00:00Z','nsi00208':{'nkscrt':1},'nsi00211':{'nkvob':1}} for unp in [123,456]]
                records=super().get(method,*args)
                return [{**record,'ngrn':int(args[0])} for record in records]
        with patch('crm.jobs.Client',return_value=TwoCompanies()):
            run_one()
            job.refresh_from_db();self.assertEqual(job.state,'pending')
            progress=self.client.get('/imports/progress/').json()
            self.assertEqual((progress['candidates'],progress['processed'],progress['crm']),(2,1,1))
            self.assertEqual((progress['days'][0]['total'],progress['days'][0]['completed'],progress['days'][0]['ready']),(2,1,1))
            before=list(CollectionJob.objects.values('state','message','current_unp'))
            csv=response_body(self.client.get(f'/companies/export/?day={job.day_id}')).decode('utf-8-sig')
            self.assertIn("'123",csv);self.assertNotIn("'456",csv)
            self.assertIn('62010',csv);self.assertIn('Компьютерное программирование',csv)
            self.assertEqual(before,list(CollectionJob.objects.values('state','message','current_unp')))
            self.assertFalse(SourceControl.objects.get(pk=1).paused)
            run_one()
        job.refresh_from_db();self.assertEqual(job.state,'done')
        progress=self.client.get('/imports/progress/').json()
        self.assertEqual((progress['processed'],progress['crm']),(2,2))
        csv=response_body(self.client.get(f'/companies/export/?day={job.day_id}')).decode('utf-8-sig')
        self.assertIn("'456",csv)

    def test_progress_deduplicates_unps_and_protects_access(self):
        first=ImportDay.objects.create(day=date(2026,1,1))
        second=ImportDay.objects.create(day=date(2026,1,2))
        for day in [first,second]: Candidate.objects.create(day=day,unp='123',state='done')
        data=self.client.get('/imports/progress/').json()
        self.assertEqual((data['candidates'],data['processed']),(1,1))
        self.client.logout()
        self.assertEqual(self.client.get('/imports/progress/').status_code,302)
        outsider=get_user_model().objects.create_user('progress-outsider')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get('/imports/progress/').status_code,302)

    def test_active_unp_is_visible_while_enrichment_runs(self):
        from .jobs import enqueue_lookup,run_one
        from .importer import enrich as original
        enqueue_lookup('123')
        def inspect(client,unp):
            data=self.client.get('/imports/progress/').json()
            self.assertEqual(data['active'][0]['unp'],'123')
            return original(Fake(),unp)
        with patch('crm.jobs.enrich',side_effect=inspect): run_one()
        self.assertEqual(self.client.get('/imports/progress/').json()['active'],[])

from django.test import TransactionTestCase

class UpgradePreservationTests(TransactionTestCase):
    def test_upgrade_keeps_existing_company_and_pending_collection(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executor=MigrationExecutor(connection)
        old=[('crm','0003_sourcecontrol_lease_owner')]
        latest=executor.loader.graph.leaf_nodes('crm')
        executor.migrate(old)
        try:
            apps=executor.loader.project_state(old).apps
            CompanyBefore=apps.get_model('crm','Company')
            DayBefore=apps.get_model('crm','ImportDay')
            CandidateBefore=apps.get_model('crm','Candidate')
            JobBefore=apps.get_model('crm','CollectionJob')
            row=CompanyBefore.objects.create(unp='193787017',kind=1,status='Клиент',notes='Сохранить',do_not_contact=True,source_data={'current_addresses':[{'vnp':'Минск','vdom':'8'}]})
            day=DayBefore.objects.create(day=date(2026,11,3),discovered=True)
            CandidateBefore.objects.create(day=day,unp='193787017',state='done')
            job=JobBefore.objects.create(day=day,key='day:2026-11-03',kind='day',state='pending')
            manual=JobBefore.objects.create(key='unp:193787017',kind='unp',unp='193787017',state='pending')
            executor=MigrationExecutor(connection);executor.migrate(latest)
            from .models import CollectionJob
            upgraded=Company.objects.get(pk=row.pk)
            self.assertEqual((upgraded.notes,upgraded.status,upgraded.do_not_contact),('Сохранить','Клиент',True))
            self.assertEqual(upgraded.current_address,'Минск, д. 8')
            self.assertIsNone(upgraded.activity_checked_at)
            self.assertEqual(CollectionJob.objects.get(pk=job.pk).state,'pending')
            self.assertEqual(CollectionJob.objects.get(pk=manual.pk).priority,0)
            self.assertEqual(Candidate.objects.get(day_id=day.pk).state,'done')
        finally:
            MigrationExecutor(connection).migrate(latest)

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class CollectionStartFeedbackTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('start-owner',is_staff=True)
        self.client.force_login(self.user)

    @patch('django.utils.timezone.localdate',return_value=date(2026,11,5))
    def test_today_rejected_with_top_level_error_and_no_false_queue(self,today):
        from .models import CollectionJob
        response=self.client.post('/imports/',{'action':'period','start':'2026-11-03','end':'2026-11-05'})
        self.assertEqual(response.status_code,200)
        text=response_body(response).decode()
        self.assertIn('Сбор не запущен',text)
        self.assertIn('04.11.2026',text)
        self.assertLess(text.index('role="alert"'),text.index('id="collection-progress"'))
        self.assertFalse(CollectionJob.objects.exists())
        self.assertFalse(ImportDay.objects.exists())

    @patch('django.utils.timezone.localdate',return_value=date(2026,11,5))
    def test_completed_period_accepted_and_calendar_has_iso_values(self,today):
        from .models import CollectionJob
        initial=self.client.get('/imports/').content.decode()
        self.assertIn('value="2026-11-04"',initial)
        self.assertIn('max="2026-11-04"',initial)
        response=self.client.post('/imports/',{'action':'period','start':'2026-11-03','end':'2026-11-04'},follow=True)
        self.assertContains(response,'03.11.2026–04.11.2026 принят в очередь')
        self.assertEqual(ImportDay.objects.count(),2)
        self.assertEqual(CollectionJob.objects.filter(state='pending').count(),2)

    def test_idle_queue_active_and_error_statuses_are_distinct(self):
        from django.utils import timezone
        from .models import CollectorSettings
        from .jobs import enqueue_lookup
        CollectorSettings.objects.create(pk=1,heartbeat=timezone.now())
        data=self.client.get('/imports/progress/').json()
        self.assertIn('ожидает задания',data['status'])
        self.assertNotEqual(data['status'],'Сборщик работает')
        job=enqueue_lookup('193787017')
        self.assertIn('Задания в очереди',self.client.get('/imports/progress/').json()['status'])
        job.state='running';job.current_unp=job.unp;job.save()
        self.assertEqual(self.client.get('/imports/progress/').json()['status'],'Идёт сбор данных')
        job.state='error';job.save()
        self.assertIn('ошибках',self.client.get('/imports/progress/').json()['status'])

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class ResetCollectionTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('reset-owner',is_staff=True)
        self.client.force_login(self.user)

    def test_reset_cancels_queue_keeps_data_and_disables_schedule(self):
        from .models import CollectionJob,CollectorSettings,CollectionReset
        from .jobs import enqueue_day,enqueue_lookup
        from .collection_control import reset_collection
        row=enrich(Fake(),'123');row.notes='Мои заметки';row.status='Клиент';row.do_not_contact=True;row.save()
        day=enqueue_day(date(2026,11,3));lookup=enqueue_lookup('193787017')
        Candidate.objects.create(day=day.day,unp='123',state='done')
        Candidate.objects.create(day=day.day,unp='456',state='pending')
        CollectorSettings.objects.create(pk=1,enabled=True)
        count=reset_collection(self.user)
        self.assertEqual(count,2)
        self.assertEqual(CollectionJob.objects.filter(state='cancelled').count(),2)
        self.assertEqual(Candidate.objects.get(unp='123').state,'done')
        self.assertEqual(Candidate.objects.get(unp='456').state,'cancelled')
        row.refresh_from_db()
        self.assertEqual((row.notes,row.status,row.do_not_contact),('Мои заметки','Клиент',True))
        self.assertTrue(row.contacts.exists())
        self.assertFalse(CollectorSettings.objects.get(pk=1).enabled)
        self.assertTrue(SourceControl.objects.get(pk=1).paused)
        self.assertEqual(CollectionReset.objects.get().actor,self.user)

    @patch('crm.egr.time.sleep')
    @patch('crm.egr.urlopen')
    def test_reset_during_http_cannot_restore_job_or_commit_data(self,open_,sleep):
        import json
        from .jobs import enqueue_lookup,run_one
        from .collection_control import reset_collection
        job=enqueue_lookup('123')
        response=open_.return_value.__enter__.return_value
        response.status=200
        def arrive(*args):
            reset_collection(self.user)
            return json.dumps([{'ngrn':123,'nsi00211':{'nkvob':1}}]).encode()
        response.read.side_effect=arrive
        self.assertFalse(run_one())
        job.refresh_from_db();self.assertEqual(job.state,'cancelled')
        self.assertFalse(Company.objects.exists())
        self.assertIsNone(SourceControl.objects.get(pk=1).locked_until)
        self.assertFalse(run_one())

    def test_reset_after_company_commit_does_not_erase_it_or_resurrect_job(self):
        from .jobs import enqueue_day,run_one
        from .collection_control import reset_collection
        job=enqueue_day(date(2026,11,3));job.day.discovered=True;job.day.save()
        Candidate.objects.create(day=job.day,unp='123')
        def completed(client,unp):
            company=enrich(Fake(),unp)
            reset_collection(self.user)
            return company
        with patch('crm.jobs.enrich',side_effect=completed): self.assertFalse(run_one())
        job.refresh_from_db();self.assertEqual(job.state,'cancelled')
        self.assertEqual(Candidate.objects.get().state,'cancelled')
        self.assertEqual(Company.objects.count(),1)
        self.assertEqual(job.day.candidates.filter(state='done').count(),0)

    def test_explicit_new_period_requeues_only_cancelled_work(self):
        from .jobs import enqueue_day,run_one
        from .collection_control import reset_collection
        job=enqueue_day(date(2026,11,3));job.day.discovered=True;job.day.save()
        Candidate.objects.create(day=job.day,unp='456',state='done')
        Candidate.objects.create(day=job.day,unp='123',state='pending')
        reset_collection(self.user)
        self.assertFalse(run_one())
        enqueue_day(job.day.day)
        job.refresh_from_db();self.assertEqual(job.state,'pending')
        self.assertEqual(Candidate.objects.get(unp='456').state,'done')
        self.assertEqual(Candidate.objects.get(unp='123').state,'pending')
        self.client.post('/imports/',{'action':'resume'})
        with patch('crm.jobs.Client',return_value=Fake()): self.assertTrue(run_one())
        job.refresh_from_db();self.assertEqual(job.state,'done')

    def test_reset_does_not_release_in_flight_lease_for_second_worker(self):
        from .leases import acquire,is_current,release
        from .collection_control import reset_collection
        token=acquire()
        reset_collection(self.user)
        self.assertFalse(is_current(token))
        self.client.post('/imports/',{'action':'resume'})
        self.assertIsNone(acquire())
        release(token)
        self.assertTrue(acquire())

    def test_preview_requires_confirmation_and_csrf_and_is_private(self):
        from .jobs import enqueue_lookup
        job=enqueue_lookup('193787017')
        self.assertContains(self.client.get('/imports/reset/'),'Подтвердить сброс')
        job.refresh_from_db();self.assertEqual(job.state,'pending')
        self.client.post('/imports/reset/',{})
        job.refresh_from_db();self.assertEqual(job.state,'pending')
        from django.test import Client as TestClient
        csrf_client=TestClient(enforce_csrf_checks=True);csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post('/imports/reset/',{'confirm':'reset'}).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.post('/imports/reset/',{'confirm':'reset'}).status_code,302)
        outsider=get_user_model().objects.create_user('reset-outsider')
        self.client.force_login(outsider)
        self.assertEqual(self.client.post('/imports/reset/',{'confirm':'reset'}).status_code,302)
        job.refresh_from_db();self.assertEqual(job.state,'pending')

    def test_pause_preserves_pending_job_instead_of_error(self):
        from .jobs import enqueue_lookup,run_one
        job=enqueue_lookup('193787017')
        def pause(client,unp):
            SourceControl.objects.filter(pk=1).update(paused=True)
            from .egr import SourceCancelled
            raise SourceCancelled('Пауза')
        with patch('crm.jobs.enrich',side_effect=pause): self.assertFalse(run_one())
        job.refresh_from_db();self.assertEqual(job.state,'pending')

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class DesignSystemTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.user=get_user_model().objects.create_superuser('ui-owner','ui@example.test','test-password')
        self.client.force_login(self.user)

    def test_unknown_status_is_escaped_and_identity_is_stable(self):
        from django.template import Template,Context
        from .templatetags.ui import company_identity
        row=Company(name='ООО «Клён Дизайн»',country='BY',unp='900000123')
        identity=company_identity(row)
        self.assertEqual(identity['initials'],'КД')
        row.name='ООО «Новое имя»'
        self.assertEqual(identity['tone'],company_identity(row)['tone'])
        html=Template('{% load ui %}{% stage_badge status %}').render(Context({'status':'<script>alert(1)</script>'}))
        self.assertIn('status--custom',html)
        self.assertNotIn('<script>',html)
        self.assertIn('&lt;script&gt;',html)

    def test_filters_keep_scope_and_remove_pagination(self):
        from django.test import RequestFactory
        from .templatetags.ui import query_update
        request=RequestFactory().get('/?q=design&oked=62010&page=3')
        url=query_update({'request':request},status='Клиент')
        from urllib.parse import parse_qs
        query=parse_qs(url[1:])
        self.assertEqual(query['oked'],['62010'])
        self.assertEqual(query['q'],['design'])
        self.assertEqual(query['status'],['Клиент'])
        self.assertNotIn('page',query)

    def test_authenticated_screens_and_separate_login_layout(self):
        for path in ['/', '/tasks/', '/imports/', '/imports/?tab=progress', '/imports/?tab=schedule', '/backups/']:
            response=self.client.get(path)
            self.assertEqual(response.status_code,200,path)
            self.assertContains(response,'design-system.css')
            self.assertContains(response,'workspace-navigation')
        response=self.client.get('/login/')
        self.assertContains(response,'Вход в закрытую CRM')
        self.assertNotContains(response,'id="workspace-navigation"')

    def test_detail_forms_have_unique_field_ids(self):
        row=enrich(Fake(),'123')
        response=self.client.get(f'/companies/{row.pk}/')
        import re
        ids=re.findall(r'\bid="([^"]+)"',response.content.decode())
        self.assertEqual(len(ids),len(set(ids)))
        self.assertContains(response,'manual_')
        self.assertContains(response,'interaction_')

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class InlineStatusTests(TestCase):
    def setUp(self):
        from .models import AccessProfile
        self.user=get_user_model().objects.create_user('inline-manager',password='test-password')
        AccessProfile.objects.create(user=self.user,role='manager')
        self.client.force_login(self.user)
        self.row=enrich(Fake(),'123')
        self.row.notes='Сохранить заметку';self.row.do_not_contact=True;self.row.owner=self.user
        self.row.save()
        self.url=f'/companies/{self.row.pk}/status/'

    def post_status(self,status,previous='Новая'):
        return self.client.post(self.url,{'status':status,'previous_status':previous},HTTP_ACCEPT='application/json')

    def test_manager_can_change_only_status_without_opening_card(self):
        from .models import Audit
        self.assertEqual(self.post_status('Клиент').status_code,200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status,'Клиент')
        self.assertEqual(self.row.notes,'Сохранить заметку')
        self.assertTrue(self.row.do_not_contact)
        self.assertEqual(self.row.owner,self.user)
        self.assertIsNone(self.row.viewed_at)
        self.assertEqual(Audit.objects.filter(company=self.row).latest('pk').changes,{'status':{'before':'Новая','after':'Клиент'}})
        fragment=self.client.get('/companies/fragment/')
        self.assertContains(fragment,'data-inline-status')
        self.assertContains(fragment,'value="Переговоры"')

    def test_stale_change_and_unknown_stage_are_rejected(self):
        self.assertEqual(self.post_status('Клиент').status_code,200)
        self.assertEqual(self.post_status('Отказ').status_code,409)
        self.assertEqual(self.post_status('<script>',previous='Клиент').status_code,400)
        self.row.refresh_from_db();self.assertEqual(self.row.status,'Клиент')

    def test_login_post_and_csrf_required(self):
        from django.test import Client as TestClient
        self.assertEqual(self.client.get(self.url).status_code,405)
        protected=TestClient(enforce_csrf_checks=True);protected.force_login(self.user)
        self.assertEqual(protected.post(self.url,{'status':'Клиент','previous_status':'Новая'}).status_code,403)
        self.client.logout()
        self.assertEqual(self.post_status('Клиент').status_code,302)
        self.row.refresh_from_db();self.assertEqual(self.row.status,'Новая')

    def test_non_ajax_fallback_and_safe_redirect(self):
        response=self.client.post(self.url,{'status':'В работе','previous_status':'Новая','next':'https://outside.example/'})
        self.assertRedirects(response,'/',fetch_redirect_response=False)
        self.row.refresh_from_db();self.assertEqual(self.row.status,'В работе')

    def test_noop_does_not_add_audit_and_unadmitted_company_cannot_change(self):
        from .models import Audit
        before=Audit.objects.count()
        self.assertEqual(self.post_status('Новая').status_code,200)
        self.assertEqual(Audit.objects.count(),before)
        self.row.admitted_at=None;self.row.save(update_fields=['admitted_at'])
        self.assertEqual(self.post_status('Клиент').status_code,404)


@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class DashboardTests(TestCase):
    def test_period_cohort_distinct_companies_and_chronology(self):
        from .models import Interaction
        from .dashboard import dashboard_context
        from django.utils import timezone
        from datetime import timedelta
        row=enrich(Fake(),'123');other=Company.objects.create(unp='456',kind=1,admitted_at=timezone.now(),registered_on=timezone.localdate())
        at=timezone.now()-timedelta(days=2)
        # A reply before the first call must not improve the response rate.
        Interaction.objects.create(company=row,kind='reply',result='answered',occurred_at=at-timedelta(hours=1))
        Interaction.objects.create(company=row,kind='call',result='attempt',occurred_at=at)
        Interaction.objects.create(company=row,kind='message',result='attempt',occurred_at=at+timedelta(minutes=1))
        Interaction.objects.create(company=row,kind='note',result='client',occurred_at=at+timedelta(hours=1))
        Interaction.objects.create(company=other,kind='call',result='client',occurred_at=at)
        Interaction.objects.create(company=other,kind='reply',result='answered',occurred_at=at+timedelta(hours=1))
        result=dashboard_context({})
        self.assertEqual((result['outgoing_events'],result['contacted'],result['answered'],result['clients']),(3,2,1,1))
        self.assertEqual(result['response_conversion'],50)
        self.assertEqual(sum(p['outgoing'] for p in result['trend']),3)
        self.assertEqual(result['period_days'],30)
        # A result outside the selected date range cannot enter the cohort.
        day=(timezone.localdate()-timedelta(days=1)).isoformat()
        result=dashboard_context({'start':day,'end':day})
        self.assertEqual(result['contacted'],0)
        self.assertIsNone(result['response_conversion'])
        self.assertEqual(result['total'],2)

    def test_quality_counts_companies_once_and_unknown_dates_visible(self):
        from django.utils import timezone
        from .models import Contact
        from .dashboard import dashboard_context
        row=enrich(Fake(phone='111'),'123')
        Contact.objects.create(company=row,kind='phone',raw='222',current=False)
        other=Company.objects.create(unp='456',kind=1,admitted_at=timezone.now(),registered_on=None)
        Contact.objects.create(company=other,kind='phone',raw='333',current=False)
        Contact.objects.create(company=other,kind='phone',raw='444',current=False)
        other.registered_on=None;other.save(update_fields=['registered_on'])
        data=dashboard_context({})
        quality={item['label']:item['total'] for item in data['quality_bars']}
        self.assertEqual(quality['Есть телефон'],2)
        self.assertEqual(quality['Телефон текущий по ЕГР'],1)
        self.assertEqual(quality['Только исторические телефоны'],1)
        self.assertEqual(next(item['total'] for item in data['age_bars'] if item['label']=='Дата не получена'),1)
        self.assertEqual(sum(item['total'] for item in data['status_bars']),2)
        self.assertEqual(sum(item['total'] for item in data['region_bars']),2)

    def test_invalid_range_and_empty_data_do_not_report_fake_conversions(self):
        from .dashboard import dashboard_context
        from django.utils import timezone
        from datetime import timedelta
        empty=dashboard_context({})
        self.assertIsNone(empty['response_conversion'])
        self.assertIsNone(empty['client_conversion'])
        self.assertEqual(empty['trend_peak'],0)
        for params in [{'start':'bad','end':'2026-01-01'}, {'start':'2026-02-02','end':'2026-01-01'}, {'start':'2020-01-01','end':'2026-01-01'}, {'start':timezone.localdate().isoformat(),'end':(timezone.localdate()+timedelta(days=1)).isoformat()}]:
            with self.subTest(params=params):
                data=dashboard_context(params)
                self.assertFalse(data['period_valid'])
                self.assertEqual(data['contacted'],0)
        data=dashboard_context({'days':'90'})
        self.assertEqual(data['period_days'],90)
        self.assertTrue(data['trend_monthly'])

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class ContactShortcutTests(TestCase):
    def test_messenger_numbers_are_single_valid_international_numbers(self):
        from .normalization import messenger_phone
        for raw,expected in [('+375 (29) 889-37-77','+375298893777'),('+7 929 351 82 85','+79293518285'),('8 (029) 889-37-77','+375298893777'),('123',''),('javascript:alert(1)',''),('+375298893777; +375299876543',''),('','')]:
            with self.subTest(raw=raw):self.assertEqual(messenger_phone(raw),expected)

    def test_rendered_links_are_encoded_and_shared_in_table_and_card(self):
        from django.utils import timezone
        from urllib.parse import urlparse,parse_qs
        from html.parser import HTMLParser
        class Links(HTMLParser):
            def __init__(self):super().__init__();self.links=[]
            def handle_starttag(self,tag,attrs):
                if tag=='a':self.links.append(dict(attrs))
        user=get_user_model().objects.create_superuser('shortcut-owner','owner@example.test','test-password')
        self.client.force_login(user)
        row=Company.objects.create(unp='900000123',kind=1,name='ООО «Клён & Ко» <script>',admitted_at=timezone.now())
        contact=Contact.objects.create(company=row,kind='phone',raw='+375 (29) 889-37-77',current=True)
        expected_key=f'phone:{row.pk}:{contact.pk}:telegram:+375298893777'
        for path in ['/',f'/companies/{row.pk}/','/companies/fragment/']:
            response=self.client.get(path)
            self.assertEqual(response.status_code,200)
            parser=Links();parser.feed(response.content.decode())
            telegram=next(link for link in parser.links if link.get('href','').startswith('https://t.me/'))
            self.assertEqual(telegram['href'],'https://t.me/+375298893777')
            self.assertEqual(telegram['data-external-action'],expected_key)
            self.assertEqual(telegram['target'],'_blank')
            self.assertIn('noopener',telegram['rel'])
            viber=next(link for link in parser.links if link.get('href','').startswith('viber://'))
            self.assertEqual(parse_qs(urlparse(viber['href']).query)['number'],['+375298893777'])
            google=next(link for link in parser.links if link.get('href','').startswith('https://www.google.com/search?'))
            self.assertEqual(parse_qs(urlparse(google['href']).query)['q'],[row.name])
            self.assertEqual(google['target'],'_blank')
            self.assertIn('noreferrer',google['rel'])
            self.assertNotIn(row.name,response.content.decode())

    def test_invalid_number_has_no_messenger_target_and_unnamed_company_uses_unp(self):
        from .templatetags.ui import company_search
        from django.template import Template,Context
        row=Company(unp='900000123',kind=1,name='')
        contact=Contact(raw='123',kind='phone')
        html=Template('{% load ui %}{% phone_actions contact row %}').render(Context({'row':row,'contact':contact}))
        self.assertNotIn('viber://',html);self.assertNotIn('https://t.me/',html)
        self.assertEqual(company_search(row)['search_url'],'https://www.google.com/search?q=900000123')

    def test_settings_forms_render_with_separate_status_rows_and_fields(self):
        user=get_user_model().objects.create_superuser('settings-layout-owner','owner@example.test','test-password')
        self.client.force_login(user)
        response=self.client.get('/settings/')
        self.assertContains(response,'settings-status-row')
        self.assertContains(response,'Новый статус')
        self.assertContains(response,'name="position"')
        self.assertContains(response,'name="active"')
