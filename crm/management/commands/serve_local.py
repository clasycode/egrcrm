from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    def handle(self,*args,**options):
        if not settings.LOCAL_MODE:raise CommandError('Desktop mode is required')
        from waitress import serve
        from config.wsgi import application
        serve(application,host='127.0.0.1',port=8000,threads=2)
