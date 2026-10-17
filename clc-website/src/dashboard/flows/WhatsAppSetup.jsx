import { useEffect, useState } from 'react'
import { Check, CircleAlert, Copy, Loader2, Pencil, Plug, Plus, Trash2 } from 'lucide-react'
import { api } from '../api.js'
import { Badge, Field, Modal } from '../shared.jsx'

/* WhatsApp (Meta Cloud API) connections: a table of the ones added, choose which one the bot uses, add or edit. */

const BLANK = { name: '', phone_number_id: '', business_account_id: '', api_version: '', access_token: '', app_secret: '', verify_token: '' }

export function WhatsAppSetup() {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [form, setForm] = useState(null) // null = closed; { id?, ...fields }
  const [formError, setFormError] = useState('')
  const [saving, setSaving] = useState(false)
  const [results, setResults] = useState({}) // test result per connection id
  const [copied, setCopied] = useState(false)
  const [removing, setRemoving] = useState(null)

  const load = () => api('chatbot/whatsapp/').then(setData).catch((e) => setError(e.message))
  useEffect(() => { load() }, [])

  const run = async (key, fn) => {
    setBusy(key)
    setError('')
    try { setData(await fn()) } catch (e) { setError(e.message) } finally { setBusy('') }
  }
  const use = (c) => run(`use-${c.id}`, () => api(`chatbot/whatsapp/${c.id}/use/`, { method: 'POST' }))
  const useEnv = () => run('use-env', () => api('chatbot/whatsapp/use-env/', { method: 'POST' }))
  const remove = async () => {
    const c = removing
    setRemoving(null)
    await run(`del-${c.id}`, () => api(`chatbot/whatsapp/${c.id}/`, { method: 'DELETE' }))
  }
  const test = async (key, path) => {
    setResults((r) => ({ ...r, [key]: 'loading' }))
    try { const res = await api(path, { method: 'POST' }); setResults((r) => ({ ...r, [key]: res })) } catch (e) { setResults((r) => ({ ...r, [key]: { ok: false, detail: e.message } })) }
  }

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })
  const save = async (e) => {
    e.preventDefault()
    setSaving(true)
    setFormError('')
    try {
      const { id, ...body } = form
      setData(await api(id ? `chatbot/whatsapp/${id}/` : 'chatbot/whatsapp/', { method: id ? 'PATCH' : 'POST', body: id ? body : { ...body, use_now: true } }))
      setForm(null)
    } catch (err) {
      setFormError(err.message)
    } finally {
      setSaving(false)
    }
  }
  const copy = () => { navigator.clipboard?.writeText(data.webhook_url); setCopied(true); setTimeout(() => setCopied(false), 1500) }

  if (!data) return error ? <p className="fb-error"><CircleAlert size={16} /> {error}</p> : <div className="fb-center-inline"><Loader2 className="spin" /></div>

  const Result = ({ k }) => {
    const r = results[k]
    if (!r) return null
    if (r === 'loading') return <small className="d-hint"><Loader2 size={12} className="spin" /> Testing…</small>
    return r.ok
      ? <small className="wa-result ok"><Check size={13} /> {r.display_phone_number}{r.verified_name ? ` · ${r.verified_name}` : ''}</small>
      : <small className="wa-result bad"><CircleAlert size={13} /> {r.detail}</small>
  }

  return (
    <>
      <div className="wa-bar">
        <p className="d-hint">Add every WhatsApp number you have, then choose the one the bot uses. Only one is in use at a time.</p>
        <button className="btn btn-gold d-sm" onClick={() => { setFormError(''); setForm({ ...BLANK }) }}><Plus size={15} /> Add connection</button>
      </div>
      {error && <p className="fb-error"><CircleAlert size={16} /> {error}</p>}

      <div className="d-table-wrap">
        <table className="d-table wa-table">
          <thead><tr><th>S/N</th><th>Name</th><th>Phone number ID</th><th>Status</th><th>Action</th></tr></thead>
          <tbody>
            {data.env.available && (
              <tr>
                <td>—</td>
                <td><strong>.env file</strong><small>The values set on the server</small></td>
                <td>{data.env.phone_number_id}</td>
                <td>{data.env.in_use ? <Badge tone="green">In use</Badge> : <Badge>Not in use</Badge>}<Result k="env" /></td>
                <td>
                  <div className="d-actions">
                    {!data.env.in_use && <button className="btn btn-outline-navy d-sm" disabled={!!busy} onClick={useEnv}>{busy === 'use-env' ? <Loader2 size={14} className="spin" /> : 'Use this'}</button>}
                    {data.env.in_use && <button className="d-act" data-tip="Test this connection." onClick={() => test('env', 'chatbot/whatsapp/test/')} aria-label="Test the connection"><Plug size={16} /></button>}
                  </div>
                </td>
              </tr>
            )}
            {data.connections.map((c, i) => (
              <tr key={c.id}>
                <td>{i + 1}</td>
                <td><strong>{c.name}</strong><small>Token {c.token_hint || 'not set'}{c.api_version ? ` · ${c.api_version}` : ''}</small></td>
                <td>{c.phone_number_id}</td>
                <td>{c.in_use ? <Badge tone="green">In use</Badge> : <Badge>Not in use</Badge>}<Result k={c.id} /></td>
                <td>
                  <div className="d-actions">
                    {!c.in_use && <button className="btn btn-outline-navy d-sm" disabled={!!busy} onClick={() => use(c)}>{busy === `use-${c.id}` ? <Loader2 size={14} className="spin" /> : 'Use this'}</button>}
                    <button className="d-act" data-tip="Test this connection with Meta." onClick={() => test(c.id, `chatbot/whatsapp/${c.id}/test/`)} aria-label={`Test ${c.name}`}><Plug size={16} /></button>
                    <button className="d-act" data-tip="Edit this connection." onClick={() => { setFormError(''); setForm({ ...BLANK, id: c.id, name: c.name, phone_number_id: c.phone_number_id, business_account_id: c.business_account_id, api_version: c.api_version }) }} aria-label={`Edit ${c.name}`}><Pencil size={16} /></button>
                    {!c.in_use && <button className="d-act is-danger" data-tip="Delete this connection." onClick={() => setRemoving(c)} aria-label={`Delete ${c.name}`}><Trash2 size={16} /></button>}
                  </div>
                </td>
              </tr>
            ))}
            {!data.env.available && data.connections.length === 0 && <tr><td colSpan={5} className="fb-muted">No connection yet. Add the first one.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="wa-hook">
        <strong>Webhook address (register it in Meta)</strong>
        <div className="wa-url"><code>{data.webhook_url}</code><button type="button" className="d-icon-btn" onClick={copy} aria-label="Copy the address">{copied ? <Check size={16} /> : <Copy size={16} />}</button></div>
        <small className="d-hint">Meta needs a public https address (use ngrok while testing). In Meta: WhatsApp → Configuration → Webhook: paste this address and the verify token of your connection, then subscribe to <b>messages</b>.</small>
      </div>

      {form && (
        <Modal title={form.id ? 'Edit connection' : 'Add a WhatsApp connection'} onClose={() => setForm(null)}
          footer={<><button type="button" className="btn btn-ghost" onClick={() => setForm(null)}>Cancel</button>
            <button form="wa-form" className="btn btn-gold" disabled={saving}>{saving ? <Loader2 size={16} className="spin" /> : 'Save connection'}</button></>}>
          <form id="wa-form" className="d-form" onSubmit={save}>
            <Field label="Name"><input required autoFocus value={form.name} onChange={set('name')} placeholder="e.g. CLC main number" /></Field>
            <Field label="Phone number ID"><input required inputMode="numeric" value={form.phone_number_id} onChange={set('phone_number_id')} placeholder="e.g. 1283801101493291" /></Field>
            <Field label={`Access token${form.id ? ' (leave empty to keep the saved one)' : ''}`}>
              <input type="password" autoComplete="off" required={!form.id} value={form.access_token} onChange={set('access_token')} />
              <small className="d-hint">Use the permanent token of a System User; a temporary token stops working after about 24 hours.</small>
            </Field>
            <Field label={`App secret${form.id ? ' (leave empty to keep)' : ''}`}>
              <input type="password" autoComplete="off" value={form.app_secret} onChange={set('app_secret')} />
              <small className="d-hint">App settings → Basic. Used to check that incoming messages really come from Meta.</small>
            </Field>
            <Field label={`Verify token${form.id ? ' (leave empty to keep)' : ''}`}>
              <input type="password" autoComplete="off" value={form.verify_token} onChange={set('verify_token')} />
              <small className="d-hint">Any word you choose; type the same one in Meta when you register the webhook.</small>
            </Field>
            <Field label="WhatsApp Business account ID (optional)"><input inputMode="numeric" value={form.business_account_id} onChange={set('business_account_id')} /></Field>
            <Field label="Graph API version (optional)"><input value={form.api_version} onChange={set('api_version')} placeholder="v23.0" /></Field>
            {formError && <p className="fb-error"><CircleAlert size={16} /> {formError}</p>}
            {!form.id && <p className="d-hint">If no other connection is in use, this one starts being used straight away.</p>}
          </form>
        </Modal>
      )}

      {removing && (
        <Modal title="Delete this connection?" onClose={() => setRemoving(null)}
          footer={<><button className="btn btn-ghost" onClick={() => setRemoving(null)}>Keep it</button><button className="btn btn-danger" onClick={remove}>Delete</button></>}>
          <p className="d-text"><strong>{removing.name}</strong> and its saved token will be removed. You can add it again later.</p>
        </Modal>
      )}
    </>
  )
}
