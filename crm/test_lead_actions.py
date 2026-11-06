import io,json,tempfile,zipfile,hashlib
from pathlib import Path
from django.test import TestCase,Client,override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import Company,Contact,CompanyReview,Interaction,WorkDay,AccessProfile,CRMStatus
from .reviewing import summary

@override_settings(SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False)
class QuickLeadTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('lead-manager')
        AccessProfile.objects.create(user=self.user,role='manager');self.client.force_login(self.user)
        self.row=Company.objects.create(unp='900123456',kind=1,name='ООО «Лид»',admitted_at=timezone.now(),notes='Старая заметка')
        Contact.objects.create(company=self.row,kind='phone',raw='+375298893777')
    def act(self,action,**extra):
        self.row.refresh_from_db()
        payload={'action':action,'previous_status':self.row.status,'previous_reply':self.row.reply_state,**extra}
        return self.client.post(f'/companies/{self.row.pk}/lead-action/',payload,HTTP_ACCEPT='application/json')
    def test_full_workflow_without_card_and_unique_daily_progress(self):
        response=self.act('start');self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['work']['done'],1);self.assertTrue(response.json()['counted'])
        self.row.refresh_from_db();self.assertEqual((self.row.status,self.row.reply_state),('В работе','waiting'))
        self.assertIsNone(self.row.viewed_at);self.assertEqual(self.row.notes,'Старая заметка')
        self.assertEqual(Interaction.objects.filter(kind='message').count(),1)
        self.assertEqual(self.act('no_reply').status_code,200)
        response=self.act('answered');self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['replies_today'],1)
        self.assertEqual(self.act('negotiation').status_code,200)
        self.assertEqual(self.act('client').status_code,200)
        self.row.refresh_from_db();self.assertEqual(self.row.status,'Клиент')
        self.assertEqual(summary(self.user)['done'],1);self.assertEqual(CompanyReview.objects.count(),1)
    def test_one_click_disqualification_no_message_or_required_note(self):
        self.assertEqual(self.act('not_fit').status_code,200)
        self.row.refresh_from_db();self.assertEqual(self.row.status,'Не подходит')
        self.assertEqual(CompanyReview.objects.get().result,'not_fit');self.assertFalse(Interaction.objects.exists())
    def test_existing_in_work_reply_does_not_count_as_new_lead(self):
        self.row.status='В работе';self.row.save(update_fields=['status'])
        self.assertEqual(self.act('answered').status_code,200)
        self.assertEqual(summary(self.user)['done'],0)
        self.assertEqual(self.act('refusal').status_code,200)
        self.assertEqual(summary(self.user)['done'],0)
    def test_stale_repeated_click_and_response_change_conflict(self):
        self.assertEqual(self.act('start').status_code,200)
        self.assertEqual(self.act('start',previous_status='Новая').status_code,409)
        self.assertEqual(self.act('answered').status_code,200)
        self.assertEqual(self.act('no_reply',previous_reply='waiting').status_code,409)
        self.assertEqual(Interaction.objects.filter(kind='message').count(),1)
    def test_transition_in_list_card_and_bulk_all_count(self):
        for index,route in enumerate(('status','card','bulk')):
            row=Company.objects.create(unp=f'90000000{index}',kind=1,admitted_at=timezone.now())
            if route=='status':response=self.client.post(f'/companies/{row.pk}/status/',{'status':'В работе','previous_status':'Новая'},HTTP_ACCEPT='application/json')
            elif route=='card':response=self.client.post(f'/companies/{row.pk}/',{'status':'В работе','notes':'Сохранена'})
            else:response=self.client.post('/bulk/',{'selected':[row.pk],'action':'status','status':'В работе'})
            self.assertIn(response.status_code,(200,302))
        self.assertEqual(summary(self.user)['done'],3)
        self.assertEqual(Interaction.objects.filter(kind='message').count(),3)
        self.assertEqual(WorkDay.objects.get().target,15)
    def test_disabled_stage_contact_ban_invalid_state_and_private_routes(self):
        self.assertEqual(self.act('answered').status_code,400)
        self.row.do_not_contact=True;self.row.save(update_fields=['do_not_contact'])
        self.assertEqual(self.act('start').status_code,400)
        self.assertEqual(self.act('not_fit').status_code,200)
        row=Company.objects.create(unp='900444444',kind=1,admitted_at=timezone.now())
        CRMStatus.objects.filter(name='В работе').update(active=False)
        self.assertEqual(self.client.post(f'/companies/{row.pk}/lead-action/',{'action':'start','previous_status':'Новая','previous_reply':''},HTTP_ACCEPT='application/json').status_code,400)
        self.assertEqual(self.client.get(f'/companies/{row.pk}/lead-action/').status_code,405)
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.user)
        self.assertEqual(csrf.post(f'/companies/{row.pk}/lead-action/',{}).status_code,403)
        self.client.logout();self.assertEqual(self.act('start').status_code,302)
    def test_tabs_show_current_state_not_card_views(self):
        self.row.viewed_at=timezone.now();self.row.save(update_fields=['viewed_at'])
        self.assertContains(self.client.get('/work/'),'data-company-id="'+str(self.row.pk)+'"')
        self.act('start');self.assertNotContains(self.client.get('/work/'),'data-company-id="'+str(self.row.pk)+'"')
        self.assertContains(self.client.get('/work/?tab=working'),'data-company-id="'+str(self.row.pk)+'"')
        self.act('answered');self.assertContains(self.client.get('/work/?tab=answered'),'data-company-id="'+str(self.row.pk)+'"')
        self.assertNotContains(self.client.get('/work/?tab=no_reply'),'data-company-id="'+str(self.row.pk)+'"')
    def test_old_review_is_preserved_not_recounted(self):
        review=CompanyReview.objects.create(company=self.row,user=self.user,result='ready',note='Прежний итог')
        old=review.completed_at
        response=self.act('start');self.assertFalse(response.json()['counted'])
        review.refresh_from_db();self.assertEqual((review.result,review.note,review.completed_at),('ready','Прежний итог',old))
    def test_old_zip_without_new_field_and_new_reply_round_trip(self):
        from .backups import create_archive,restore_archive
        with tempfile.TemporaryDirectory() as directory,override_settings(BACKUP_DIR=Path(directory)):
            self.act('start');self.act('answered')
            record=create_archive();file=Path(directory)/record.filename
            raw=file.read_bytes()
            restore_archive(io.BytesIO(raw),self.user,replace=True)
            self.row.refresh_from_db();self.assertEqual(self.row.reply_state,'answered')
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                manifest=json.loads(archive.read('manifest.json'));records=json.loads(archive.read('data.json'))
            for item in records:
                if item['model']=='crm.company':item['fields'].pop('reply_state',None)
            payload=json.dumps(records,ensure_ascii=False).encode()
            manifest['sha256']=hashlib.sha256(payload).hexdigest()
            manifest['migrations']=[name for name in manifest['migrations'] if not name.startswith('0017')]
            old=io.BytesIO()
            with zipfile.ZipFile(old,'w') as archive:archive.writestr('manifest.json',json.dumps(manifest));archive.writestr('data.json',payload)
            old.seek(0);restore_archive(old,self.user,replace=True)
            self.row.refresh_from_db();self.assertEqual(self.row.reply_state,'');self.assertEqual(self.row.notes,'Старая заметка')
    def test_new_filter_tracks_status_not_opening_the_card(self):
        self.client.get(f'/companies/{self.row.pk}/')
        self.assertContains(self.client.get('/?new=1'),'data-company-id="'+str(self.row.pk)+'"')
        self.act('not_fit')
        self.assertNotContains(self.client.get('/?new=1'),'data-company-id="'+str(self.row.pk)+'"')
    def test_saved_reply_in_card_agrees_with_fast_processing(self):
        self.act('start')
        self.client.post(f'/companies/{self.row.pk}/',{'action':'interaction','kind':'reply','result':'answered','note':'Получен ответ','occurred_at':timezone.localtime().strftime('%Y-%m-%dT%H:%M')})
        self.row.refresh_from_db();self.assertEqual(self.row.reply_state,'answered')
        self.assertEqual(Interaction.objects.filter(company=self.row,kind='reply').count(),1)
        self.assertEqual(summary(self.user)['done'],1)
