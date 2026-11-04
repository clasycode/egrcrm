from django.contrib import admin
from .models import Company,Contact,ImportDay,Candidate,Receipt,SourceControl,Audit,Activity
for model in [Company,Contact,ImportDay,Candidate,Receipt,SourceControl,Audit,Activity]: admin.site.register(model)
