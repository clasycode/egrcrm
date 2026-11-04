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

class Fake:
    def __init__(self,kind=1,phone='123',email='',history=None):
        self.kind,self.phone,self.email,self.history = kind,phone,email,history
    def get(self,method,*args):
        if method=='getBaseInfoByRegNum': return [{'ngrn':123,'dfrom':'2026-11-02T21:00:00Z','nsi00211':{'nkvob':self.kind}}]
        if 'Names' in method or 'FIO' in method: return [{'ngrn':123,'vnaim':'Компания <script>'}]
        if method=='getAllAddressByRegNum': return self.history or []
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
        with patch('crm.management.commands.collect_egr.discover',side_effect=SourceError('offline')):
            from django.core.management import CommandError
            with self.assertRaises(CommandError): call_command('collect_egr',initialize=True,limit=1)
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
        self.assertIn("'193787017",response.content.decode('utf-8-sig'))
        self.assertContains(self.client.get('/imports/'),'Скачать CSV')
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
        body=response.content.decode('utf-8-sig')
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
