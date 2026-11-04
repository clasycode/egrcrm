from django.contrib import admin
from .models import Company,Contact,ImportDay,Candidate,Receipt,SourceControl,Audit,Activity,CollectionReset
for model in [Company,Contact,ImportDay,Candidate,Receipt,SourceControl,Audit,Activity,CollectionReset]: admin.site.register(model)
