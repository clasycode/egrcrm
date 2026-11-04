(() => {
  const root = document.getElementById('collection-progress');
  if (!root) return;
  const freshness = document.getElementById('progress-freshness');
  function cell(row, text) {
    const td = document.createElement('td');
    td.textContent = text;
    row.append(td);
    return td;
  }
  function link(parent, url, text) {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.textContent = text;
    parent.append(anchor);
  }
  function render(data) {
    root.querySelectorAll('[data-counter]').forEach(node => {
      node.textContent = data[node.dataset.counter];
    });
    document.getElementById('progress-status').textContent = data.status;
    document.getElementById('progress-active').textContent = data.active.length
      ? data.active.map(item => `Сейчас: ${item.day ? item.day + ' · ' : ''}${item.unp ? 'УНП ' + item.unp : 'получение списка регистраций'}`).join('; ')
      : 'Сейчас нет активного запроса. Проверьте очередь и состояние источника.';
    const body = document.getElementById('day-progress-rows');
    if (body) {
      const fragment = document.createDocumentFragment();
      data.days.forEach(day => {
        const row = document.createElement('tr');
        cell(row, day.date);
        cell(row, !day.discovered ? 'Получение списка' : day.state === 'done' ? 'Завершено' : 'Обогащение');
        cell(row, `${day.completed} / ${day.total}${day.discovered ? '' : ' (список ещё не получен)'}`);
        cell(row, `${day.ready}`);
        cell(row, `${day.errors}${day.error ? ' · ' + day.error : ''}`);
        const downloads = cell(row, '');
        link(downloads, `/companies/export/?day=${day.id}`, 'Готовые компании CSV');
        downloads.append(document.createElement('br'));
        link(downloads, `/imports/${day.id}/export/`, 'Все найденные УНП CSV');
        fragment.append(row);
      });
      body.replaceChildren(fragment);
    }
    freshness.textContent = `Данные на ${data.as_of}. Обновляются автоматически каждые 3 секунды.`;
  }
  async function poll() {
    try {
      const response = await fetch('/imports/progress/', {cache: 'no-store', headers: {'Accept': 'application/json'}});
      if (!response.ok || response.redirected) throw new Error('Session or server error');
      render(await response.json());
    } catch (_) {
      freshness.textContent = 'Не удалось обновить прогресс. Показаны предыдущие данные. Проверьте соединение и вход в CRM.';
    } finally {
      window.setTimeout(poll, 3000);
    }
  }
  window.setTimeout(poll, 3000);
})();
