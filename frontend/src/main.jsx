import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'

// Страницы демонстрации грузятся отдельными чанками: телефон зрителя не тянет весь дашборд
// (карты, графики) и index.css с внешними шрифтами — экран зрителя открывается и без интернета.
const App = lazy(() => Promise.all([import('./index.css'), import('./App.jsx')]).then(([, page]) => page))
const Live = lazy(() => import('./Live.jsx'))
const DemoControl = lazy(() => import('./DemoControl.jsx'))

const path = window.location.pathname
// /#replay — запись сценария без нашего сервера: годится любой статический веб-сервер.
const replay = window.location.hash === '#replay'
const Page = replay || path.startsWith('/live/') ? Live : path === '/demo' ? DemoControl : App

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Suspense fallback={null}>
      <Page />
    </Suspense>
  </StrictMode>,
)
