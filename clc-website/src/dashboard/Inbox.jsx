import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Bot, CircleAlert, FlaskConical, Loader2, Lock, RotateCcw, Send, UserRound, X } from 'lucide-react'
import { api } from './api.js'
import { Badge } from './shared.jsx'

const TABS = [['queued', 'Waiting'], ['with_staff', 'With staff'], ['bot', 'Bot'], ['closed', 'Closed'], ['all', 'All']]
const TONE = { queued: 'red', with_staff: 'blue', bot: 'navy', closed: 'green' }
const LABEL = { queued: 'Waiting for CLC', with_staff: 'With staff', bot: 'Bot', closed: 'Closed' }
const DETAIL = /^#\/app\/admin\/conversations\/([0-9a-f-]{36})/

const when = (d) => {
  const x = new Date(d)
  const today = new Date()
  return x.toDateString() === today.toDateString() ? x.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : x.toLocaleDateString([], { day: 'numeric', month: 'short' })
}

export default function Inbox() {
  const [selected, setSelected] = useState(() => (window.location.hash.match(DETAIL) || [])[1] || null)
  const [tab, setTab] = useState(() => ((window.location.hash.match(DETAIL) || [])[1] ? 'all' : 'queued')) // opening a chat by link shows it in the list
  const [list, setList] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const on = () => setSelected((window.location.hash.match(DETAIL) || [])[1] || null)
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  const loadList = useCallback(() => api('admin/conversations/').then((r) => { setList(r); setError('') }).catch((e) => setError(e.message)), [])
  useEffect(() => {
    loadList()
    const t = setInterval(loadList, 8000) // new client messages show up without a refresh
    return () => clearInterval(t)
  }, [loadList])

  const shown = (list || []).filter((c) => tab === 'all' || c.status === tab)
  const count = (s) => (list || []).filter((c) => c.status === s).length
  const open = (id) => { window.location.hash = `#/app/admin/conversations/${id}` }

  return (
    <div className={`inb ${selected ? 'has-thread' : ''}`}>
      <aside className="inb-list">
        <header>
          <h1>Conversations</h1>
          <div className="inb-tabs">
            {TABS.map(([k, l]) => <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}{k !== 'all' && list ? <b>{count(k)}</b> : null}</button>)}
          </div>
        </header>
        <div className="inb-rows">
          {!list && !error && <div className="fb-center-inline"><Loader2 className="spin" /></div>}
          {error && <p className="fb-error"><CircleAlert size={16} /> {error}</p>}
          {list && shown.length === 0 && <p className="fb-muted inb-empty">No conversations here.</p>}
          {shown.map((c) => (
            <button key={c.id} className={`inb-row ${selected === c.id ? 'on' : ''}`} onClick={() => open(c.id)}>
              <span className="inb-ava"><UserRound size={18} /></span>
              <span className="inb-main">
                <strong>{c.client_name || c.client_phone || 'Unknown client'}{c.is_test && <em><FlaskConical size={11} /> test</em>}</strong>
                <small>{c.last_message || '—'}</small>
              </span>
              <span className="inb-meta">
                <small>{when(c.last_at)}</small>
                <Badge tone={TONE[c.status]}>{LABEL[c.status]}</Badge>
              </span>
            </button>
          ))}
        </div>
      </aside>

      <section className="inb-thread">
        {selected ? <Thread key={selected} id={selected} onChanged={loadList} /> : (
          <div className="inb-none"><Bot size={34} /><p>Choose a conversation to read it, take it over and write to the client on WhatsApp.</p></div>
        )}
      </section>
    </div>
  )
}

