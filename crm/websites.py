import ipaddress,json,os,re
from urllib.parse import urlsplit,urlencode
from urllib.request import Request,urlopen
from .models import WebsiteSearch

def safe_url(value):
    from django.core.validators import URLValidator
    from django.core.exceptions import ValidationError
    try: URLValidator(schemes=['http','https'])(value)
    except ValidationError as exc: raise ValueError('Некорректный URL сайта') from exc
    parsed=urlsplit(value)
    if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password: raise ValueError('Разрешены только внешние HTTP(S)-адреса')
    host=parsed.hostname.casefold()
    if host=='localhost' or host.endswith(('.localhost','.local','.internal')): raise ValueError('Внутренний адрес недопустим')
    try:
        if not ipaddress.ip_address(host).is_global: raise ValueError('Внутренний IP недопустим')
    except ValueError:
        if re.fullmatch(r'[0-9.]+',host) or ':' in host: raise ValueError('Внутренний или некорректный IP')
    return value

def search_website(company,actor,confirmed=False):
    if not confirmed: return WebsiteSearch.objects.create(company=company,actor=actor,state='not_checked',evidence='Не подтверждены настройки и возможные расходы')
    if not os.environ.get('BRAVE_API_KEY'): return WebsiteSearch.objects.create(company=company,actor=actor,state='unconfigured',evidence='Поиск не настроен: нужен BRAVE_API_KEY в защищённых настройках сервера')
    return WebsiteSearch.objects.create(company=company,actor=actor,state='pending',provider='Brave Search')

def run_search_once():
    from django.utils import timezone
    from datetime import timedelta
    WebsiteSearch.objects.filter(state='running',created_at__lt=timezone.now()-timedelta(minutes=10)).update(state='error',evidence='Процесс прервался; результат неизвестен. Автоматический платный повтор не выполнен.')
    job=WebsiteSearch.objects.filter(state='pending').select_related('company').order_by('pk').first()
    if not job: return
    # Conditional claim prevents duplicate paid requests across workers.
    if not WebsiteSearch.objects.filter(pk=job.pk,state='pending').update(state='running'): return
    try:
        key=os.environ.get('BRAVE_API_KEY')
        if not key: raise ValueError('Провайдер не настроен')
        query=f'{job.company.unp} {job.company.name}'
        endpoint='https://api.search.brave.com/res/v1/web/search?'+urlencode({'q':query,'count':5})
        request=Request(endpoint,headers={'Accept':'application/json','X-Subscription-Token':key})
        with urlopen(request,timeout=20) as response: data=json.loads(response.read(2_000_001))
        candidates=[]
        for hit in (data.get('web') or {}).get('results',[])[:5]:
            try: url=safe_url(hit.get('url',''))
            except ValueError: continue
            description=(hit.get('description') or '')[:2000]
            candidates.append({'url':url,'title':hit.get('title',''),'excerpt':description,'unp_match':job.company.unp in description})
        job.state='review' if candidates else 'not_found'
        job.evidence=json.dumps(candidates,ensure_ascii=False)
    except Exception as exc:
        job.state='error';job.evidence=f'Ошибка провайдера ({type(exc).__name__}). Отсутствие сайта не установлено.'
    job.save(update_fields=['state','evidence'])
