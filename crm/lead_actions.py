"""Explicit outreach decisions shared by list, card and bulk status changes."""
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import CompanyReview, WorkDay, Audit, Interaction


def record_decision(company,user,before):
    if before!='Новая' or company.status=='Новая' or company.admitted_at is None:return False
    from .reviewing import summary
    now=timezone.now();day=timezone.localdate(now)
    WorkDay.objects.get_or_create(user=user,day=day,defaults={'target':summary(user,day)['target']})
    results={'В работе':'in_work','Не подходит':'not_fit','Переговоры':'negotiation','Клиент':'client','Отказ':'refusal','Вернуться позже':'follow_up'}
    _,created=CompanyReview.objects.get_or_create(company=company,user=user,defaults={'result':results.get(company.status,'changed'),'note':f'Первичный разбор: {before} → {company.status}.','completed_at':now})
    return created


def change_status(company,user,status):
    before=company.status
    if before==status:return False
    if status=='В работе' and (company.do_not_contact or company.contacts.filter(do_not_contact=True).exists()):
        raise ValidationError('У компании или контакта установлен запрет обращений. Сначала проверьте его в карточке.')
    company.status=status
    fields=['status']
    if status=='В работе' and before=='Новая':
        company.reply_state='waiting';fields.append('reply_state')
        Interaction.objects.create(company=company,actor=user,kind='message',result='attempt',note='Первое сообщение отмечено отправленным: компания переведена в работу.')
    company.save(update_fields=fields)
    Audit.objects.create(company=company,actor=user,changes={'status':{'before':before,'after':status}})
    from django.core.cache import cache
    cache.delete('crm-nav-counts-v31')
    return record_decision(company,user,before)


def mark_reply(company,user,state,record_event=True):
    if company.status!='В работе':raise ValidationError('Отметка ответа доступна для компаний в работе. Обновите список.')
    if state not in ('answered','no_reply'):raise ValidationError('Неизвестная отметка ответа.')
    if company.reply_state==state:return
    before=company.reply_state;company.reply_state=state;company.save(update_fields=['reply_state'])
    label=company.get_reply_state_display()
    if record_event:
        Interaction.objects.create(company=company,actor=user,kind='reply' if state=='answered' else 'note',result='answered' if state=='answered' else 'other',note=f'Быстрая отметка: {label}.')
    Audit.objects.create(company=company,actor=user,changes={'reply_state':{'before':before,'after':state}})
