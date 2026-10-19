import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft, CheckCircle2, CircleAlert, Download, FileText, Loader2, MessageSquare, Trash2, UserCheck } from 'lucide-react'
import { api, download } from './api.js'
import { Badge, Card, DetailList, Field, Modal, PageHead } from './shared.jsx'
import { navigate } from '../router.js'

const TABS = [['all', 'All'], ['unassigned', 'Unassigned'], ['assigned', 'Assigned'], ['closed', 'Closed']]
const STATUS_TONE = { new: 'amber', under_review: 'blue', referred: 'blue', advocate_assigned: 'amber', active: 'blue', closed: 'green', referred_back: 'red' }
const PRIORITY_TONE = { high: 'red', urgent: 'red', low: 'navy', normal: 'blue' }
const fmt = (d) => (d ? new Date(d).toLocaleDateString() : '—')
const fmtTime = (d) => (d ? new Date(d).toLocaleString() : '—')
const size = (b) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`)

const DETAIL = /^\/app\/admin\/matters\/([0-9a-f-]{36})/

export default function AdminMatters() {
  const [id, setId] = useState(() => (window.location.pathname.match(DETAIL) || [])[1] || null)
  useEffect(() => {
    const on = () => setId((window.location.pathname.match(DETAIL) || [])[1] || null)
    window.addEventListener('popstate', on)
    return () => window.removeEventListener('popstate', on)
  }, [])
  return id ? <MatterPage key={id} id={id} /> : <MatterList />
}

function MatterList() {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')
  const [tab, setTab] = useState('all')
  useEffect(() => { api('admin/matters/').then(setRows).catch((e) => setError(e.message)) }, [])

  const shown = (rows || []).filter((m) => tab === 'all' || (tab === 'unassigned' ? !m.advocate && m.status !== 'closed' : tab === 'assigned' ? !!m.advocate && m.status !== 'closed' : m.status === 'closed'))
  const waiting = (rows || []).filter((m) => !m.advocate && m.status !== 'closed').length

  return (
    <>
      <PageHead title="Matters" sub="Every matter created from WhatsApp, with who is handling it." />
      {error && <Card><p className="fb-error"><CircleAlert size={18} /> {error}</p></Card>}
      <div className="d-tabs">
        {TABS.map(([k, l]) => <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}{k === 'unassigned' && rows ? ` (${waiting})` : ''}</button>)}
      </div>
      {!rows && !error && <div className="fb-center-inline"><Loader2 className="spin" /></div>}
      {rows && (
        <Card>
          <div className="d-table-wrap">
            <table className="d-table">
              <thead><tr><th>S/N</th><th>Reference</th><th>Client</th><th>Service</th><th>Region</th><th>Priority</th><th>Status</th><th>Advocate</th><th>Created</th></tr></thead>
              <tbody>
                {shown.map((m, i) => (
                  <tr key={m.id} className="clickable-row" onClick={() => { navigate(`/app/admin/matters/${m.id}`) }}>
                    <td>{i + 1}</td>
                    <td className="nowrap"><strong>{m.reference}</strong>{m.is_test && <small>Flow test</small>}</td>
                    <td><strong>{m.client_name || '—'}</strong><small>{m.client_phone}</small></td>
                    <td>{m.service || '—'}</td>
                    <td>{m.region || '—'}</td>
                    <td><Badge tone={PRIORITY_TONE[m.priority]}>{m.priority}</Badge></td>
                    <td><Badge tone={m.ready_to_close ? 'green' : STATUS_TONE[m.status]}>{m.status_label}</Badge></td>
                    <td>{m.advocate ? <><strong>{m.advocate}</strong><small>{m.firm}</small></> : <span className="ap-muted">Not assigned</span>}</td>
                    <td className="nowrap">{fmt(m.created_at)}</td>
                  </tr>
                ))}
                {shown.length === 0 && <tr><td colSpan={9} className="fb-muted">No matters here yet. They appear when a client finishes a chat that creates a matter.</td></tr>}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  )
}

function MatterPage({ id }) {
  const [m, setM] = useState(null)
  const [error, setError] = useState('')
  const [assigning, setAssigning] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [closing, setClosing] = useState(false)

  const load = useCallback(() => api(`admin/matters/${id}/`).then(setM).catch((e) => setError(e.message)), [id])
  useEffect(() => { load() }, [load])

  return (
    <>
      <a href="/app/admin/matters" className="ap-back"><ArrowLeft size={16} /> Matters</a>
      {!m ? (error ? <Card><p className="fb-error"><CircleAlert size={18} /> {error}</p></Card> : <div className="fb-center-inline"><Loader2 className="spin" /></div>) : (
        <>
          <PageHead title={m.reference} sub={`${m.service || 'Legal matter'} · ${m.client_name || 'Client'}`}>
            <Badge tone={m.ready_to_close ? 'green' : STATUS_TONE[m.status]}>{m.status_label}</Badge>
            {m.conversation_id && <a className="btn btn-outline-navy" href={`/app/admin/conversations/${m.conversation_id}`}><MessageSquare size={16} /> Open conversation</a>}
            {m.ready_to_close && <button className="btn btn-gold" onClick={() => setClosing(true)}><CheckCircle2 size={16} /> Close matter</button>}
            {m.status !== 'closed' && !m.ready_to_close && <button className="btn btn-gold" onClick={() => setAssigning(true)}><UserCheck size={16} /> {m.advocate ? 'Reassign' : 'Assign advocate'}</button>}
            <button className="btn btn-outline-navy" onClick={() => setDeleting(true)}><Trash2 size={16} /> Delete</button>
          </PageHead>

          <div className="ap-grid">
            <section className="ap-panel">
              <h4>Client</h4>
              <DetailList rows={[['Name', m.client_name], ['Phone', m.client_phone], ['Region', m.region], ['Created', fmtTime(m.created_at)]]} />
            </section>
            <section className="ap-panel">
              <h4>Handling</h4>
              <DetailList rows={[['Advocate', m.advocate || 'Not assigned yet'], ['Law firm', m.firm], ['Priority', m.priority], ['Last update', fmtTime(m.updated_at)]]} />
              {m.ready_to_close_at && <p className="ap-muted">The advocate marked this ready to close on {fmt(m.ready_to_close_at)}: {m.ready_to_close_summary}</p>}
            </section>
          </div>

          <Card title="Documents">
            {m.documents.length === 0 ? <p className="fb-muted">The client has not sent any documents.</p> : (
              <div className="d-table-wrap">
                <table className="d-table">
                  <thead><tr><th>S/N</th><th>File</th><th>Type</th><th>Size</th><th>Added</th><th>Action</th></tr></thead>
                  <tbody>
                    {m.documents.map((d, i) => (
                      <tr key={d.id}>
                        <td>{i + 1}</td><td><span className="adv-file"><FileText size={16} /> <strong>{d.file_name}</strong></span></td>
                        <td>{d.category}</td><td>{size(d.size_bytes)}</td><td className="nowrap">{fmt(d.at)}</td>
                        <td><button className="u-icon" title="Download" aria-label={`Download ${d.file_name}`} onClick={() => download(`admin/matters/${m.id}/documents/${d.id}/download/`, d.file_name)}><Download size={16} /></button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card title="Payments">
            {m.payments.rows.length === 0 ? <p className="fb-muted">No payments have been recorded for this matter.</p> : (
              <>
                <p className="adv-pay-total">Total recorded: {m.payments.totals.map((t) => `${t.currency} ${Number(t.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}`).join(' · ')}</p>
                <div className="d-table-wrap">
                  <table className="d-table">
                    <thead><tr><th>Date</th><th>For</th><th>Amount</th><th>Method</th><th>Reference</th><th>Recorded by</th></tr></thead>
                    <tbody>
                      {m.payments.rows.map((p) => (
                        <tr key={p.id}>
                          <td className="nowrap">{fmt(p.paid_on)}</td>
                          <td>{p.purpose}{p.note && <small className="adv-pay-note">{p.note}</small>}</td>
                          <td className="nowrap"><strong>{p.currency} {Number(p.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}</strong></td>
                          <td>{p.method}</td><td>{p.reference || '—'}</td><td>{p.recorded_by}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </Card>

          <Card title="History">
            {m.history.length === 0 ? <p className="fb-muted">No changes yet.</p> : (
              <ul className="d-timeline">
                {[...m.history].reverse().map((h, i) => <li key={i}><span>{fmtTime(h.at)} · {h.by}</span>{h.note || `${h.from || 'new'} → ${h.to}`}</li>)}
              </ul>
            )}
          </Card>

          {closing && <CloseDialog matter={m} onClose={() => setClosing(false)} onDone={() => { setClosing(false); load() }} />}
          {deleting && <DeleteDialog matter={m} onClose={() => setDeleting(false)} />}
          {assigning && <AssignDialog matter={m} onClose={() => setAssigning(false)} onDone={() => { setAssigning(false); load() }} />}
        </>
      )}
    </>
  )
}

function AssignDialog({ matter, onClose, onDone }) {
  const [advocates, setAdvocates] = useState(null)
  const [firm, setFirm] = useState('')
  const [pick, setPick] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { api('admin/advocates/').then(setAdvocates).catch((e) => { setError(e.message); setAdvocates([]) }) }, [])

  const submit = async () => {
    setBusy(true)
    setError('')
    try { await api(`admin/matters/${matter.id}/assign/`, { method: 'POST', body: { advocate_id: pick } }); onDone() } catch (e) { setError(e.message); setBusy(false) }
  }

  return (
    <Modal title={matter.advocate ? 'Reassign matter' : 'Assign matter'} onClose={onClose}
      footer={<><button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn btn-gold" disabled={!pick || busy} onClick={submit}>{busy ? <Loader2 size={16} className="spin" /> : 'Assign'}</button></>}>
      <p className="d-text">Choose the law firm, then the advocate who takes <strong>{matter.reference}</strong>. They are emailed and see it on their dashboard.</p>
      {!advocates ? <Loader2 className="spin" /> : (
        <div className="d-form">
          <Field label="Law firm">
            <select value={firm} onChange={(e) => { setFirm(e.target.value); setPick('') }}>
              <option value="">Select a law firm</option>
              {[...new Set(advocates.map((a) => a.firm))].map((f) => <option key={f} value={f}>{f} ({advocates.filter((a) => a.firm === f).length} available)</option>)}
            </select>
          </Field>
          <Field label="Advocate">
            <select value={pick} disabled={!firm} onChange={(e) => setPick(e.target.value)}>
              <option value="">{firm ? 'Select an advocate' : 'Choose the law firm first'}</option>
              {advocates.filter((a) => a.firm === firm).map((a) => <option key={a.id} value={a.id}>{a.name} ({a.open_matters} open)</option>)}
            </select>
          </Field>
          {advocates.length === 0 && !error && <p className="d-hint">No advocate can take matters yet. They must be active, verified, with a valid practising certificate, in an approved law firm.</p>}
        </div>
      )}
      {error && <p className="fb-error"><CircleAlert size={16} /> {error}</p>}
    </Modal>
  )
}

function CloseDialog({ matter, onClose, onDone }) {
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const close = async () => {
    setBusy(true)
    setError('')
    try { await api(`admin/matters/${matter.id}/close/`, { method: 'POST', body: { comment } }); onDone() } catch (e) { setError(e.message); setBusy(false) }
  }

  return (
    <Modal title="Close this matter?" onClose={onClose}
      footer={<><button className="btn btn-ghost" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn btn-gold" onClick={close} disabled={busy}>{busy ? <Loader2 size={16} className="spin" /> : 'Close matter'}</button></>}>
      <p className="d-text"><strong>{matter.reference}</strong> will be marked as closed. The advocate has finished the work{matter.ready_to_close_summary ? `: “${matter.ready_to_close_summary}”` : '.'}</p>
      <div className="d-form">
        <Field label="Comment (optional)"><textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Add a comment for the record, if you wish." /></Field>
      </div>
      {error && <p className="fb-error"><CircleAlert size={16} /> {error}</p>}
    </Modal>
  )
}

function DeleteDialog({ matter, onClose }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const remove = async () => {
    setBusy(true)
    setError('')
    try { await api(`admin/matters/${matter.id}/delete/`, { method: 'DELETE' }); navigate('/app/admin/matters') } catch (e) { setError(e.message); setBusy(false) }
  }

  return (
    <Modal title="Delete this matter?" onClose={onClose}
      footer={<><button className="btn btn-ghost" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="btn btn-danger" onClick={remove} disabled={busy}>{busy ? <Loader2 size={16} className="spin" /> : 'Delete permanently'}</button></>}>
      <p className="d-text"><strong>{matter.reference}</strong> will be deleted for good, together with its documents, notes and history. This cannot be undone.</p>
      {error && <p className="fb-error"><CircleAlert size={16} /> {error}</p>}
    </Modal>
  )
}
