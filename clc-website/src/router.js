import { useEffect, useState } from 'react'

// Path routing (clc.tz/privacy-policy, not clc.tz/#/privacy-policy).
// Every route change fires `popstate`, so pages only need to listen to that one event.

export function navigate(path, { replace = false, section = null } = {}) {
  if (path === window.location.pathname && !section) return
  window.history[replace ? 'replaceState' : 'pushState']({ section }, '', path)
  window.dispatchEvent(new PopStateEvent('popstate', { state: { section } }))
}

export function usePath() {
  const [path, setPath] = useState(window.location.pathname)
  useEffect(() => {
    const on = () => setPath(window.location.pathname)
    window.addEventListener('popstate', on)
    return () => window.removeEventListener('popstate', on)
  }, [])
  return path
}

const isFile = (path) => /\.[a-z0-9]+$/i.test(path) || path.startsWith('/api/')

export function installRouter() {
  // Old links (emails, bookmarks) used clc.tz/#/login — move them to clc.tz/login.
  // A section anchor (clc.tz/#services) becomes a scroll target with a clean address.
  const { hash, pathname, search } = window.location
  if (hash.startsWith('#/')) window.history.replaceState(null, '', hash.slice(1))
  else if (hash.length > 1) window.history.replaceState({ section: hash.slice(1) }, '', pathname + search)

  // Listening on window runs after document-level handlers (e.g. the #book pop-up), which can preventDefault first.
  window.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    const a = e.target.closest?.('a[href]')
    if (!a || (a.target && a.target !== '_self') || a.hasAttribute('download')) return
    const href = a.getAttribute('href')
    if (href.startsWith('#') && href.length > 1) {
      // Section links (#services, #contact) scroll on the home page and keep the address clean.
      e.preventDefault()
      const section = href.slice(1)
      if (window.location.pathname === '/') document.getElementById(section)?.scrollIntoView()
      else navigate('/', { section })
      return
    }
    const url = new URL(a.href, window.location.href)
    if (url.origin !== window.location.origin || isFile(url.pathname)) return
    e.preventDefault()
    navigate(url.pathname + url.search)
  })
}
