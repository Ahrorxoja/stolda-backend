/**
 * Agent qo'llanmasini PDF qiladi (Playwright — frontend repodagi).
 * Ishlatish (stolda-backend ichidan):
 *   node agents/guide/render.mjs ../stolda
 * Matnni `index.html` da o'zgartiring, keyin shu buyruqni ishga tushiring.
 */
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dir = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(process.argv[2] ?? "../stolda");
const { chromium } = createRequire(path.join(frontend, "package.json"))("playwright");

const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto(`file://${dir}/index.html`, { waitUntil: "networkidle" });
await page.evaluate(() => document.fonts.ready);
await page.pdf({
  path: path.join(dir, "stolda-agent-qollanma.pdf"),
  format: "A4",
  printBackground: true,
  preferCSSPageSize: true,
  displayHeaderFooter: true,
  headerTemplate: "<span></span>",
  footerTemplate:
    '<div style="width:100%;font-family:Rubik,sans-serif;font-size:8px;color:#A2968B;padding:0 15mm;display:flex;justify-content:space-between"><span>stolda.uz · Agent qo\'llanmasi</span><span class="pageNumber"></span></div>',
});
await browser.close();
console.log("✓ agents/guide/stolda-agent-qollanma.pdf");