function Thread({ id, onChanged }) {
  const [c, setC] = useState(null)
  const [error, setError] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef(null)
  const count = useRef(0)

  const load = useCallback(() => api(`admin/conversations/${id}/`).then((d) => { setC(d); setError('') }).catch((e) => setError(e.message)), [id])
  useEffect(() => {
    load()
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [load])

  const items = c ? [...c.messages.map((m) => ({ ...m, kind: 'msg' })), ...(c.events || []).map((e) => ({ ...e, kind: 'event' }))].sort((a, b) => new Date(a.at) - new Date(b.at)) : []
  useEffect(() => {
    if (c && items.length !== count.current) { count.current = items.length; end.current?.scrollIntoView({ block: 'end' }) }
  }, [c]) // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (path) => {
    setBusy(true)
    setError('')
    try { setC(await api(`admin/conversations/${id}/${path}/`, { method: 'POST' })); onChanged() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const send = async (e) => {
    e?.preventDefault()
    if (!text.trim() || busy) return
    setBusy(true)
    setError('')
    try {
      const res = await api(`admin/conversations/${id}/send/`, { method: 'POST', body: { text } })
      setC(res.conversation)
      setText('')
      onChanged()
    } catch (err) {
      setError(err.message)
      load()
    } finally {
      setBusy(false)
    }
  }

  if (!c) return <div className="inb-none">{error ? <p className="fb-error"><CircleAlert size={16} /> {error}</p> : <Loader2 className="spin" />}</div>
  const closed = c.status === 'closed'

  return (
    <>
      <header className="inb-head">
        <a href="#/app/admin/conversations" className="inb-back" aria-label="Back to the list"><ArrowLeft size={18} /></a>
        <span className="inb-ava lg"><UserRound size={20} /></span>
        <div className="inb-who">
          <strong>{c.client_name || 'Client'}{c.is_test && <em><FlaskConical size={11} /> flow test</em>}</strong>
          <small>{c.client_phone || (c.is_test ? 'Created from the flow builder test' : '')}{c.taken_over_by ? ` · handled by ${c.taken_over_by}` : ''}</small>
        </div>
        <Badge tone={TONE[c.status]}>{LABEL[c.status]}</Badge>
        <div className="inb-actions">
          {c.matter_id && <a className="btn btn-outline-navy d-sm" href={`#/app/admin/matters/${c.matter_id}`}>Matter {c.matter_reference}</a>}
          {c.status !== 'with_staff' && !closed && <button className="btn btn-gold d-sm" disabled={busy} onClick={() => act('take-over')}>Take over</button>}
          {c.status === 'with_staff' && <button className="btn btn-outline-navy d-sm" disabled={busy} onClick={() => act('release')}><RotateCcw size={14} /> Give back to bot</button>}
          {!closed && <button className="u-icon danger" title="Close conversation" aria-label="Close conversation" disabled={busy} onClick={() => act('close')}><X size={16} /></button>}
        </div>
      </header>

      <div className="inb-msgs">
        {items.length === 0 && <p className="fb-muted inb-empty">No messages yet.</p>}
        {items.map((m) => m.kind === 'event' ? (
          <div key={m.id} className="inb-event"><span><b>{m.who}</b> {m.text} · {when(m.at)}</span></div>
        ) : (
          <div key={m.id} className={`inb-msg ${m.direction === 'inbound' ? 'in' : 'out'} ${m.sender}`}>
            <small>{m.sender === 'client' ? (c.client_name || 'Client') : m.sender === 'bot' ? 'CLC bot' : m.staff || 'CLC team'} · {when(m.at)}</small>
            <p>{m.body || `(${m.type})`}</p>
            {m.status === 'failed' && <span className="inb-fail"><CircleAlert size={12} /> Not delivered{m.error ? `: ${m.error}` : ''}</span>}
          </div>
        ))}
        <div ref={end} />
      </div>

      {error && <p className="fb-error inb-err"><CircleAlert size={16} /> {error}</p>}
      {closed ? (
        <p className="inb-note"><Lock size={14} /> This conversation is closed. When the client writes again, a new one starts.</p>
      ) : !c.window_open ? (
        <p className="inb-note"><Lock size={14} /> It has been more than 24 hours since the client last wrote, so WhatsApp does not allow a free message. You can reply once they write again.</p>
      ) : (
        <form className="inb-compose" onSubmit={send}>
          <textarea rows={2} value={text} placeholder={c.status === 'with_staff' ? 'Write a message to the client on WhatsApp…' : 'Write a message. Sending takes over this chat, so the bot stops replying.'}
            onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) send(e) }} />
          <button className="btn btn-gold" disabled={busy || !text.trim()} aria-label="Send">{busy ? <Loader2 size={16} className="spin" /> : <Send size={16} />}</button>
        </form>
      )}
    </>
  )
}
