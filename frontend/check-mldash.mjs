/* Проверка экрана зрителя: ML-дашборд рендерится на всех кадрах записи сценария
   (public/demo-replay.json — те же данные, что приходят зрителям с сервера).
   Запуск: node check-mldash.mjs */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const server = await createServer({ appType: 'custom', logLevel: 'error', server: { middlewareMode: true } });
try {
  const { LiveView } = await server.ssrLoadModule('/src/MlDash.jsx');
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
  console.log(`ok: ${frames.length} кадров, из них с вердиктом модели — ${withMl}`);
} finally {
  await server.close();
}
