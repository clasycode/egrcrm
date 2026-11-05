(() => {
  if (!document.getElementById('company-table')) return;
  async function tick() {
    try {
      const note=document.getElementById('list-update-note');
      if (document.querySelector('input[name="selected"]:checked')) {
        note.textContent='Вы отмечаете строки: автообновление таблицы приостановлено, чтобы сохранить выбор. Сбор продолжается.';
      } else if (!document.hidden) {
        const response=await fetch('/companies/fragment/'+location.search,{cache:'no-store'});
        if (!response.ok || response.redirected) throw new Error();
        const doc=new DOMParser().parseFromString(await response.text(),'text/html');
        const table=doc.getElementById('company-table');
        if (table) {document.getElementById('company-table').replaceWith(table);document.dispatchEvent(new Event('crm:table-updated'));}
        note.textContent='Таблица обновлена без сброса фильтров и прокрутки.';
      }
    } catch (_) { document.getElementById('list-update-note').textContent='Автообновление недоступно. Проверьте соединение или обновите страницу.'; }
    finally { setTimeout(tick,15000); }
  }
  setTimeout(tick,15000);
})();
