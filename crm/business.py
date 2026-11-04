from django.db.models import Q
from .models import CRMStatus
PHONE_RESULTS=['Не проверен','ЛПР','Сотрудник','Общий номер','Не отвечает','Неверный/не существует','Не относится к компании']
DEFAULT_STATUSES=['Новая','В работе','Переговоры','Клиент','Вернуться позже','Не подходит','Отказ']
def status_choices():
    values=list(CRMStatus.objects.filter(active=True).values_list('name','name'))
    return values or [(s,s) for s in DEFAULT_STATUSES]
def team():
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(is_active=True).filter(Q(is_staff=True)|Q(is_superuser=True)|Q(crm_profile__role__in=['owner','manager'])).distinct()
