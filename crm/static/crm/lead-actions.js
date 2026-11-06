(() => {
  function updateProgress(result) {
    if (!result.work) return;
    if(result.history_total!==undefined)document.querySelectorAll('[data-history-total]').forEach(n=>n.textContent=result.history_total);
    if(result.history_met_days!==undefined)document.querySelectorAll('[data-history-met]').forEach(n=>n.textContent=result.history_met_days);
    if(result.history_html)document.querySelectorAll('[data-work-history]').forEach(n=>n.innerHTML=result.history_html);
    if(result.today_html)document.querySelectorAll('[data-today-decisions]').forEach(n=>n.innerHTML=result.today_html);
    if(result.lead_counts)document.querySelectorAll('[data-new-count]').forEach(n=>n.textContent=result.lead_counts.new);
    Object.entries(result.lead_counts||{}).forEach(([key,value])=>document.querySelectorAll('[data-lead-count="'+key+'"]').forEach(n=>n.textContent=value));
    document.querySelectorAll('[data-outreach-today]').forEach(n=>n.textContent=result.outreach_today);
    document.querySelectorAll('[data-replies-today]').forEach(n=>n.textContent=result.replies_today);
    document.querySelectorAll('[data-work-done]').forEach(n=>n.textContent=result.work.done);
    document.querySelectorAll('[data-work-remaining]').forEach(n=>n.textContent=result.work.remaining);
    document.querySelectorAll('[data-work-progress]').forEach(n=>{n.style.width=result.work.percent+'%';n.parentElement.setAttribute('aria-valuenow',Math.min(result.work.done,result.work.target));});
    document.querySelectorAll('[data-client-count]').forEach(n=>n.textContent=result.clients);
  }
  document.addEventListener('crm:lead-saved',event=>updateProgress(event.detail));
  document.addEventListener('submit',async event=>{
    const form=event.target;
    if (!form.matches('[data-lead-form]')) return;
    event.preventDefault();
    const card=form.closest('[data-company-id],tr,.review-card-summary');
    if (!card||card.dataset.leadSaving||card.querySelector('[data-inline-status][data-saving]')) return;
    const button=event.submitter;
    if (!button) return;
    const data=new FormData(form);data.set('action',button.value);
    const feedback=form.parentElement.querySelector('[role="status"]');
    feedback.textContent='Сохраняем…';feedback.classList.remove('warning');
    card.dataset.leadSaving='true';form.setAttribute('aria-busy','true');
    card.querySelectorAll('[data-lead-form] button,[data-inline-status] select').forEach(n=>n.disabled=true);
    try {
      const response=await fetch(form.getAttribute('action'),{method:'POST',body:data,headers:{Accept:'application/json'},credentials:'same-origin'});
      if (response.redirected)throw new Error('Сессия завершена. Обновите страницу и войдите снова.');
      let result;
      try {result=await response.json();} catch (_) {throw new Error('Не удалось сохранить. Обновите список и проверьте результат.');}
      if (!response.ok)throw new Error(result.error||'Не удалось сохранить действие.');
      const wrapper=form.closest('[data-lead-actions]');wrapper.outerHTML=result.actions;
      card.querySelectorAll('[data-status-badge],[data-lead-badge]').forEach(n=>n.innerHTML=result.badge);
      card.querySelectorAll('[data-inline-status]').forEach(f=>{f.elements.previous_status.value=result.status;f.elements.status.value=result.status;});
      card.classList.toggle('is-client',result.status==='Клиент');
      const live=card.querySelector('.lead-feedback');
      if(live)live.textContent=result.counted?'Сохранено · +1 к дневному плану':'Сохранено';
      document.dispatchEvent(new CustomEvent('crm:lead-saved',{detail:result}));
      const next=card.querySelector('[data-lead-form] button:not(:disabled)');
      if(next)next.focus({preventScroll:true});
    } catch(error) {
      feedback.textContent=error instanceof TypeError?'Нет соединения. Обновите список и проверьте результат.':error.message||'Не удалось сохранить. Обновите список.';
      feedback.classList.add('warning');
    } finally {
      delete card.dataset.leadSaving;form.removeAttribute('aria-busy');
      card.querySelectorAll('[data-lead-form] button,[data-inline-status] select').forEach(n=>{if(!n.hasAttribute('data-contact-blocked'))n.disabled=false;});
    }
  });
})();
