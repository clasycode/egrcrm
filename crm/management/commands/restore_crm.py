from django.core.management.base import BaseCommand,CommandError
class Command(BaseCommand):
    def add_arguments(self,p):
        p.add_argument('file');p.add_argument('--owner',required=True);p.add_argument('--replace',action='store_true')
    def handle(self,*args,**o):
        from django.contrib.auth import get_user_model
        from crm.backups import restore_archive
        from crm.access import is_owner
        user=get_user_model().objects.get(username=o['owner'])
        if not is_owner(user): raise CommandError('Owner required')
        try:
            with open(o['file'],'rb') as stream: restore_archive(stream,user,replace=o['replace'])
        except ValueError as exc: raise CommandError(str(exc)) from exc
        self.stdout.write('Restored; collector paused, schedule disabled.')
