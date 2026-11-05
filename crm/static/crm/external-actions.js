(() => {
  const storageKey='crm-external-actions:v1:'+document.body.dataset.uiUser;
  let visited=new Set();
  try {
    const stored=JSON.parse(localStorage.getItem(storageKey)||'[]');
    if (Array.isArray(stored)) visited=new Set(stored.filter(value=>typeof value==='string').slice(-1000));
  } catch (_) { /* Links keep working when browser storage is unavailable. */ }
  function update() {
    document.querySelectorAll('[data-external-action]').forEach(link=>{
      const used=visited.has(link.dataset.externalAction);
      link.classList.toggle('is-visited',used);
      if (!link.dataset.originalTitle) link.dataset.originalTitle=link.title;
      if (!link.dataset.originalLabel) link.dataset.originalLabel=link.getAttribute('aria-label')||link.title;
      link.title=link.dataset.originalTitle+(used?' · Уже открывали':'');
      link.setAttribute('aria-label',link.dataset.originalLabel+(used?' · Уже открывали':''));
    });
  }
  function mark(event) {
    if (event.type==='auxclick'&&event.button!==1) return;
    const link=event.target.closest('[data-external-action]');
    if (!link) return;
    visited.delete(link.dataset.externalAction);
    visited.add(link.dataset.externalAction);
    visited=new Set([...visited].slice(-1000));
    try {localStorage.setItem(storageKey,JSON.stringify([...visited]));} catch (_) {}
    update();
  }
  document.addEventListener('click',mark);
  document.addEventListener('auxclick',mark);
  document.addEventListener('crm:table-updated',update);
  window.addEventListener('storage',event=>{
    if (event.key!==storageKey) return;
    try {const stored=JSON.parse(event.newValue||'[]');if(Array.isArray(stored))visited=new Set(stored.filter(v=>typeof v==='string').slice(-1000));} catch (_) {}
    update();
  });
  update();
})();
