import { useEffect } from 'react'
import Navbar from './components/Navbar.jsx'
import HeroSlider from './components/HeroSlider.jsx'
import { ServiceBands, ServicesOverview } from './components/Services.jsx'
import { LiveMini, MediaArchive, MediaPage, MediaSection } from './components/Media.jsx'
import Login from './components/Login.jsx'
import LivePage from './components/LivePage.jsx'
import LegalPage from './components/LegalPage.jsx'
import { legalDoc } from './legal.js'
import { usePath } from './router.js'
import AppointmentModal from './appointments/AppointmentModal.jsx'
import Dashboard from './dashboard/Dashboard.jsx'
import Join from './join/Join.jsx'
import Track from './join/Track.jsx'
import { FinalCTA, FloatingWA, Footer, HowItWorks, PartnerSection, WhyChoose } from './components/Sections.jsx'

const MEDIA_ROUTE = /^\/media\/(.+)$/
const JOIN_ROUTE = /^\/join\/(track|edit)\/([0-9a-f-]{36})\/([\w-]+)$/

export default function App() {
  const path = usePath()
  const match = path.match(MEDIA_ROUTE)
  const mediaId = match ? decodeURIComponent(match[1]) : null
  const legal = legalDoc(path.slice(1))

  // A new page starts at the top, unless a section link (#services from another page) asked for a section.
  useEffect(() => {
    const section = window.history.state?.section
    const el = section && document.getElementById(section)
    if (el) el.scrollIntoView()
    else window.scrollTo(0, 0)
  }, [path])

  if (path === '/login') return <Login />
  if (path === '/join') return <Join />
  const joinRoute = path.match(JOIN_ROUTE)
  if (joinRoute) {
    const [, mode, id, token] = joinRoute
    return mode === 'track' ? <Track id={id} token={token} /> : <Join resume={{ id, token }} />
  }
  if (path.startsWith('/app')) return <Dashboard path={path} />

  return (
    <>
      <Navbar />
      {path === '/media' ? (
        <MediaArchive />
      ) : path === '/live' ? (
        <LivePage />
      ) : legal ? (
        <LegalPage doc={legal} />
      ) : mediaId ? (
        <MediaPage id={mediaId} />
      ) : (
        <main>
          <HeroSlider />
          <ServicesOverview />
          <ServiceBands />
          <HowItWorks />
          <WhyChoose />
          <MediaSection />
          <PartnerSection />
          <FinalCTA />
        </main>
      )}
      <Footer />
      <LiveMini hidden={path === '/live'} />
      <AppointmentModal />
      <FloatingWA />
    </>
  )
}
