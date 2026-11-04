from django.db import migrations

def populate(apps,schema_editor):
    apps.get_model('crm','CollectionJob').objects.filter(kind='unp').update(priority=0)
    Status=apps.get_model('crm','CRMStatus')
    for i,name in enumerate(['Новая','В работе','Переговоры','Клиент','Вернуться позже','Не подходит','Отказ']):
        Status.objects.get_or_create(name=name,defaults={'position':i})
    Company=apps.get_model('crm','Company')
    Contact=apps.get_model('crm','Contact')
    # Frozen migration logic: future refactoring of application helpers must not break old databases.
    import re
    import phonenumbers
    def normalized_contact(kind,raw):
        if kind=='phone':
            try:
                phone=phonenumbers.parse(raw,'BY')
                if phonenumbers.is_valid_number(phone): return phonenumbers.format_number(phone,phonenumbers.PhoneNumberFormat.E164)
            except phonenumbers.NumberParseException: pass
            return re.sub(r'\D','',raw)
        return raw.strip().casefold()
    for row in Company.objects.iterator():
        addresses=row.source_data.get('current_addresses') or []
        address=addresses[0] if addresses else {}
        row.search_name=row.name.casefold()
        row.region=address.get('vregion') or '';row.city=address.get('vnp') or '';row.district=address.get('vdistrict') or ''
        row.egr_state=str(((row.source_data.get('base') or {}).get('nsi00219') or {}).get('vnsostk') or '')
        row.save(update_fields=['search_name','region','city','district','egr_state'])
    for contact in Contact.objects.iterator():
        contact.normalized=normalized_contact(contact.kind,contact.raw)
        contact.original_line=contact.raw
        contact.save(update_fields=['normalized','original_line'])
class Migration(migrations.Migration):
    dependencies=[('crm','0008_alter_company_status')]
    operations=[migrations.RunPython(populate,migrations.RunPython.noop)]
