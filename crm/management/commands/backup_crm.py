from django.core.management.base import BaseCommand
class Command(BaseCommand):
    def handle(self,*args,**options):
        from crm.backups import create_archive
        from django.conf import settings
        record=create_archive('command')
        self.stdout.write(str(settings.BACKUP_DIR/record.filename))
