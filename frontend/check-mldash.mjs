/* Две проверки на записи сценария (public/demo-replay.json — те же данные, что приходят с сервера):
     1) экран зрителя (ML-дашборд) рендерится на всех кадрах;
     2) слой demoData отдаёт экранам приложения ответы в форме боевого API.
   Запуск: node check-mldash.mjs */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const server = await createServer({ appType: 'custom', logLevel: 'error', server: { middlewareMode: true } });
try {
  const { LiveView } = await server.ssrLoadModule('/src/MlDash.jsx');
  const { default: demoSource } = await server.ssrLoadModule('/src/demoData.js');
  const frames = JSON.parse(readFileSync(new URL('./public/demo-replay.json', import.meta.url), 'utf8'));
  assert(frames.length > 0, 'запись сценария пуста');

  let withMl = 0;
  for (const frame of frames) {
    const html = renderToStaticMarkup(createElement(LiveView, { view: frame.view, conn: 'replay' }));
    assert(html.includes('САМ ВКО — ML в реальном времени'), `кадр «${frame.label}» без заголовка`);
    assert(html.includes('Модель 1 · Атрибуция причины'), `кадр «${frame.label}» без блока атрибуции`);
    if (frame.view.ml) {
      withMl += 1;
      assert(html.includes(frame.view.ml.cause_label), `кадр «${frame.label}» без вердикта модели`);
      assert(html.includes('Вклад признаков в вердикт'), `кадр «${frame.label}» без вклада признаков`);
      assert(html.includes('Риск нарушения SLA'), `кадр «${frame.label}» без прогноза SLA`);
    }
  }
  assert(withMl > 0, 'в записи нет ни одного кадра с результатом модели');

  // Экраны приложения во время демо: формы ответов должны совпадать с боевым API.
  for (const frame of frames) {
    const d = demoSource(frame.view, 'check');
    const { overview } = d;
    assert(overview.total_schools === frame.view.schools.length, 'overview: не все школы');
    assert(overview.filters.regions[0] === 'Все районы', 'overview: нет справочника районов');
    const rows = d.schools({});
    assert(rows.length === frame.view.schools.length, 'schools: потеряны школы');
    assert(rows.every((s) => s.lat > 47 && s.lat < 51 && s.lng > 82 && s.lng < 85),
      'schools: координаты вне ВКО — точки не лягут на карту');
    if (frame.view.incident) {
      assert(d.incidents.length === frame.view.incident.affected.length, 'incidents: не все аварии');
      const card = d.school(frame.view.incident.affected[0]);
      assert(card.lines.length === 1 && card.devices.length > 0, 'карточка школы: нет линии или ПК');
      assert(typeof card.workstations.total === 'number', 'карточка школы: рабочие места');
      assert(d.schoolMeasurements(card.id).length > 1, 'карточка школы: пустой график');
      assert(typeof d.schoolAnalytics(card.id).forecast !== 'object'
        || d.schoolAnalytics(card.id).forecast === null, 'аналитика: прогноз должен быть текстом');
    }
    if (frame.view.ml) {
      const board = d.mlBoard(40);
      assert(board.length > 0 && board[0].cause_label, 'доска диагностики пуста');
      assert(d.mlSummary.total_affected === board.length, 'сводка не сходится с доской');
      assert(d.mlForecast(12).every((f) => f.probability >= 0), 'прогноз SLA без вероятности');
    }
  }
  console.log(`ok: ${frames.length} кадров, из них с вердиктом модели — ${withMl};`
    + ' формы ответов для экранов приложения проверены');
} finally {
  await server.close();
}
