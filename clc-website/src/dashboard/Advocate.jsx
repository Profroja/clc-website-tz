import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Briefcase, CalendarCheck, CheckCircle2, CircleAlert, Download, FileText, Loader2, Lock, MessageSquare, Plus, Send, Timer, UserRound } from 'lucide-react'
import { api, download } from './api.js'
import { Badge, Card, DetailList, Modal, PageHead, StatCard } from './shared.jsx'
import { navigate } from '../router.js'

const fmt = (d) => (d ? new Date(d).toLocaleDateString() : '—')
const fmtTime = (d) => (d ? new Date(d).toLocaleString() : '—')
const pad = (n) => String(n).padStart(2, '0')
const greeting = () => { const h = new Date().getHours(); return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening' }
const STATUS_TONE = { New: 'amber', 'In progress': 'blue', 'Ready to close': 'green', Closed: 'green' }
const PRIORITY_TONE = { high: 'red', urgent: 'red', low: 'navy', normal: 'blue' }

function useLoad(path) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => { api(path).then(setData).catch((e) => setError(e.message)) }, [path])
  return { data, error }
}

function Loading({ error }) {
  if (error) return <Card><p className="fb-error"><CircleAlert size={18} /> {error}</p></Card>
  return <div className="fb-center-inline"><Loader2 className="spin" /></div>
}

const goMatters = () => { navigate('/app/advocate/matters') }
const openMatter = (id) => { navigate(`/app/advocate/matters/${id}`) }

/* ---------------------------------------------------------------- Dashboard */
export function AdvocateOverview() {
  const { data, error } = useLoad('advocate/overview/')

  return (
    <>
      <PageHead title="Advocate dashboard" sub={data ? `${greeting()}, ${data.name.split(' ')[0]}` : ''} />
      {!data ? <Loading error={error} /> : (
        <>
          <div className="adv-cards three">
            <button className="adv-card" onClick={goMatters}>
              <StatCard icon={Briefcase} label="Assigned matters" value={pad(data.stats.assigned)} hint="Waiting for you to start" tone="gold" />
            </button>
            <button className="adv-card" onClick={goMatters}>
              <StatCard icon={Timer} label="In progress" value={pad(data.stats.in_progress)} hint="Matters you are working on" tone="blue" delay={0.05} />
            </button>
            <button className="adv-card" onClick={goMatters}>
              <StatCard icon={CheckCircle2} label="Closed" value={pad(data.stats.closed)} hint="Matters that are finished" tone="green" delay={0.1} />
            </button>
          </div>

          <h2 className="adv-heading">My assigned matters</h2>
          {data.matters.length === 0 && <Card><p className="fb-muted">No matters are assigned to you yet. They appear here when your firm assigns one.</p></Card>}
          <div className="adv-matters">
            {data.matters.map((m) => (
              <article key={m.id} className="adv-matter">
                <header>
                  <strong>{m.reference}</strong>
                  <Badge tone={STATUS_TONE[m.status_label]}>{m.status_label}</Badge>
                </header>
                <p className="adv-service">{m.service || 'Legal matter'}</p>
                <p className="adv-client">Client: <b>{m.client}</b></p>
                <button className="btn btn-outline-navy d-sm" onClick={() => openMatter(m.id)}>Open matter <ArrowRight size={14} /></button>
              </article>
            ))}
          </div>

        </>
      )}
    </>
  )
}

