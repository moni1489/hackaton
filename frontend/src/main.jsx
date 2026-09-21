import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'

// Страницы демонстрации грузятся отдельными чанками: телефон зрителя не тянет весь дашборд
// (карты, графики) и index.css с внешними шрифтами — экран зрителя открывается и без интернета.
const App = lazy(() => Promise.all([import('./index.css'), import('./App.jsx')]).then(([, page]) => page))
const Live = lazy(() => import('./Live.jsx'))

const path = window.location.pathname
// /#replay — запись сценария без нашего сервера: годится любой статический веб-сервер.
const replay = window.location.hash === '#replay'
// /demo — то же приложение: панель оператора открывается на вкладке демонстрации.
const Page = replay || path.startsWith('/live/') ? Live : App

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Suspense fallback={null}>
      <Page />
    </Suspense>
  </StrictMode>,
)
