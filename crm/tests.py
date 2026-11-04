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
