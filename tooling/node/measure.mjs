// Overflow and safe-area measurement for bcn render.
//
// Renders the same markdown Marp sees, with the same pinned marp-core and the
// same pinned Chrome, then measures every block element on every slide against
// the section's content box and against the subtitle safe area declared in
// theme.toml. Marp never reports overflow itself; it just clips.
//
// usage: node measure.mjs <deck.md> <theme.css> <width> <height> <safeBottom> <out.html>
// stdout: {"slides": N, "issues": [{slide, kind, element, text, bottom, limit, right}]}

import { readFileSync, writeFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { Marp } from '@marp-team/marp-core'
import puppeteer from 'puppeteer-core'

const [mdPath, cssPath, w, h, safe, outHtml] = process.argv.slice(2)
const width = Number(w), height = Number(h), safeBottom = Number(safe)

const marp = new Marp({ inlineSVG: false, html: false, math: false })
marp.themeSet.add(readFileSync(cssPath, 'utf8'))
const { html, css } = marp.render(readFileSync(mdPath, 'utf8'))

const page = `<!doctype html><html><head><meta charset="utf-8"><base href="${pathToFileURL(mdPath).href}">
<style>${css}
body{margin:0;background:#000}
div.marpit > section{margin:0 0 8px 0}
</style></head><body>${html}</body></html>`
writeFileSync(outHtml, page)

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH,
  headless: true,
  args: ['--allow-file-access-from-files', '--disable-gpu', '--font-render-hinting=none'],
})
try {
  const tab = await browser.newPage()
  await tab.setViewport({ width, height, deviceScaleFactor: 1 })
  await tab.goto(pathToFileURL(outHtml).href, { waitUntil: 'load', timeout: 60000 })
  await tab.evaluate(() => document.fonts.ready)
  const result = await tab.evaluate((safeBottom) => {
    const sections = [...document.querySelectorAll('div.marpit > section')]
    const issues = []
    const BLOCKS = 'h1,h2,h3,h4,h5,h6,p,li,img,table,pre,blockquote,figure'
    sections.forEach((sec, i) => {
      const sr = sec.getBoundingClientRect()
      const cs = getComputedStyle(sec)
      const contentBottom = sr.top + sr.height - parseFloat(cs.paddingBottom)
      const contentRight = sr.left + sr.width - parseFloat(cs.paddingRight)
      const safeTop = sr.top + sr.height - safeBottom
      const seen = new Set()
      for (const el of sec.querySelectorAll(BLOCKS)) {
        // Report the outermost offending block only, not every nested child.
        if ([...seen].some((s) => s.contains(el))) continue
        const r = el.getBoundingClientRect()
        if (r.height === 0 && r.width === 0) continue
        const text = (el.innerText || el.getAttribute('alt') || '').trim().slice(0, 80)
        const tag = el.tagName.toLowerCase()
        const bottom = Math.round(r.bottom - sr.top)
        if (r.bottom > safeTop + 1) {
          issues.push({ slide: i + 1, kind: 'safe_area', element: tag, text, bottom, limit: Math.round(safeTop - sr.top) })
          seen.add(el)
        } else if (r.bottom > contentBottom + 1) {
          issues.push({ slide: i + 1, kind: 'overflow', element: tag, text, bottom, limit: Math.round(contentBottom - sr.top) })
          seen.add(el)
        } else if (r.right > contentRight + 1 || el.scrollWidth > el.clientWidth + 1) {
          issues.push({ slide: i + 1, kind: 'overflow', element: tag, text, right: Math.round(r.right - sr.left), limit: Math.round(contentRight - sr.left) })
          seen.add(el)
        }
      }
    })
    return { slides: sections.length, issues }
  }, safeBottom)
  process.stdout.write(JSON.stringify(result) + '\n')
} finally {
  await browser.close()
}
