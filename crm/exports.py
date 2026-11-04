import csv,tempfile
from django.http import StreamingHttpResponse,FileResponse
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from .models import Company
HEADERS=['УНП','Название','Регистрация','Текущие телефоны','Исторические телефоны','Email','Сайт','CRM-статус','Не связываться','ОКЭД','Виды деятельности','Текущий адрес','Область','Город','Район','Состояние ЕГР','Ответственный','Заметки']
def safe_csv(value):
    text=str(value or '')
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')) else text

def values(row,include_notes=False,outreach=False):
    contacts=[c for c in row.contacts.all() if not outreach or not c.do_not_contact]
    def contact(kind,current=None): return ', '.join(c.raw for c in contacts if c.kind==kind and (current is None or c.current==current))
    return [row.unp,row.name,str(row.registered_on or ''),contact('phone',True),contact('phone',False),contact('email'),contact('site'),row.status,'Да' if row.do_not_contact else 'Нет',', '.join(a.code for a in row.current_activities),'; '.join(a.name for a in row.current_activities),row.current_address,row.region,row.city,row.district,row.egr_state,row.owner.username if row.owner else '',row.notes if include_notes else '']

def export_rows(request,rows):
    from .access import is_owner
    notes=is_owner(request.user) and (request.GET.get('notes')=='1' or request.POST.get('include_notes')=='on')
    selected=request.POST.getlist('selected') if request.method=='POST' else request.GET.getlist('selected')
    if selected: rows=rows.filter(pk__in=[int(x) for x in selected if x.isdigit()])
    rows=rows.select_related('owner')
    if request.GET.get('format')=='xlsx':
        output=tempfile.TemporaryFile()
        wb=Workbook(write_only=True);ws=wb.create_sheet('Компании');ws.append(HEADERS)
        for row in rows.iterator(chunk_size=100):
            cells=[]
            for value in values(row,notes,request.GET.get('outreach')=='1'):
                from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
                cell=WriteOnlyCell(ws,value=ILLEGAL_CHARACTERS_RE.sub('�',str(value)));cell.data_type='s';cells.append(cell)
            ws.append(cells)
        wb.save(output);output.seek(0)
        return FileResponse(output,as_attachment=True,filename='companies.xlsx')
    class Echo:
        def write(self,value): return value
    writer=csv.writer(Echo(),delimiter=';')
    def generate():
        yield '\ufeff';yield writer.writerow(HEADERS)
        for row in rows.iterator(chunk_size=100):
            data=[safe_csv(x) for x in values(row,notes,request.GET.get('outreach')=='1')]
            for i in [0,3,4,9]: data[i]="'"+data[i] if not data[i].startswith("'") else data[i]
            yield writer.writerow(data)
    response=StreamingHttpResponse(generate(),content_type='text/csv; charset=utf-8')
    response['Content-Disposition']='attachment; filename="companies.csv"'
    return response
