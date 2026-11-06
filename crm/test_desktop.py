import io
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch
from django.test import TestCase, override_settings, Client
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth import get_user_model
from desktop_app.storage import prepare_storage, backup_database, data_identity
from desktop_app.runtime import InstanceLock, AlreadyRunning

class DesktopStorageTests(TestCase):
    def test_legacy_copy_does_not_overwrite_saved_database_or_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'program';root.mkdir();data=Path(temporary)/'data'
            with sqlite3.connect(root/'dev.sqlite3') as db:db.execute('create table old (name text)');db.execute("insert into old values ('client')")
            (root/'.local-key').write_text('preserved-key')
            first=prepare_storage(root,data)
            self.assertTrue(first['imported']);self.assertEqual(first['secret'],'preserved-key')
            with sqlite3.connect(data/'dev.sqlite3') as db:db.execute("insert into old values ('new')")
            (root/'.local-key').write_text('different-key')
            second=prepare_storage(root,data)
            self.assertFalse(second['imported']);self.assertEqual(second['secret'],'preserved-key')
            copy=backup_database(data)
            with sqlite3.connect(copy) as db:self.assertEqual(db.execute('select count(*) from old').fetchone()[0],2)
            with sqlite3.connect(root/'dev.sqlite3') as db:self.assertEqual(db.execute('select count(*) from old').fetchone()[0],1)
    def test_single_instance_lock_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            first=InstanceLock(Path(temporary)/'lock');second=InstanceLock(Path(temporary)/'lock')
            first.acquire()
            try:
                with self.assertRaises(AlreadyRunning):second.acquire()
            finally:first.close()
            second.acquire();second.close()

@override_settings(LOCAL_MODE=True)
class DesktopAccountTests(TestCase):
    def command(self,action,payload):
        output=io.StringIO()
        with patch('sys.stdin',io.StringIO(json.dumps(payload))):call_command('desktop_account',action,stdout=output)
        return json.loads(output.getvalue())
    def test_create_reset_owner_and_reject_weak_password(self):
        self.assertFalse(self.command('create',{'username':'owner','password':'123'})['ok'])
        password='Local-test-strong-93'
        self.assertTrue(self.command('create',{'username':'owner','password':password})['ok'])
        self.assertFalse(self.command('create',{'username':'other','password':password})['ok'])
        self.assertTrue(self.command('reset',{'username':'owner','password':password+'new'})['ok'])
        user=get_user_model().objects.get(username='owner');self.assertTrue(user.check_password(password+'new'))
        self.assertEqual(user.crm_profile.role,'owner')
    @override_settings(LOCAL_MODE=False)
    def test_public_mode_disables_local_controls(self):
        with self.assertRaises(CommandError):self.command('reset',{})
        self.assertEqual(self.client.get('/local/status/',secure=True).status_code,404)
    @override_settings(SECURE_SSL_REDIRECT=False,ALLOWED_HOSTS=['testserver'])
    def test_health_has_no_company_data_and_csrf_still_required(self):
        result=self.client.get('/local/status/');self.assertEqual(result.status_code,200)
        self.assertEqual(set(result.json()),{'application','data_id','version'})
        self.assertEqual(self.client.get('/local/status/',REMOTE_ADDR='8.8.8.8').status_code,404)
        client=Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/login/',{'username':'owner','password':'unknown'}).status_code,403)
    def test_stop_interrupts_source_request_before_network(self):
        from crm.egr import Client as EGRClient, SourceCancelled
        with tempfile.TemporaryDirectory() as directory:
            stop=Path(directory)/'stop';stop.touch()
            with patch.dict('os.environ',{'CRM_LOCAL_STOP_FILE':str(stop)}):
                with self.assertRaises(SourceCancelled):EGRClient().ensure_active()

    def test_shutdown_returns_active_job_to_queue_without_error(self):
        from crm.models import SourceControl
        from crm.jobs import enqueue_lookup, run_one
        SourceControl.objects.update_or_create(pk=1,defaults={'paused':False})
        job=enqueue_lookup('193787017')
        with tempfile.TemporaryDirectory() as directory:
            stop=Path(directory)/'stop'
            def interrupted(client,*args,**kwargs):
                stop.touch();client.ensure_active()
            with patch.dict('os.environ',{'CRM_LOCAL_STOP_FILE':str(stop)}),patch('crm.jobs.enrich',side_effect=interrupted):
                self.assertFalse(run_one())
        job.refresh_from_db();self.assertEqual(job.state,'pending');self.assertEqual(job.attempts,0)
        self.assertEqual(SourceControl.objects.get(pk=1).lease_owner,'')

class DesktopProcessTests(TestCase):
    def test_cancel_silent_setup_process(self):
        import sys, threading, time
        from desktop_app.runtime import run_logged, Cancelled
        cancel=threading.Event();timer=threading.Timer(.2,cancel.set);timer.start()
        started=time.monotonic()
        try:
            with self.assertRaises(Cancelled):
                run_logged([sys.executable,'-c','import time; time.sleep(30)'],Path.cwd(),None,lambda *args:None,cancel)
            self.assertLess(time.monotonic()-started,5)
        finally:timer.cancel()
    def test_public_https_defaults_and_desktop_settings_are_separate(self):
        import os, subprocess, sys
        base=os.environ.copy();base.update(DJANGO_SECRET_KEY='settings-test',DEBUG='0',POSTGRES_HOST='')
        script='import config.settings as s; print(s.LOCAL_MODE,s.SECURE_SSL_REDIRECT,s.SESSION_COOKIE_SECURE,s.CSRF_COOKIE_SECURE)'
        for local,expected in [('0','False True True True'),('1','True False False False')]:
            base['CRM_LOCAL_MODE']=local
            result=subprocess.run([sys.executable,'-c',script],env=base,text=True,capture_output=True,check=True)
            self.assertEqual(result.stdout.strip(),expected)
