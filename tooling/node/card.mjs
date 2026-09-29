// Screenshot a title card (bcn bumpers) with the pinned Chrome.
//
// The page may hold one #title element inside a #box with a max-height. The title's
// font size steps down until it fits the box, so a long title wraps and shrinks
// rather than being clipped.
//
// usage: node card.mjs <in.html> <out.png> <width> <height> <maxFontPx> <minFontPx> [transparent]
// stdout: {"font_size": N, "fits": true|false}
import { pathToFileURL } from 'node:url'
import puppeteer from 'puppeteer-core'

const [inHtml, outPng, w, h, maxF, minF, mode] = process.argv.slice(2)

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH,
  headless: true,
  args: ['--allow-file-access-from-files', '--disable-gpu', '--font-render-hinting=none'],
})
try {
  const page = await browser.newPage()
  await page.setViewport({ width: Number(w), height: Number(h), deviceScaleFactor: 1 })
  await page.goto(pathToFileURL(inHtml).href, { waitUntil: 'load', timeout: 60000 })
  const fit = await page.evaluate(async (maxF, minF) => {
    await document.fonts.ready
    await Promise.all([...document.images].map((i) => i.decode().catch(() => {})))
    const title = document.getElementById('title')
    const box = document.getElementById('box')
    if (!title || !box) return { font_size: null, fits: true } // a card with no title, e.g. the logo-only outro
    let size = maxF
    const maxH = parseFloat(getComputedStyle(box).maxHeight)
    const fits = () => title.scrollHeight <= maxH + 1 && title.scrollWidth <= box.clientWidth + 1
    for (; size > minF; size -= 2) {
      title.style.fontSize = `${size}px`
      if (fits()) break
    }
    title.style.fontSize = `${size}px`
    return { font_size: size, fits: fits() }
  }, Number(maxF), Number(minF))
  // 'transparent': keep the alpha channel, so the card can be laid over a background video.
  await page.screenshot({ path: outPng, type: 'png', omitBackground: mode === 'transparent',
    clip: { x: 0, y: 0, width: Number(w), height: Number(h) } })
  process.stdout.write(JSON.stringify(fit) + '\n')
} finally {
  await browser.close()
}
