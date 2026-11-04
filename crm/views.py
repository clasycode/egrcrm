from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from .models import Company, Audit, ImportDay
allowed = user_passes_test(lambda u: u.is_active and u.is_staff)

@allowed
def companies(request):
    rows = Company.objects.filter(admitted_at__isnull=False).prefetch_related('contacts')
    q = request.GET.get('q','').strip()
    if q: rows = rows.filter(Q(unp__icontains=q)|Q(name__icontains=q)|Q(contacts__raw__icontains=q)).distinct()
    if request.GET.get('new'): rows = rows.filter(viewed_at__isnull=True)
    return render(request,'crm/list.html',{'rows':rows.order_by('-admitted_at')[:200],'q':q})

@allowed
def company(request,pk):
    with transaction.atomic():
        row = get_object_or_404(Company.objects.select_for_update(),pk=pk,admitted_at__isnull=False)
        if request.method == 'POST':
            changes = {}
            for field,value in [('notes',request.POST.get('notes','')),('do_not_contact',request.POST.get('do_not_contact')=='on'),('status',request.POST.get('status',row.status))]:
                if field == 'status' and value not in dict(Company._meta.get_field('status').choices): continue
                if getattr(row,field) != value:
                    changes[field] = {'before':getattr(row,field),'after':value}
                    setattr(row,field,value)
            if changes:
                row.save(update_fields=list(changes))
                Audit.objects.create(company=row,actor=request.user,changes=changes)
            return redirect(''+request.path)
        if row.viewed_at is None:
            row.viewed_at = timezone.now()
            row.save(update_fields=['viewed_at'])
    return render(request,'crm/detail.html',{'row':row,'statuses':Company._meta.get_field('status').choices})

@allowed
def imports(request):
    return render(request,'crm/imports.html',{'rows':ImportDay.objects.order_by('-day')[:100]})
