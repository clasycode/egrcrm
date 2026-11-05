import hashlib,re
from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe
register=template.Library()
ICONS={
'telegram':'<path d="m21 4-4 16-6-5-4 3 1-6 10-6-8 8-6-2Z"/>',
'viber':'<path d="M20 11c0 5-3 8-8 8H9l-4 3v-5c-2-2-3-4-3-7 0-5 3-8 9-8s9 3 9 9Z"/><path d="m7 7 2 2-1 2a9 9 0 0 0 4 4l2-1 2 2M13 6a5 5 0 0 1 4 4M13 9a2 2 0 0 1 1 1"/>',
'browser':'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z"/>',

'companies':'<rect x="3" y="3" width="13" height="18" rx="1"/><path d="M16 9h5v12H3M7 7h5M7 11h5M7 15h5M7 19h5"/>',
'new':'<path d="M12 3v6m0 6v6M3 12h6m6 0h6M5.6 5.6l3 3m6.8 6.8 3 3m0-12.8-3 3m-6.8 6.8-3 3"/>',
'clients':'<path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 9.4l6.1-.9Z"/>',
'tasks':'<rect x="4" y="4" width="16" height="17" rx="2"/><path d="M9 3h6v4H9zM8 13l2 2 5-5M8 18h7"/>',
'collections':'<path d="M3 6h7l2 2h9v12H3ZM3 6V4h7l2 2"/>',
'collect':'<path d="M12 3v12m-4-4 4 4 4-4M4 15v5h16v-5"/>',
'queue':'<path d="M4 5h16M4 12h10M4 19h7"/><circle cx="18" cy="17" r="4"/><path d="M18 15v2l1 1"/>',
'history':'<path d="M3 11a9 9 0 1 1 2.5 7M3 4v7h7M12 7v5l3 2"/>',
'chart':'<path d="M4 3v17h17M8 15v-4m5 4V7m5 8V4"/>',
'backup':'<path d="M4 4h13l3 3v13H4ZM8 4v6h8V4M8 20v-6h8v6"/>',
'settings':'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
'help':'<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4.1 2c-1.2.7-1.6 1.2-1.6 2.5M12 17h.01"/>',
'search':'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
'filter':'<path d="M3 5h18M6 12h12M10 19h4"/>',
'arrow':'<path d="M4 12h16m-6-6 6 6-6 6"/>',
'back':'<path d="M20 12H4m6-6-6 6 6 6"/>',
'chevron':'<path d="m9 5 7 7-7 7"/>',
'close':'<path d="m6 6 12 12M6 18 18 6"/>',
'check':'<path d="m4 12 5 5L20 6"/>',
'ban':'<circle cx="12" cy="12" r="9"/><path d="m6 6 12 12"/>',
'clock':'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
'phone':'<path d="m5 3 4 1 1 5-3 2a15 15 0 0 0 6 6l2-3 5 1 1 4-3 2C10 21 3 14 3 6Z"/>',
'email':'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 6 9 7 9-7"/>',
'location':'<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0Z"/><circle cx="12" cy="10" r="2"/>',
'plus':'<path d="M12 4v16M4 12h16"/>',
'export':'<path d="M12 15V3m-4 4 4-4 4 4M4 13v7h16v-7"/>',
'dots':'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
'logout':'<path d="M9 4H4v16h5M9 12h12m-5-5 5 5-5 5"/>',
'menu':'<path d="M4 5h16M4 12h16M4 19h16"/>',
'work':'<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V3h8v4M3 12h18M10 12v3h4v-3"/>',
'negotiation':'<path d="M4 4h16v12H9l-5 4ZM8 8h8M8 12h5"/>',
'info':'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
'copy':'<rect x="8" y="8" width="12" height="13" rx="1"/><path d="M15 8V3H3v13h5"/>',
'person':'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
'shield':'<path d="m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6Z"/><path d="m8 12 3 3 5-6"/>',
'custom':'<path d="m12 3 9 9-9 9-9-9Z"/>',
}
@register.simple_tag
def ui_icon(name):
    return format_html('<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>',mark_safe(ICONS.get(name,ICONS['custom'])))

STAGES={'Новая':('new','new'),'В работе':('working','work'),'Переговоры':('negotiating','negotiation'),'Клиент':('client','clients'),'Вернуться позже':('later','clock'),'Не подходит':('inactive','close'),'Отказ':('refused','ban')}
@register.inclusion_tag('crm/components/status.html')
def stage_badge(status):
    tone,icon=STAGES.get(status,('custom','custom'))
    return {'label':status,'tone':tone,'icon':icon}

@register.inclusion_tag('crm/components/identity.html')
def company_identity(company,large=False):
    words=re.findall(r'[^\W\d_]+',company.name,flags=re.UNICODE)
    legal={'ООО','ОДО','ОАО','ЗАО','ИП','ЧУП','УП','ЧТУП','ЧТПУП','ПТУП'}
    words=[word for word in words if word.upper() not in legal]
    initials=''.join(word[0] for word in words[:2]).upper() or company.unp[-2:]
    tone=['slate','ochre','plum','olive','clay','graphite'][hashlib.sha256((company.country+company.unp).encode()).digest()[0]%6]
    return {'initials':initials,'tone':tone,'large':large,'kind':company.kind}

@register.simple_tag(takes_context=True)
def query_update(context,**changes):
    query=context['request'].GET.copy();query.pop('page',None)
    for key,value in changes.items():
        if value is None or value=='': query.pop(key,None)
        else: query[key]=value
    return '?'+query.urlencode()

@register.filter
def verification_tone(value):
    if value=='ЛПР': return 'client'
    if value in ('Неверный/не существует','Не относится к компании'): return 'refused'
    if value=='Не отвечает': return 'later'
    return 'custom'

@register.inclusion_tag('crm/components/contact_preview.html')
def company_contacts(company):
    values=list(company.contacts.all())
    phones=[c for c in values if c.kind=='phone']
    emails=[c for c in values if c.kind=='email']
    current=[c for c in phones if c.current]
    phone=(current or phones or [None])[0]
    email=([c for c in emails if c.current] or emails or [None])[0]
    return {'phone':phone,'email':email,'other':max(0,len(phones)+len(emails)-bool(phone)-bool(email)),'company':company}

@register.filter
def status_values(choices):
    return [value for value,label in choices]

@register.inclusion_tag('crm/components/phone_actions.html')
def phone_actions(contact,company,with_search=False):
    from crm.normalization import messenger_phone
    return {'contact':contact,'company':company,'number':messenger_phone(contact.raw) if contact else '', 'with_search':with_search}

@register.inclusion_tag('crm/components/company_search.html')
def company_search(company,compact=False):
    from urllib.parse import urlencode
    query=company.name.strip() or company.unp
    return {'company':company,'compact':compact,'search_url':'https://www.google.com/search?'+urlencode({'q':query})}
