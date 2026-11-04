"""Sequential, auditable EGR transport. Never converts failures into empty data."""
import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .models import Receipt, SourceControl

BASE = 'https://egr.gov.by/api/v2/egr/'
class SourceError(Exception): pass
class SourcePaused(SourceError): pass

class Client:
    def __init__(self,lease=None):
        self.last_request = 0
        self.lease = lease
    def get(self,method,*args):
        if SourceControl.objects.filter(pk=1,paused=True).exists(): raise SourcePaused('Источник приостановлен')
        url = BASE + method + '/' + '/'.join(str(x) for x in args)
        for attempt in range(3):
            time.sleep(max(0,1-(time.monotonic()-self.last_request)))
            self.last_request = time.monotonic()
            if self.lease:
                from .leases import renew
                if not renew(self.lease): raise SourceError('Блокировка сборщика истекла. Повторите задание.')
            try:
                with urlopen(Request(url,headers={'Accept':'application/json'}),timeout=30) as response:
                    status = response.status
                    body = response.read(10_000_001)
            except HTTPError as exc:
                status,body = exc.code,exc.read(10_000_001)
            except (URLError,TimeoutError,OSError) as exc:
                if attempt < 2:
                    time.sleep(2**(attempt+1)); continue
                raise SourceError(f'Не удалось подключиться к ЕГР ({type(exc).__name__}). Проверьте интернет, доступ к egr.gov.by и настройки сертификатов. Отсутствие данных не установлено.') from exc
            text = body.decode('utf-8',errors='replace')
            Receipt.objects.create(url=url,status=status,body=text)
            if status in (401,403,429):
                SourceControl.objects.update_or_create(pk=1,defaults={'paused':True,'reason':f'HTTP {status}: {url}'})
                raise SourcePaused(f'HTTP {status}; источник приостановлен')
            if status >= 500 and attempt < 2:
                time.sleep(2**(attempt+1)); continue
            if status == 204: return []
            if status != 200: raise SourceError(f'HTTP {status}')
            if len(body)>10_000_000: raise SourceError('Ответ превышает лимит 10 МБ; сохранён усечённый ответ')
            try: data = json.loads(text)
            except ValueError as exc: raise SourceError('Некорректный JSON') from exc
            if not isinstance(data,list) or any(not isinstance(x,dict) for x in data):
                raise SourceError('Ожидался массив объектов')
            return data
        raise SourceError(f'HTTP {status}')
