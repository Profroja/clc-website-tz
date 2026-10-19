import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { LangProvider } from './i18n.jsx'
import App from './App.jsx'
import { installRouter } from './router.js'
import './styles.css'
import './dashboard/dashboard.css'
import './join/join.css'
import './appointments/appointment.css'
import './dashboard/flows/flows.css'

installRouter()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <LangProvider>
      <App />
    </LangProvider>
  </StrictMode>
)
