"""Complete plain-text company brief; no UI truncation or external transmission."""
import json
from django.utils import timezone

def date(value):
    if not value:return 'Не указано'
    return timezone.localtime(value).strftime('%d.%m.%Y %H:%M') if hasattr(value,'hour') else value.strftime('%d.%m.%Y')

def company_brief(row):
    lines=['КАРТОЧКА КОМПАНИИ',row.name or 'Название не получено',f'УНП: {row.unp}',f'Страна: {row.country}',f'Тип: {row.get_kind_display()}',f'Регистрация: {date(row.registered_on)}',f'Состояние ЕГР: {row.egr_state or "Не получено"}',f'Добавлена в CRM: {date(row.admitted_at)}','', 'РАБОТА С КОМПАНИЕЙ',f'CRM-статус: {row.status}',f'Ответственный: {row.owner.get_full_name() or row.owner.username if row.owner else "Не назначен"}',f'Не связываться: {"ДА" if row.do_not_contact else "Нет"}',f'Заметки:\n{row.notes or "Нет заметок"}', '', 'АДРЕС И ГЕОГРАФИЯ',f'Текущий адрес: {row.current_address or "Не получен"}',f'Область: {row.region or "Не получена"}',f'Город: {row.city or "Не получен"}',f'Район: {row.district or "Не получен"}', '', 'КОНТАКТЫ']
    for contact in row.contacts.all():
        lines.extend([f'- {contact.get_kind_display()}: {contact.raw}',f'  Актуальность по источнику: {"Текущий" if contact.current else "Исторический"}; проверка: {contact.verification}',f'  Не связываться: {"ДА" if contact.do_not_contact else "Нет"}; источник: {contact.source}',f'  Первое/последнее получение: {date(contact.first_seen)} / {date(contact.last_seen)}',f'  Комментарий: {contact.comment or "Нет"}'])
        lines.append(f'  Последняя ручная проверка: {date(contact.checked_at)}; проверил: {contact.checked_by.username if contact.checked_by else "Не указан"}')
        for check in contact.checks.select_related('actor').order_by('created_at','pk'):
            lines.append(f'  Проверка {date(check.created_at)}: {check.result}; {check.note}')
        if contact.original_line and contact.original_line!=contact.raw: lines.append(f'  Исходная строка: {contact.original_line}')
    if not row.contacts.exists():lines.append('Контакты отсутствуют')
    lines+=['','ВИДЫ ДЕЯТЕЛЬНОСТИ / ОКЭД']
    for activity in row.activities.all():lines.append(f'- {activity.code}: {activity.name} ({"текущий" if activity.current else "исторический"})')
    if not row.activities.exists():lines.append('Нет сведений')
    lines+=['','ЗАДАЧИ']
    for task in row.tasks.select_related('owner').order_by('due_at','pk'):
        lines.append(f'- {task.title} | срок: {date(task.due_at)} | {"Выполнена "+date(task.completed_at) if task.completed_at else "Открыта"} | ответственный: {task.owner.username if task.owner else "Не назначен"}')
    lines+=['','ОБРАЩЕНИЯ И РЕЗУЛЬТАТЫ']
    for event in row.interactions.select_related('actor','contact').order_by('occurred_at','pk'):
        lines.append(f'- {date(event.occurred_at)} | {event.get_kind_display()} | {event.get_result_display()} | {event.actor.username if event.actor else "Пользователь удалён"}\n  Контакт: {event.contact.raw if event.contact else "Не указан"}\n  {event.note}')
    lines+=['','ИТОГИ ОБРАБОТКИ']
    for review in row.reviews.select_related('user').order_by('completed_at','pk'):
        lines.append(f'- {date(review.completed_at)} | {review.user.username} | {review.get_result_display()}\n  {review.note}')
    lines+=['','ПОИСК САЙТА']
    for website in row.website_searches.order_by('created_at','pk'):lines.append(f'- {website.url or "Без адреса"} | {website.get_state_display()}\n  {website.evidence}')
    lines+=['','ИСТОРИЯ ИЗМЕНЕНИЙ CRM']
    for audit in row.audit.select_related('actor').order_by('created_at','pk'):
        lines.append(f'- {date(audit.created_at)} | {audit.actor.username if audit.actor else "Система"}\n'+json.dumps(audit.changes,ensure_ascii=False,indent=2))
    lines+=['','СВЕДЕНИЯ ИСТОЧНИКА ЕГР (JSON)',json.dumps(row.source_data,ensure_ascii=False,indent=2),'','ИСТОРИЯ СНИМКОВ ЕГР']
    for snapshot in row.snapshots.order_by('observed_at','pk'):lines.extend([date(snapshot.observed_at),json.dumps(snapshot.data,ensure_ascii=False,indent=2)])
    lines+=['','Примечание: текущие сведения ЕГР и ручная проверка контакта — разные признаки. Запреты обращений необходимо учитывать.']
    return '\n'.join(lines)
