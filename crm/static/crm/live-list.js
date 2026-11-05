(() => {
  if (!document.getElementById('company-table')) return;
  function editing() {
    const table=document.getElementById('company-table');
    return document.querySelector('input[name="selected"]:checked, dialog[open], [data-inline-status][data-saving], [data-copy-company][aria-busy="true"]') || table?.contains(document.activeElement);
  }
  async function tick() {
    const note=document.getElementById('list-update-note');
    try {
      if (editing()) {
        note.textContent='Вы работаете с таблицей: автообновление приостановлено. Сбор продолжается.';
      } else if (!document.hidden) {
        const response=await fetch('/companies/fragment/'+location.search,{cache:'no-store'});
        if (!response.ok || response.redirected) throw new Error();
        const doc=new DOMParser().parseFromString(await response.text(),'text/html');
        const table=doc.getElementById('company-table');
        if (table && !editing()) {
          const previous=document.getElementById('company-table');
          const scroll=previous.querySelector('.table-scroll').scrollLeft;
          previous.replaceWith(table);
          table.querySelector('.table-scroll').scrollLeft=scroll;
          document.dispatchEvent(new Event('crm:table-updated'));
          note.textContent='Таблица обновлена без сброса фильтров и прокрутки.';
        }
      }
    } catch (_) {note.textContent='Автообновление недоступно. Проверьте соединение или обновите страницу.';}
    finally {setTimeout(tick,15000);}
  }
  setTimeout(tick,15000);
})();
