(() => {
  let toastTimer;
  function toast(text) {const node=document.getElementById('ui-toast');if(!node)return;node.textContent=text;node.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>node.hidden=true,3500);}
  async function write(text) {
    if (navigator.clipboard?.writeText) {try {await navigator.clipboard.writeText(text);return true;} catch (_) {}}
    return false;
  }
  document.addEventListener('click',async event=>{
    const review=event.target.closest('[data-review-company]');
    if (review) {
      const dialog=document.getElementById('review-dialog');
      dialog.querySelector('[name="company"]').value=review.dataset.reviewCompany;
      dialog.querySelector('[data-review-name]').textContent=review.dataset.reviewName;
      dialog.querySelector('[name="note"]').value='';
    }
    const button=event.target.closest('[data-copy-company]');
    if (button&&!button.disabled) {
      button.disabled=true;button.setAttribute('aria-busy','true');
      try {
        const response=await fetch('/companies/'+button.dataset.copyCompany+'/copy/',{headers:{Accept:'text/plain'},cache:'no-store'});
        if (!response.ok||response.redirected)throw new Error('Не удалось получить карточку. Обновите страницу и проверьте вход.');
        const content=await response.text();
        if (await write(content))toast('Полная карточка скопирована');
        else {
          const dialog=document.getElementById('company-copy-dialog');
          const text=document.getElementById('company-copy-text');text.value=content;
          if(!dialog.open)dialog.showModal();text.focus();text.select();
          dialog.addEventListener('close',()=>button.focus(),{once:true});
        }
      } catch(error) {toast(error instanceof TypeError?'Нет соединения. Попробуйте скопировать карточку ещё раз.':error.message||'Не удалось скопировать данные.');}
      finally {button.disabled=false;button.removeAttribute('aria-busy');}
    }
    if(event.target.closest('[data-copy-prepared]')) {
      const text=document.getElementById('company-copy-text');
      if(await write(text.value))toast('Полная карточка скопирована');
      else {text.focus();text.select();document.querySelector('[data-copy-help]').textContent='Текст выделен. Нажмите Ctrl+C (или Cmd+C на Mac), чтобы скопировать.';}
    }
  });
})();
