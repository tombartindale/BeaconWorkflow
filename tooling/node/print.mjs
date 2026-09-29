// Print an HTML file to PDF with the pinned Chrome (used for recording scripts).
// usage: node print.mjs <in.html> <out.pdf> <footer text>
import { pathToFileURL } from 'node:url'
import puppeteer from 'puppeteer-core'

const [inHtml, outPdf, footer] = process.argv.slice(2)
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH,
  headless: true,
  args: ['--allow-file-access-from-files', '--disable-gpu'],
})
try {
  const page = await browser.newPage()
  await page.goto(pathToFileURL(inHtml).href, { waitUntil: 'load', timeout: 60000 })
  await page.evaluate(() => document.fonts.ready)
  await page.pdf({
    path: outPdf,
    format: 'A4',
    printBackground: true,
    margin: { top: '20mm', bottom: '20mm', left: '22mm', right: '22mm' },
    displayHeaderFooter: true,
    headerTemplate: '<span></span>',
    footerTemplate: `<div style="width:100%;font:9px 'Azo Sans',Helvetica,Arial,sans-serif;color:#777;padding:0 22mm;display:flex;justify-content:space-between">
      <span>${esc(footer)}</span><span>page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>`,
  })
} finally {
  await browser.close()
}
