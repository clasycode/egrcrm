(() => {
  const $=(s,root=document)=>root.querySelector(s);
  const $$=(s,root=document)=>[...root.querySelectorAll(s)];
  let dialogTrigger=null, toastTimer;
  function selected(){return $$('input[type="checkbox"][name="selected"]:checked');}
  function updateSelection(){
    const values=selected();
    $$('[data-selected-count]').forEach(n=>n.textContent=values.length);
    const dock=$('[data-selection-dock]');if(dock)dock.hidden=!values.length;
    $$('input[type="checkbox"][name="selected"]').forEach(n=>n.closest('tr')?.classList.toggle('is-selected',n.checked));
    const all=$('[data-select-page]'),checkboxes=$$('input[type="checkbox"][name="selected"]');
    if(all){all.checked=!!checkboxes.length&&values.length===checkboxes.length;all.indeterminate=values.length>0&&values.length<checkboxes.length;}
    $$('[data-export-scope]').forEach(n=>n.textContent=values.length?`Выбранные компании: ${values.length}`:'Все компании текущего результата фильтра');
  }
  function mirrorSelection(form){
    $$('input[data-selection-copy]',form).forEach(n=>n.remove());
    selected().forEach(box=>{const input=document.createElement('input');input.type='hidden';input.name='selected';input.value=box.value;input.dataset.selectionCopy='true';form.append(input);});
  }
  function openDialog(dialog,trigger){
    if(!dialog)return;dialogTrigger=trigger;updateSelection();
    const form=$('form[data-export-form]',dialog);if(form)mirrorSelection(form);
    if(!dialog.open)dialog.showModal();
  }
  function notify(text){const box=$('#ui-toast');if(!box)return;box.textContent=text;box.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>box.hidden=true,2500);}
  document.addEventListener('click',async e=>{
    const opener=e.target.closest('[data-dialog-open]');
    if(opener){e.preventDefault();openDialog(document.getElementById(opener.dataset.dialogOpen),opener);}
    const close=e.target.closest('[data-dialog-close]');if(close){e.preventDefault();close.closest('dialog')?.close();}
    if(e.target.closest('[data-clear-selection]')){$$('input[type="checkbox"][name="selected"]').forEach(n=>n.checked=false);updateSelection();}
    const copy=e.target.closest('[data-copy]');
    if(copy){e.preventDefault();try{await navigator.clipboard.writeText(copy.dataset.copy);notify('Скопировано');}catch(_){notify('Копирование недоступно. Выделите значение вручную.');}}
    if(e.target.closest('[data-nav-toggle]')){
      const nav=$('#workspace-navigation'),toggle=$('[data-nav-toggle]');
      const open=nav.classList.toggle('is-open');$('[data-nav-scrim]')?.classList.toggle('is-visible',open);toggle.setAttribute('aria-expanded',String(open));
      nav.inert=!open;$('.workspace').inert=open;
      if(open){nav.setAttribute('role','dialog');nav.setAttribute('aria-modal','true');$('[data-nav-close]',nav)?.focus();}
    }
    if(e.target.closest('[data-nav-scrim]')||e.target.closest('[data-nav-close]'))closeNavigation();
    if(e.target.closest('[data-search-trigger]'))search(e.target.closest('button'));
  });
  function closeNavigation(){const wasOpen=!!$('#workspace-navigation.is-open');const nav=$('#workspace-navigation');nav?.classList.remove('is-open');nav?.removeAttribute('role');nav?.removeAttribute('aria-modal');if(nav)nav.inert=matchMedia('(max-width:700px)').matches;const workspace=$('.workspace');if(workspace)workspace.inert=false;$('[data-nav-scrim]')?.classList.remove('is-visible');$('[data-nav-toggle]')?.setAttribute('aria-expanded','false');if(wasOpen)$('[data-nav-toggle]')?.focus();}
  function search(trigger){if($('dialog[open]'))return;closeNavigation();const field=$('[data-primary-search]');if(field){field.focus();field.select();}else openDialog($('#global-search'),trigger);}
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();search($('[data-search-trigger]'));}if(e.key==='Escape'){const opened=$('#workspace-navigation.is-open');closeNavigation();if(opened)$('[data-nav-toggle]')?.focus();}if(e.key==='Tab'&&$('#workspace-navigation.is-open')){const items=$$('a,button', $('#workspace-navigation')).filter(n=>n.getClientRects().length);const first=items[0],last=items[items.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
  document.addEventListener('change',e=>{
    if(e.target.matches('[data-select-page]')){$$('input[type="checkbox"][name="selected"]').forEach(n=>n.checked=e.target.checked);updateSelection();}
    if(e.target.matches('input[name="selected"]'))updateSelection();
    if(e.target.matches('[data-bulk-action]'))updateBulk(e.target);
  });
  function updateBulk(select){
    const form=select.closest('form');
    $$('[data-bulk-fields]',form).forEach(group=>{group.hidden=group.dataset.bulkFields!==select.value;$$('input,select,textarea',group).forEach(field=>field.disabled=group.hidden);});
  }
  $$('[data-bulk-action]').forEach(updateBulk);
  document.addEventListener('submit',e=>{if(e.target.matches('[data-export-form]'))mirrorSelection(e.target);});
  $$('dialog').forEach(dialog=>{
    const heading=$('h2',dialog);if(heading){heading.id=dialog.id+'-title';dialog.setAttribute('aria-labelledby',heading.id);}
    dialog.addEventListener('close',()=>{dialogTrigger?.focus();dialogTrigger=null;});
    dialog.addEventListener('click',e=>{if(e.target===dialog){const rect=dialog.getBoundingClientRect();if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom)dialog.close();}});
    if(dialog.dataset.hasErrors==='true')openDialog(dialog,null);
  });
  document.addEventListener('crm:table-updated',updateSelection);
  const mobile=matchMedia('(max-width:700px)');function resizeNavigation(){if(!mobile.matches||!$('#workspace-navigation')?.classList.contains('is-open'))closeNavigation();}mobile.addEventListener('change',resizeNavigation);resizeNavigation();
  updateSelection();
})();
