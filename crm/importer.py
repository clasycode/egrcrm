from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from .models import Company, Contact, Candidate, Activity, SourceSnapshot
from .egr import SourceError

def local_date(value):
    if not value: return None
    if not isinstance(value,str): raise SourceError('Неожиданный формат даты ЕГР')
    parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    return parsed.astimezone(ZoneInfo('Europe/Minsk')).date() if parsed.tzinfo else parsed.date()

def reference(record,key):
    value=record.get(key)
    if value is None: return {}
    if not isinstance(value,dict): raise SourceError('Неожиданный формат '+key)
    return value

def ensure_active(client):
    # A real client locks the source row until this short persistence transaction commits.
    check=getattr(client,'ensure_active',None)
    if check: check(lock=True)

def discover(client,day):
    date = day.day.strftime('%d.%m.%Y')
    records = client.get('getBaseInfoByPeriod',date,date)
    ids = []
    for record in records:
        if reference(record,'nsi00208').get('nkscrt') != 1: continue
        if local_date(record.get('dfrom')) != day.day: continue
        if reference(record,'nsi00211').get('nkvob') not in (1,2): continue
        unp = str(record.get('ngrn',''))
        if not unp.isdigit(): raise SourceError('Недопустимый УНП в выборке')
        ids.append(unp)
    with transaction.atomic():
        ensure_active(client)
        for unp in ids: Candidate.objects.get_or_create(day=day,unp=unp)
        day.discovered = True
        day.save(update_fields=['discovered','updated_at'])

def enrich(client,unp):
    base = client.get('getBaseInfoByRegNum',unp)
    if not base: raise SourceError('ЕГР вернул пустой ответ: субъект по этому УНП не найден. Это не ошибка сети.')
    if len(base)!=1 or str(base[0].get('ngrn'))!=unp: raise SourceError('Базовая запись не соответствует УНП')
    base = base[0]
    kind = reference(base,'nsi00211').get('nkvob')
    if kind not in (1,2): raise SourceError('Неизвестный тип субъекта')
    names = client.get('getJurNamesByRegNum' if kind==1 else 'getIPFIOByRegNum',unp)
    current = client.get('getAddressByRegNum',unp)
    history = client.get('getAllAddressByRegNum',unp)
    activities = client.get('getVEDByRegNum',unp)
    for record in names+current+history+activities:
        if str(record.get('ngrn'))!=unp: raise SourceError('Чужой УНП в ответе обогащения')
    for record in names:
        for key in ('vnaim','vfn','vfio'):
            if record.get(key) is not None and not isinstance(record[key],str): raise SourceError('Неожиданный формат названия компании')
    name = next((r.get('vnaim') or r.get('vfn') or r.get('vfio') for r in names if r.get('vnaim') or r.get('vfn') or r.get('vfio')),'')
    activity_values = []
    for record in activities:
        definition = record.get('nsi00114')
        if not isinstance(definition,dict): raise SourceError('В ответе ОКЭД отсутствует справочник nsi00114')
        raw_code = definition.get('vkvdn')
        if not isinstance(raw_code,(str,int)) or isinstance(raw_code,bool) or not str(raw_code).strip():
            raise SourceError('В ответе ОКЭД отсутствует код vkvdn')
        activity_name = definition.get('vnvdnp') or ''
        if not isinstance(activity_name,str): raise SourceError('Неожиданный формат названия деятельности')
        activity_values.append((str(raw_code).strip(),activity_name,record))
    with transaction.atomic():
        ensure_active(client)
        from .business import status_choices
        company,created = Company.objects.get_or_create(country='BY',unp=unp,defaults={'kind':kind,'status':status_choices()[0][0]})
        company=Company.objects.select_for_update().get(pk=company.pk)
        old_data=company.source_data
        company.kind,company.name = kind,name
        company.registered_on = local_date(base.get('dfrom'))
        company.source_data = {'base':base,'names':names,'current_addresses':current,'address_history':history,'activities':activities}
        import hashlib,json
        def fingerprint(value): return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        if old_data and not company.snapshots.exists():
            SourceSnapshot.objects.create(company=company,fingerprint=fingerprint(old_data),data=old_data)
        if not old_data or fingerprint(old_data)!=fingerprint(company.source_data):
            SourceSnapshot.objects.create(company=company,fingerprint=fingerprint(company.source_data),data=company.source_data)
            if not created: company.source_changed_at=timezone.now()
        address=current[0] if current else {}
        company.region=str(address.get('vregion') or '')
        company.city=str(address.get('vnp') or '')
        company.district=str(address.get('vdistrict') or '')
        company.egr_state=str(reference(base,'nsi00219').get('vnsostk') or reference(base,'nsi00219').get('nksost') or '')
        company.activities.update(current=False)
        for code,activity_name,record in activity_values:
            Activity.objects.update_or_create(company=company,code=code,name=activity_name,defaults={'current':True,'search_name':activity_name.casefold(),'source_data':record})
        company.activity_checked_at = timezone.now()
        company.contacts.filter(source='ЕГР').update(current=False)
        # History first; current endpoint determines currentness, never cact.
        for records,is_current in [(history,False),(current,True)]:
            for record in records:
                for field,contact_kind in [('vtels','phone'),('vemail','email'),('vsite','site')]:
                    raw = record.get(field)
                    if raw is None or not str(raw).strip(): continue
                    if not isinstance(raw,str): raise SourceError('Неожиданный формат контакта')
                    from .normalization import normalized_contact,contact_parts
                    for part in contact_parts(contact_kind,raw):
                        contact,new_contact = Contact.objects.get_or_create(company=company,kind=contact_kind,raw=part,source='ЕГР',defaults={'current':is_current,'source_data':record,'original_line':raw,'normalized':normalized_contact(contact_kind,part)})
                        if not new_contact:
                            contact.current=is_current or contact.current
                            contact.source_data=record
                            contact.normalized=normalized_contact(contact_kind,part)
                            contact.original_line=raw
                            contact.save(update_fields=['current','source_data','last_seen','normalized','original_line'])
        eligible = company.contacts.filter(kind='phone').exists() or (kind==1 and company.contacts.filter(kind='email').exists())
        if eligible and company.admitted_at is None: company.admitted_at = timezone.now()
        if eligible:
            company.next_contact_check=None
        elif company.first_no_contacts is None:
            company.first_no_contacts=timezone.now()
            company.next_contact_check=company.first_no_contacts+timedelta(days=7)
        # Only source-owned fields: concurrent/manual CRM edits are never saved from this instance.
        company.save(update_fields=['kind','name','registered_on','source_data','activity_checked_at','region','city','district','egr_state','source_changed_at','admitted_at','first_no_contacts','next_contact_check','search_name'])
    return company
