import hashlib,json
from datetime import timedelta
from django.db import migrations
from django.utils import timezone

def populate(apps,schema_editor):
    Company=apps.get_model('crm','Company')
    Snapshot=apps.get_model('crm','SourceSnapshot')
    now=timezone.now()
    Company.objects.filter(admitted_at__isnull=True,first_no_contacts__isnull=True).update(first_no_contacts=now,next_contact_check=now+timedelta(days=7))
    for company in Company.objects.exclude(source_data={}).iterator():
        if not Snapshot.objects.filter(company_id=company.pk).exists():
            payload=company.source_data
            fingerprint=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
            Snapshot.objects.create(company_id=company.pk,fingerprint=fingerprint,data=payload,observed_at=now)
class Migration(migrations.Migration):
    dependencies=[('crm','0013_collectionrun_progress_at_interaction_contact')]
    operations=[migrations.RunPython(populate,migrations.RunPython.noop)]
