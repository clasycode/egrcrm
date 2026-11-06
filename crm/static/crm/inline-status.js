(() => {
  document.documentElement.classList.add('js-inline-status');
  async function save(form) {
    if (form.dataset.saving||form.closest('tr')?.dataset.leadSaving) return;
    const select=form.elements.status, previous=form.elements.previous_status;
    const feedback=form.querySelector('[role="status"]');
    if (select.value===previous.value) return;
    const data=new FormData(form);
    form.dataset.saving='true';
    select.disabled=true;
    form.setAttribute('aria-busy','true');
    feedback.textContent='Сохраняем…';
    feedback.classList.remove('warning');
    try {
      const response=await fetch(form.action,{method:'POST',body:data,headers:{Accept:'application/json'},credentials:'same-origin'});
      if (response.redirected) throw new Error('Сессия завершена. Обновите страницу и войдите снова.');
      let result;
      try {result=await response.json();} catch (_) {throw new Error('Не удалось сохранить статус. Обновите страницу и проверьте результат.');}
      if (!response.ok) throw new Error(result.error||'Не удалось сохранить статус. Попробуйте ещё раз.');
      previous.value=result.status;
      document.querySelectorAll('[data-client-count]').forEach(node=>node.textContent=result.clients);
      form.querySelector('[data-status-badge]').innerHTML=result.badge;
      form.closest('tr').classList.toggle('is-client',result.status==='Клиент');
      const card=form.closest('tr');
      const actions=card.querySelector('[data-lead-actions]');
      if(actions&&result.actions)actions.outerHTML=result.actions;
      document.dispatchEvent(new CustomEvent('crm:lead-saved',{detail:result}));
      feedback.textContent=result.counted?'Сохранено · +1 к плану':'Сохранено';
    } catch (error) {
      select.value=previous.value;
      feedback.textContent=error instanceof TypeError?'Нет соединения. Обновите страницу и проверьте результат.':error.message||'Не удалось сохранить статус.';
      feedback.classList.add('warning');
    } finally {
      select.disabled=false;
      form.removeAttribute('aria-busy');
      delete form.dataset.saving;
    }
  }
  document.addEventListener('change',event=>{
    const form=event.target.closest('[data-inline-status]');
    if (form&&event.target.name==='status') save(form);
  });
  document.addEventListener('submit',event=>{
    if (event.target.matches('[data-inline-status]')) {event.preventDefault();save(event.target);}
  });
})();
