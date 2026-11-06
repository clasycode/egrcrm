from django.conf import settings
import os
from django.utils import timezone

def ui_context(request):
    from .access import is_owner,permitted
    owner=is_owner(request.user)
    result={'app_version':settings.APP_VERSION,'crm_owner':owner,'ui_authenticated':permitted(request.user) and request.path!='/login/','ui_preview':os.environ.get('CRM_UI_PREVIEW')=='1','ui_today':timezone.localdate(),'ui_now':timezone.now()}
    path=request.path
    if path=='/':
        section='new' if request.GET.get('new') else 'clients' if request.GET.get('status')=='Клиент' else 'companies'
        title={'new':'Новые компании','clients':'Клиенты','companies':'Компании'}[section]
    elif path.startswith('/companies/') or path.startswith('/contacts/'):
        section,title='companies','Карточка компании'
    else:
        names={'work':'Моя обработка','tasks':'Задачи','selections':'Подборки','candidates':'Ожидают контактов','imports':'Сбор данных','runs':'Запуски сбора','backups':'Резервные копии','settings':'Настройки','statistics':'Статистика','diagnostics':'Диагностика','jobs':'Результат запроса','design-system':'Дизайн-система'}
        segment=path.strip('/').split('/')[0]
        section,title=segment,names.get(segment,'Рабочее пространство')
        if section=='jobs': section='imports'
    result.update(ui_nav=section,ui_page_label=title)
    if not result['ui_authenticated']: return result
    from .models import Company,Task
    from django.db.models import Count,Q
    from django.core.cache import cache
    counts=cache.get('crm-nav-counts-v31')
    if counts is None:
        counts=Company.objects.filter(admitted_at__isnull=False).aggregate(total=Count('pk'),new=Count('pk',filter=Q(status='Новая')),clients=Count('pk',filter=Q(status='Клиент')))
        counts.update(Task.objects.filter(completed_at__isnull=True).aggregate(today=Count('pk',filter=Q(due_at__date=timezone.localdate())),overdue=Count('pk',filter=Q(due_at__lt=timezone.now()))))
        cache.set('crm-nav-counts-v31',counts,5)
    result['ui_counts']=counts
    return result
