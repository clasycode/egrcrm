from datetime import datetime
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from .models import Company, Contact, Candidate, Activity
from .egr import SourceError

def local_date(value):
    if not value: return None
    parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    return parsed.astimezone(ZoneInfo('Europe/Minsk')).date() if parsed.tzinfo else parsed.date()

def discover(client,day):
    date = day.day.strftime('%d.%m.%Y')
    records = client.get('getBaseInfoByPeriod',date,date)
    ids = []
    for record in records:
        if (record.get('nsi00208') or {}).get('nkscrt') != 1: continue
        if local_date(record.get('dfrom')) != day.day: continue
        if (record.get('nsi00211') or {}).get('nkvob') not in (1,2): continue
        unp = str(record.get('ngrn',''))
        if not unp.isdigit(): raise SourceError('Недопустимый УНП в выборке')
        ids.append(unp)
    with transaction.atomic():
        for unp in ids: Candidate.objects.get_or_create(day=day,unp=unp)
        day.discovered = True
        day.save(update_fields=['discovered','updated_at'])

def enrich(client,unp):
    base = client.get('getBaseInfoByRegNum',unp)
    if not base: raise SourceError('ЕГР вернул пустой ответ: субъект по этому УНП не найден. Это не ошибка сети.')
    if len(base)!=1 or str(base[0].get('ngrn'))!=unp: raise SourceError('Базовая запись не соответствует УНП')
    base = base[0]
    kind = (base.get('nsi00211') or {}).get('nkvob')
    if kind not in (1,2): raise SourceError('Неизвестный тип субъекта')
    names = client.get('getJurNamesByRegNum' if kind==1 else 'getIPFIOByRegNum',unp)
    current = client.get('getAddressByRegNum',unp)
    history = client.get('getAllAddressByRegNum',unp)
    activities = client.get('getVEDByRegNum',unp)
    for record in names+current+history+activities:
        if str(record.get('ngrn'))!=unp: raise SourceError('Чужой УНП в ответе обогащения')
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
        company,_ = Company.objects.get_or_create(country='BY',unp=unp,defaults={'kind':kind})
        company.kind,company.name = kind,name
        company.registered_on = local_date(base.get('dfrom'))
        company.source_data = {'base':base,'names':names,'current_addresses':current,'address_history':history,'activities':activities}
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
                    contact,created = Contact.objects.get_or_create(company=company,kind=contact_kind,raw=raw,source='ЕГР',defaults={'current':is_current,'source_data':record})
                    if not created:
                        contact.current = is_current or contact.current
                        contact.source_data = record
                        contact.save(update_fields=['current','source_data','last_seen'])
        eligible = company.contacts.filter(kind='phone').exists() or (kind==1 and company.contacts.filter(kind='email').exists())
        if eligible and company.admitted_at is None: company.admitted_at = timezone.now()
        company.save()
    return company
