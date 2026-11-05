from django.contrib import admin
from .models import Company,Contact,ImportDay,Candidate,Receipt,SourceControl,Audit,Activity,CollectionReset
for model in [Company,ImportDay,Candidate,Receipt,SourceControl,Audit,Activity,CollectionReset]: admin.site.register(model)


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    exclude=('telegram_state','viber_state')
