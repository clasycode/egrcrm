from django.contrib import admin
from .models import Company,Contact,ImportDay,Candidate,Receipt,SourceControl,Audit
for model in [Company,Contact,ImportDay,Candidate,Receipt,SourceControl,Audit]: admin.site.register(model)
