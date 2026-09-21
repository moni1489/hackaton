import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

// Страницы демонстрации грузятся отдельными чанками: телефон зрителя не тянет
// весь дашборд (карты, графики) ради одного экрана.
const App = lazy(() => import('./App.jsx'))
const Live = lazy(() => import('./Live.jsx'))
const DemoControl = lazy(() => import('./DemoControl.jsx'))

const path = window.location.pathname
const Page = path.startsWith('/live/') ? Live : path === '/demo' ? DemoControl : App

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Suspense fallback={null}>
      <Page />
    </Suspense>
  </StrictMode>,
)
