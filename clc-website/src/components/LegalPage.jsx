import { ArrowLeft, FileText, Mail } from 'lucide-react'
import { UI, useLang } from '../i18n.jsx'
import { LEGAL_DOCS } from '../legal.js'
import { Reveal } from './ui.jsx'

// Turns emails and clc.tz addresses inside plain text into links.
const LINK = /(info@clc\.tz|https?:\/\/clc\.tz|www\.clc\.tz)/g
function Linked({ text }) {
  return text.split(LINK).map((part, i) => {
    if (i % 2 === 0) return part
    const href = part.includes('@') ? `mailto:${part}` : part.startsWith('http') ? part : `https://${part}`
    return <a key={i} href={href}>{part}</a>
  })
}

function Block({ b }) {
  if (typeof b === 'string') return <p>{b.split('\n').map((line, i) => <span key={i}>{i > 0 && <br />}<Linked text={line} /></span>)}</p>
  if (b.h) return <h2>{b.h}</h2>
  if (b.ul) return <ul>{b.ul.map((li) => <li key={li}><Linked text={li} /></li>)}</ul>
  if (b.quote) return <blockquote>{b.quote}</blockquote>
  return null
}

export default function LegalPage({ doc }) {
  const { t } = useLang()
  return (
    <main className="mpage legal">
      <div className="mpage-hero">
        <div className="container">
          <a href="#home" className="mback"><ArrowLeft size={16} /> {t(UI.media.home)}</a>
          <Reveal>
            <h1>{doc.title}</h1>
            <p className="march-sub">{doc.sub}</p>
            <div className="mmeta">{doc.meta.map((m) => <span key={m}>{m}</span>)}</div>
          </Reveal>
        </div>
      </div>
      <div className="container mpage-inner">
        <article className="legal-body">
          {doc.body.map((b, i) => <Block key={i} b={b} />)}
        </article>
        <aside className="mside">
          <h4 className="mside-title">{t({ sw: 'Nyaraka za Kisheria', en: 'Legal documents', zh: '法律文件' })}</h4>
          <nav className="legal-nav">
            {LEGAL_DOCS.map((d) => (
              <a key={d.slug} href={`#/${d.slug}`} className={d.slug === doc.slug ? 'active' : ''}>
                <FileText size={16} /> {t(d.short)}
              </a>
            ))}
          </nav>
          <div className="mcta">
            <h4>{t({ sw: 'Una swali?', en: 'Questions?', zh: '有疑问？' })}</h4>
            <p>{t({ sw: 'Wasiliana nasi kuhusu faragha au taarifa zako.', en: 'Contact us about privacy or your personal data.', zh: '如对隐私或个人数据有疑问，请联系我们。' })}</p>
            <a href="mailto:info@clc.tz" className="btn btn-gold"><Mail size={16} /> info@clc.tz</a>
          </div>
        </aside>
      </div>
    </main>
  )
}
