FILTER_LABELS={'q':'Поиск','kind':'Тип','status':'Статус','owner':'Ответственный','region':'Область','city':'Город','district':'Район','egr_state':'Состояние ЕГР','start':'Регистрация с','end':'Регистрация по','oked':'ОКЭД','exclude_oked':'Без ОКЭД','activity':'Деятельность','contact':'Контакт','current':'Актуальность','allowed':'Обращения','verification':'Проверка номера','task':'Задачи','changed':'Обновления','day':'День сбора','run':'Запуск'}
VALUES={'kind':{'1':'ЮЛ','2':'ИП'},'contact':{'phone':'Телефон','email':'Email','site':'Сайт'},'current':{'1':'Только текущие'},'allowed':{'1':'Без запрета'},'task':{'open':'Есть открытые','overdue':'Есть просроченные'},'changed':{'1':'Источник изменился'}}
def list_context(request):
    chips=[]
    for key,label in FILTER_LABELS.items():
        value=request.GET.get(key,'')
        if not value: continue
        display=VALUES.get(key,{}).get(value,value)
        params=request.GET.copy();params.pop(key,None);params.pop('page',None)
        chips.append({'label':label,'value':display,'url':'?'+params.urlencode()})
    quick=[(key,value) for key,value in request.GET.items() if key not in ('q','status','sort','page','selected','format','notes') and value]
    scope=[(key,request.GET[key]) for key in ('new','outreach','changed','run','day','sort') if request.GET.get(key)]
    return {'filter_chips':chips,'quick_hidden_filters':quick,'scope_hidden_filters':scope,'has_filters':bool(chips)}