function AppointmentList({ rows }) {
  if (!rows.length) return <p className="fb-muted">No appointments are assigned to you.</p>
  return (
    <div className="d-table-wrap">
      <table className="d-table">
        <thead><tr><th>S/N</th><th>Reference</th><th>Client</th><th>Contact</th><th>Meeting</th><th>Date &amp; time</th><th>Status</th></tr></thead>
        <tbody>
          {rows.map((a, i) => (
            <tr key={a.id}>
              <td>{i + 1}</td><td className="nowrap">{a.reference}</td>
              <td><strong>{a.full_name}</strong><small>{a.service}</small></td>
              <td>{a.phone}<small>{a.email}</small></td>
              <td>{a.meeting_label}</td>
              <td className="nowrap"><strong>{fmt(`${a.date}T00:00:00`)}</strong><small>{a.time} EAT</small></td>
              <td><Badge>{a.status}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ---------------------------------------------------------------- My details (opened from the user menu) */
export function AdvocateDetails() {
  const { data, error } = useLoad('advocate/overview/')
  const d = data?.details
  return (
    <>
      <PageHead title="My details" sub="Your professional details as CLC holds them." />
      {!d ? <Loading error={error} /> : (
        <Card>
          <DetailList rows={[
          ['Full name', d.full_name], ['Law firm', d.law_firm], ['Email', d.email], ['Phone', d.phone],
          ['Roll number', d.roll_number], ['Practising certificate', d.practising_certificate],
          ['Certificate expires', fmt(d.certificate_expires_on)], ['Verified by CLC', d.verified ? 'Yes' : 'Not yet'],
          ['Areas of expertise', d.areas_of_expertise.join(', ')],
          ]} />
        </Card>
      )}
    </>
  )
}

/* ---------------------------------------------------------------- Matters */
const MATTER = /^\/app\/advocate\/matters\/([0-9a-f-]{36})/

export function AdvocateMatters() {
  const [id, setId] = useState(() => (window.location.pathname.match(MATTER) || [])[1] || null)
  useEffect(() => {
    const on = () => setId((window.location.pathname.match(MATTER) || [])[1] || null)
    window.addEventListener('popstate', on)
    return () => window.removeEventListener('popstate', on)
  }, [])
  return id ? <MatterPage key={id} id={id} /> : <MatterList />
}

function MatterList() {
  const { data, error } = useLoad('advocate/matters/')
  return (
    <>
      <PageHead title="Matters" sub="The matters assigned to you." />
      {!data ? <Loading error={error} /> : (
        <Card>
          {data.length === 0 ? <p className="fb-muted">No matters are assigned to you yet.</p> : (
            <div className="d-table-wrap">
              <table className="d-table">
                <thead><tr><th>S/N</th><th>Reference</th><th>Service</th><th>Client</th><th>Region</th><th>Priority</th><th>Status</th><th>Action</th></tr></thead>
                <tbody>
                  {data.map((m, i) => (
                    <tr key={m.id} className="clickable-row" onClick={() => openMatter(m.id)}>
                      <td>{i + 1}</td>
                      <td className="nowrap"><strong>{m.reference}</strong></td>
                      <td>{m.service || '—'}</td>
                      <td className="nowrap">{m.client}</td>
                      <td>{m.region || '—'}</td>
                      <td><Badge tone={PRIORITY_TONE[m.priority]}>{m.priority}</Badge></td>
                      <td><Badge tone={STATUS_TONE[m.status_label]}>{m.status_label}</Badge></td>
                      <td onClick={(e) => e.stopPropagation()}><button className="btn btn-outline-navy d-sm" onClick={() => openMatter(m.id)}>Open <ArrowRight size={14} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </>
  )
}

const TABS = ['Documents', 'Appointments', 'Payments', 'Notes', 'Matter status']

function MatterPage({ id }) {
  const [m, setM] = useState(null)
  const [error, setError] = useState('')
  const [tab, setTab] = useState('Documents')
  const [view, setView] = useState(() => (CHAT.test(window.location.pathname) ? 'chat' : 'case')) // the case data and the WhatsApp chat are separate pages
  const [closing, setClosing] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => api(`advocate/matters/${id}/`).then(setM).catch((e) => setError(e.message)), [id])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    const on = () => setView(CHAT.test(window.location.pathname) ? 'chat' : 'case')
    window.addEventListener('popstate', on)
    return () => window.removeEventListener('popstate', on)
  }, [])

  const start = async () => {
    setBusy(true)
    try { await api(`advocate/matters/${id}/start/`, { method: 'POST' }); await load() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (m && view === 'chat') return <AdvocateChat id={id} m={m} />
  return (
    <>
      <a href="/app/advocate/matters" className="ap-back"><ArrowLeft size={16} /> Matters</a>
      {!m ? <Loading error={error} /> : (
        <>
          <PageHead title={m.reference} sub={`${m.service || 'Legal matter'} · Client ${m.client}`}>
            <Badge tone={STATUS_TONE[m.status_label]}>{m.status_label}</Badge>
            <a className="btn btn-outline-navy" href={`/app/advocate/matters/${id}/chat`}><MessageSquare size={16} /> WhatsApp chat</a>
            {m.status === 'advocate_assigned' && <button className="btn btn-gold" onClick={start} disabled={busy}>{busy ? <Loader2 size={16} className="spin" /> : 'Start work'}</button>}
            {m.status === 'active' && !m.ready_to_close_at && <button className="btn btn-outline-navy" onClick={() => setClosing(true)}>Mark ready to close</button>}
          </PageHead>
          {error && <Card><p className="fb-error"><CircleAlert size={18} /> {error}</p></Card>}
          <Card>
                <DetailList rows={[['Service', m.service], ['Region', m.region], ['Priority', m.priority], ['Assigned', fmt(m.assigned_at)], ['Summary', m.summary]]} />
              </Card>
              <Card>
                <div className="ap-tabs">{TABS.map((t) => <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>{t}</button>)}</div>
                {tab === 'Documents' && <Documents id={id} />}
                {tab === 'Appointments' && <MatterAppointments id={id} />}
                {tab === 'Payments' && <Payments id={id} closed={m.status === 'closed'} />}
                {tab === 'Notes' && <Notes id={id} />}
                {tab === 'Matter status' && <Status m={m} />}
              </Card>
          {closing && <ReadyToClose id={id} onClose={() => setClosing(false)} onDone={() => { setClosing(false); load() }} />}
        </>
      )}
    </>
  )
}

const CHAT = /^\/app\/advocate\/matters\/[0-9a-f-]{36}\/chat/
const when = (d) => {
  const x = new Date(d)
  return x.toDateString() === new Date().toDateString() ? x.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : x.toLocaleDateString([], { day: 'numeric', month: 'short' })
}
const CHAT_LABEL = { bot: 'Bot', queued: 'Waiting for CLC', with_staff: 'With staff', closed: 'Closed' }
const CHAT_TONE = { bot: 'navy', queued: 'red', with_staff: 'blue', closed: 'green' }

/* The conversation with the client: the advocate's chats on the left, the open one on the right (same as the admin inbox) */
function AdvocateChat({ id, m }) {
  const { data } = useLoad('advocate/matters/')
  const [q, setQ] = useState('')
  const rows = (data || []).filter((r) => r.chat.has && `${r.reference} ${r.service || ''} ${r.client}`.toLowerCase().includes(q.trim().toLowerCase()))
  return (
    <div className="inb has-thread">
      <aside className="inb-list">
        <header>
          <h1>Conversations</h1>
          <input className="inb-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search conversations..." />
        </header>
        <div className="inb-rows">
          {!data && <div className="fb-center-inline"><Loader2 className="spin" /></div>}
          {data && rows.length === 0 && <p className="fb-muted inb-empty">No conversations here.</p>}
          {rows.map((r) => (
            <a key={r.id} href={`/app/advocate/matters/${r.id}/chat`} className={`inb-row ${r.id === id ? 'on' : ''}`}>
              <span className="inb-ava"><UserRound size={18} /></span>
              <span className="inb-main">
                <strong>{r.reference}</strong>
                <small>{r.chat.last_message || `${r.service || 'Matter'} · ${r.client}`}</small>
              </span>
              <span className="inb-meta">
                <small>{r.chat.last_at ? when(r.chat.last_at) : ''}</small>
                <Badge tone={STATUS_TONE[r.status_label]}>{r.status_label}</Badge>
              </span>
            </a>
          ))}
        </div>
      </aside>
      <section className="inb-thread"><Messages key={id} id={id} m={m} /></section>
    </div>
  )
}

function Messages({ id, m }) {
  const [chat, setChat] = useState(null)
  const [error, setError] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef(null)
  const count = useRef(0)

  const load = useCallback(() => api(`advocate/matters/${id}/messages/`).then((d) => { setChat(d); setError('') }).catch((e) => setError(e.message)), [id])
  useEffect(() => {
    load()
    const t = setInterval(load, 5000) // new client messages show up without a refresh
    return () => clearInterval(t)
  }, [load])

  const items = chat ? [...chat.messages.map((x) => ({ ...x, kind: 'msg' })), ...chat.events.map((e) => ({ ...e, kind: 'event' }))].sort((a, b) => new Date(a.at) - new Date(b.at)) : []
  useEffect(() => {
    if (items.length !== count.current) { count.current = items.length; end.current?.scrollIntoView({ block: 'end' }) }
  }, [items.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (path, body) => {
    setBusy(true)
    setError('')
    try {
      const res = await api(`advocate/matters/${id}/chat/${path}/`, { method: 'POST', body })
      setChat(res.chat || res)
      return true
    } catch (e) {
      setError(e.message)
      load()
      return false
    } finally {
      setBusy(false)
    }
  }
  const send = async (e) => {
    e?.preventDefault()
    if (!text.trim() || busy) return
    if (await act('send', { text })) setText('')
  }

  if (!chat) return <div className="inb-none">{error ? <p className="fb-error"><CircleAlert size={16} /> {error}</p> : <Loader2 className="spin" />}</div>
  if (!chat.conversation_id) return <div className="inb-none"><p>There are no messages with the client yet.</p></div>
  const started = m.status === 'active'

  return (
    <>
      <header className="inb-head">
        <a href={`/app/advocate/matters/${id}`} className="inb-back" aria-label="Back to the case"><ArrowLeft size={18} /></a>
        <span className="inb-ava lg"><UserRound size={20} /></span>
        <div className="inb-who">
          <strong>Client {m.client}</strong>
          <small>{chat.mine ? 'You are chatting with the client' : chat.handled_by ? `Handled by ${chat.handled_by}` : chat.status === 'bot' ? 'The CLC bot is answering' : ''}</small>
        </div>
        <Badge tone={CHAT_TONE[chat.status]}>{CHAT_LABEL[chat.status]}</Badge>
        <div className="inb-actions">
          <a className="btn btn-outline-navy d-sm" href={`/app/advocate/matters/${id}`}>Matter {m.reference}</a>
          {started && chat.can_chat && !chat.mine && <button className="btn btn-gold d-sm" disabled={busy} onClick={() => act('take-over')}>Take over</button>}
          {chat.mine && chat.can_chat && <button className="btn btn-outline-navy d-sm" disabled={busy} onClick={() => act('release')}>Give back to bot</button>}
        </div>
      </header>

      <div className="inb-msgs">
        {items.length === 0 && <p className="fb-muted inb-empty">No messages yet.</p>}
        {items.map((x) => x.kind === 'event' ? (
          <div key={x.id} className="inb-event"><span><b>{x.who}</b> {x.text} · {when(x.at)}</span></div>
        ) : (
          <div key={x.id} className={`inb-msg ${x.direction === 'inbound' ? 'in' : 'out'} ${x.sender}`}>
            <small>{x.sender === 'client' ? 'Client' : x.sender === 'bot' ? 'CLC bot' : x.mine ? 'You' : x.staff || 'CLC team'} · {when(x.at)}</small>
            <p>{x.body || `(${x.type})`}</p>
            {x.status === 'failed' && <span className="inb-fail"><CircleAlert size={12} /> Not delivered</span>}
          </div>
        ))}
        <div ref={end} />
      </div>

      {error && <p className="fb-error inb-err"><CircleAlert size={16} /> {error}</p>}
      {!started ? (
        <p className="inb-note"><Lock size={14} /> Start work on this matter to chat with the client.</p>
      ) : chat.status === 'closed' ? (
        <p className="inb-note"><Lock size={14} /> This conversation is closed.</p>
      ) : !chat.window_open ? (
        <p className="inb-note"><Lock size={14} /> It has been more than 24 hours since the client last wrote, so WhatsApp does not allow a free message. You can reply once they write again.</p>
      ) : (
        <form className="inb-compose" onSubmit={send}>
          <textarea rows={2} value={text} placeholder={chat.mine ? 'Write a message to the client on WhatsApp…' : 'Write a message. Sending takes over this chat, so the bot stops replying.'}
            onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) send(e) }} />
          <button className="btn btn-gold" disabled={busy || !text.trim()} aria-label="Send">{busy ? <Loader2 size={16} className="spin" /> : <Send size={16} />}</button>
        </form>
      )}
    </>
  )
}

const size = (b) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`)

function Documents({ id }) {
  const { data, error } = useLoad(`advocate/matters/${id}/documents/`)
  if (!data) return <Loading error={error} />
  if (!data.length) return <p className="fb-muted">No documents have been shared on this matter yet.</p>
  return (
    <div className="d-table-wrap">
      <table className="d-table">
        <thead><tr><th>S/N</th><th>File</th><th>Type</th><th>Size</th><th>Added</th><th>Action</th></tr></thead>
        <tbody>
          {data.map((d, i) => (
            <tr key={d.id}>
              <td>{i + 1}</td>
              <td><span className="adv-file"><FileText size={16} /> <strong>{d.file_name}</strong></span></td>
              <td>{d.category}</td><td>{size(d.size_bytes)}</td><td className="nowrap">{fmt(d.at)}</td>
              <td><button className="u-icon" title="Download" aria-label={`Download ${d.file_name}`} onClick={() => download(`advocate/matters/${id}/documents/${d.id}/download/`, d.file_name)}><Download size={16} /></button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MatterAppointments({ id }) {
  const { data, error } = useLoad(`advocate/matters/${id}/appointments/`)
  return !data ? <Loading error={error} /> : <AppointmentList rows={data} />
}

const METHODS = [['cash', 'Cash'], ['bank_transfer', 'Bank transfer'], ['mobile_money', 'Mobile money'], ['cheque', 'Cheque'], ['other', 'Other']]
const money = (a, c) => `${c} ${Number(a).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const today = () => new Date().toISOString().slice(0, 10)
const BLANK_PAY = () => ({ purpose: '', amount: '', currency: 'TZS', paid_on: today(), method: 'cash', reference: '', note: '' })

function Payments({ id, closed }) {
  const [data, setData] = useState(null)
  const [form, setForm] = useState(BLANK_PAY())
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [formError, setFormError] = useState('')
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })
  useEffect(() => { api(`advocate/matters/${id}/payments/`).then(setData).catch((e) => setError(e.message)) }, [id])

  const add = async (e) => {
    e.preventDefault()
    setBusy(true)
    setFormError('')
    try { setData(await api(`advocate/matters/${id}/payments/`, { method: 'POST', body: form })); setForm(BLANK_PAY()); setOpen(false) } catch (err) { setFormError(err.message) } finally { setBusy(false) }
  }

  return (
    <>
      {!closed && <div className="adv-pay-bar"><button className="btn btn-gold" onClick={() => { setFormError(''); setOpen(true) }}><Plus size={16} /> Record a payment</button></div>}
      {open && (
        <Modal wide title="Record a payment" onClose={() => setOpen(false)}
          footer={<><button type="button" className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button>
            <button form="pay-form" className="btn btn-gold" disabled={busy}>{busy ? <Loader2 size={16} className="spin" /> : 'Record payment'}</button></>}>
          <form id="pay-form" className="d-form adv-pay-form" onSubmit={add}>
            <div className="d-form-grid">
              <label className="d-field"><span>What it was for</span><input required autoFocus value={form.purpose} onChange={set('purpose')} placeholder="e.g. Legal fee, court filing fee" /></label>
              <label className="d-field"><span>Amount</span>
                <span className="adv-amount">
                  <select value={form.currency} onChange={set('currency')}><option>TZS</option><option>USD</option></select>
                  <input required inputMode="decimal" value={form.amount} onChange={set('amount')} placeholder="0.00" />
                </span>
              </label>
              <label className="d-field"><span>Date paid</span><input type="date" required max={today()} value={form.paid_on} onChange={set('paid_on')} /></label>
              <label className="d-field"><span>Method</span><select value={form.method} onChange={set('method')}>{METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
              <label className="d-field"><span>Receipt or transaction number</span><input value={form.reference} onChange={set('reference')} /></label>
              <label className="d-field"><span>Note</span><input value={form.note} onChange={set('note')} /></label>
            </div>
            {formError && <p className="fb-error"><CircleAlert size={16} /> {formError}</p>}
          </form>
        </Modal>
      )}
      {error && <p className="fb-error"><CircleAlert size={16} /> {error}</p>}
      {!data ? <Loading /> : data.rows.length === 0 ? <p className="fb-muted">No payments have been recorded for this matter.</p> : (
        <>
          <p className="adv-pay-total">Total recorded: {data.totals.map((t) => money(t.amount, t.currency)).join(' · ')}</p>
          <div className="d-table-wrap">
            <table className="d-table">
              <thead><tr><th>Date</th><th>For</th><th>Amount</th><th>Method</th><th>Reference</th><th>Recorded by</th></tr></thead>
              <tbody>
                {data.rows.map((p) => (
                  <tr key={p.id}>
                    <td className="nowrap">{fmt(p.paid_on)}</td>
                    <td>{p.purpose}{p.note && <small className="adv-pay-note">{p.note}</small>}</td>
                    <td className="nowrap"><strong>{money(p.amount, p.currency)}</strong></td>
                    <td>{p.method}</td><td>{p.reference || '—'}</td><td>{p.recorded_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}

function Notes({ id }) {
  const [notes, setNotes] = useState(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { api(`advocate/matters/${id}/notes/`).then(setNotes).catch((e) => setError(e.message)) }, [id])

  const add = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try { setNotes(await api(`advocate/matters/${id}/notes/`, { method: 'POST', body: { body: text } })); setText('') } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return (
    <>
      <form className="adv-note-form" onSubmit={add}>
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="Write a note. Only your law firm can see it." />
        <button className="btn btn-gold" disabled={busy || !text.trim()}>{busy ? <Loader2 size={16} className="spin" /> : <><Send size={15} /> Add note</>}</button>
      </form>
      {error && <p className="fb-error"><CircleAlert size={16} /> {error}</p>}
      {!notes ? <Loading /> : notes.length === 0 ? <p className="fb-muted">No notes yet.</p> : (
        <ul className="adv-notes">
          {notes.map((n) => <li key={n.id}><small>{n.author} · {fmtTime(n.at)}</small><p>{n.body}</p></li>)}
        </ul>
      )}
    </>
  )
}

function Status({ m }) {
  return (
    <>
      <div className="ap-top"><Badge tone={STATUS_TONE[m.status_label]}>{m.status_label}</Badge>
        {m.ready_to_close_at && <span className="ap-muted">Marked ready to close on {fmt(m.ready_to_close_at)}: CLC will close it.</span>}</div>
      {m.ready_to_close_summary && <p className="d-text">{m.ready_to_close_summary}</p>}
      <h4 className="d-sub">History</h4>
      {m.history.length === 0 ? <p className="fb-muted">No changes yet.</p> : (
        <ul className="d-timeline">
          {[...m.history].reverse().map((h, i) => <li key={i}><span>{fmtTime(h.at)} · {h.by}</span>{h.note || `${h.from || 'new'} → ${h.to}`}</li>)}
        </ul>
      )}
    </>
  )
}

function ReadyToClose({ id, onClose, onDone }) {
  const [summary, setSummary] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async () => {
    setBusy(true)
    setError('')
    try { await api(`advocate/matters/${id}/ready-to-close/`, { method: 'POST', body: { summary } }); onDone() } catch (e) { setError(e.message); setBusy(false) }
  }
  return (
    <Modal title="Mark ready to close" onClose={onClose}
      footer={<><button className="btn btn-ghost" onClick={onClose}>Cancel</button><button className="btn btn-gold" onClick={submit} disabled={busy || !summary.trim()}>{busy ? <Loader2 size={16} className="spin" /> : 'Mark ready to close'}</button></>}>
      <p className="d-text">Tell CLC the work is finished. CLC reviews it and closes the matter.</p>
      <div className="d-form"><label className="d-field"><span>Short summary of the outcome</span><textarea rows={4} value={summary} onChange={(e) => setSummary(e.target.value)} /></label></div>
      {error && <p className="fb-error"><CircleAlert size={16} /> {error}</p>}
    </Modal>
  )
}

/* ---------------------------------------------------------------- Subscription plans */
export function AdvocateSubscription() {
  const { data, error } = useLoad('advocate/subscription/')
  const sub = data?.subscription
  return (
    <>
      <PageHead title="Subscription plans" sub="Your law firm's plan on the CLC platform." />
      {!data ? <Loading error={error} /> : (
        <Card title={data.law_firm}>
          {!sub ? <p className="fb-muted">No subscription details are on record for your firm yet.</p> : (
            <>
              <div className="ap-top"><Badge>{sub.status}</Badge>{sub.in_trial && <span className="ap-muted">{sub.trial_days_left} days of trial left</span>}</div>
              <DetailList rows={[
                ['Plan status', sub.status_label], ['Trial start', fmt(sub.trial_start)], ['Trial end', fmt(sub.trial_end)],
                ['Billing starts', fmt(sub.billing_effective)],
              ]} />
              <p className="ap-muted adv-hint"><CalendarCheck size={15} /> Every newly approved law firm receives a 2-month trial. Any agreed financial terms start after the trial unless CLC sets another date.</p>
            </>
          )}
        </Card>
      )}
    </>
  )
}
