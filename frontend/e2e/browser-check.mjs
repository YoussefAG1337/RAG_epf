// End-to-end check of the running app in a real Chromium-based browser.
//
//   npm run e2e                     (the stack must be running: ./start.sh)
//   BROWSER_PATH=/path/to/chrome BASE_URL=http://localhost:3000 npm run e2e
//
// It asks questions about the example EPF courses (Cryptographie, stat, virtualisation), so
// those must be imported. Screenshots are written to e2e/screenshots/.
import { existsSync, mkdirSync } from "node:fs";
import puppeteer from "puppeteer-core";

const BASE = process.env.BASE_URL ?? "http://localhost:3000";
const OUT = new URL("./screenshots/", import.meta.url).pathname;
mkdirSync(OUT, { recursive: true });
const CANDIDATES = [
  process.env.BROWSER_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  "/Applications/Chromium.app/Contents/MacOS/Chromium",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
];
const executablePath = CANDIDATES.find((path) => path && existsSync(path));
if (!executablePath) {
  console.error("No Chromium-based browser found: set BROWSER_PATH.");
  process.exit(2);
}

const results = [];
const consoleErrors = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
};

const browser = await puppeteer.launch({
  executablePath,
  headless: true,
  args: ["--no-first-run"],
  defaultViewport: { width: 1600, height: 1000 },
});
const page = await browser.newPage();
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(`pageerror: ${error.message}`));
page.on("response", (response) => {
  if (response.status() >= 400) consoleErrors.push(`HTTP ${response.status()} ${response.url()}`);
});

const text = () => page.evaluate(() => document.body.innerText);
const waitText = (needle, timeout = 60000) =>
  page.waitForFunction((n) => document.body.innerText.includes(n), { timeout }, needle);
const waitIdle = (timeout = 120000) =>
  page.waitForFunction(() => !!document.querySelector('button[aria-label="Envoyer"]') && !document.querySelector("[aria-busy=true]"), { timeout });

async function ask(question) {
  await page.click("#question");
  await page.type("#question", question);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => !!document.querySelector('button[aria-label="Arrêter"]') || !!document.querySelector("[aria-busy=true]"), { timeout: 10000 }).catch(() => {});
  await waitIdle();
}

try {
  // 1. Load
  await page.goto(BASE, { waitUntil: "networkidle2" });
  await waitText("Serveur connecté");
  await waitText("Cryptographie");
  check("page loads, server connected, library lists courses", true);
  await page.screenshot({ path: `${OUT}01-accueil.png` });

  // 2. Ask a question
  const started = Date.now();
  await ask("Comment fonctionne le chiffrement RSA ?");
  const firstAnswer = await text();
  check("answer streamed with sources", /sources \(/i.test(firstAnswer), `${((Date.now() - started) / 1000).toFixed(1)} s`);
  const markers = await page.$$eval('button[aria-label^="Source "]', (items) => items.map((item) => item.getAttribute("aria-label")));
  check("claims carry numbered source markers", markers.length > 0, markers.slice(0, 3).join(" | "));
  check("URL keeps the conversation", (await page.url()).includes("?c="));
  await page.screenshot({ path: `${OUT}02-reponse.png` });

  // 3. Open a citation: the PDF opens at the page with highlight boxes
  await page.click('button[aria-label^="Source 1 "]');
  await page.waitForSelector(".react-pdf__Page canvas", { timeout: 60000 });
  await page.waitForFunction(() => document.querySelectorAll(".citation-mark").length > 0, { timeout: 30000 });
  const viewer = await page.evaluate(() => ({
    marks: document.querySelectorAll(".citation-mark").length,
    page: document.querySelector('input[type="number"]')?.value,
    title: document.querySelector('section[aria-label="Document"] h2')?.textContent,
    url: location.search,
  }));
  check("citation opens the PDF with highlighted lines", viewer.marks > 0, `${viewer.title}, page ${viewer.page}, ${viewer.marks} box(es), ${viewer.url}`);
  const layout = await page.evaluate(() => {
    const panel = document.querySelector('section[aria-label="Document"]');
    const column = panel?.parentElement;
    const canvas = document.querySelector(".react-pdf__Page canvas");
    const scroller = canvas?.closest(".overflow-auto");
    return {
      panel: Math.round(panel?.getBoundingClientRect().width ?? 0),
      column: Math.round(column?.getBoundingClientRect().width ?? 0),
      canvas: Math.round(canvas?.getBoundingClientRect().width ?? 0),
      scroller: Math.round(scroller?.getBoundingClientRect().width ?? 0),
    };
  });
  check(
    "document panel fills its column and the page fills the panel",
    layout.panel >= layout.column - 2 && layout.canvas >= layout.scroller - 48,
    `column ${layout.column}px, panel ${layout.panel}px, page area ${layout.scroller}px, page ${layout.canvas}px`,
  );
  await new Promise((resolve) => setTimeout(resolve, 800));
  await page.screenshot({ path: `${OUT}03-citation-pdf.png` });

  // 4. Follow-up question uses the conversation
  await ask("Et pour signer un message ?");
  const followUp = await text();
  const rewritten = followUp.match(/Recherche effectuée : « ([^»]+) »/);
  check("follow-up is understood in context", !!rewritten, rewritten ? rewritten[1] : "no rewritten search shown");
  await page.screenshot({ path: `${OUT}04-suivi.png` });

  // 5. Reload restores the conversation and the open document
  await page.reload({ waitUntil: "networkidle2" });
  await waitText("Et pour signer un message ?");
  check("reload restores the saved conversation", true);

  // 6. Document scope from the library
  await page.click('button[aria-label="Déplier virtualisation"]');
  const qcm = await page.$$('button[title^="virtualisation/"]');
  await qcm[0].click();
  await waitText("Document : ");
  await page.evaluate(() => {
    const newChat = [...document.querySelectorAll("button")].find((button) => button.textContent?.includes("Nouvelle conversation"));
    newChat?.click();
  });
  await ask("À quoi sert la déduplication ?");
  const scoped = await text();
  check("document-scoped question answered from that document", /sources \(/i.test(scoped) && /qcm|virtualisation/i.test(scoped), scoped.match(/Document : [^\n]+/)?.[0] ?? "");
  await page.screenshot({ path: `${OUT}05-document.png` });

  // 7. A Markdown source highlights lines in the text viewer
  await page.evaluate(() => [...document.querySelectorAll("button")].find((button) => button.textContent?.includes("Nouvelle conversation"))?.click());
  await page.click('button[aria-label="Chercher dans tous les cours"]').catch(() => {});
  await ask("Quand faut-il rejeter l'hypothèse nulle ?");
  const cards = await page.$$eval('section[aria-label="Sources"] button', (items) => items.map((item) => item.innerText));
  const mdIndex = cards.findIndex((card) => /Fiche|Synth/i.test(card));
  if (mdIndex >= 0) {
    const buttons = await page.$$('section[aria-label="Sources"] button');
    await buttons[mdIndex].click();
    await page.waitForFunction(() => !!document.querySelector("[data-lines].bg-amber-100"), { timeout: 30000 });
    await new Promise((resolve) => setTimeout(resolve, 1500)); // let the smooth scroll finish
    const visible = await page.evaluate(() => {
      const mark = document.querySelector("[data-lines].bg-amber-100");
      const scroller = mark?.closest(".overflow-auto");
      if (!mark || !scroller) return null;
      const m = mark.getBoundingClientRect();
      const box = scroller.getBoundingClientRect();
      return { visible: m.top >= box.top - 1 && m.top < box.bottom, lines: mark.getAttribute("data-lines"), scrolled: Math.round(scroller.scrollTop) };
    });
    check("Markdown citation highlights the cited block and scrolls it into view", !!visible?.visible, `${cards[mdIndex].split("\n")[0]} lines ${visible?.lines}, scrolled ${visible?.scrolled}px`);
  } else {
    check("Markdown citation highlights the cited block", false, `no Markdown source among: ${cards.map((c) => c.split("\n")[0]).join(" | ")}`);
  }
  await page.screenshot({ path: `${OUT}06-markdown.png` });

  // 8. Off-topic question is refused
  await page.evaluate(() => [...document.querySelectorAll("button")].find((button) => button.textContent?.includes("Nouvelle conversation"))?.click());
  await ask("Qui a gagné la coupe du monde 2018 ?");
  check("off-topic question is refused", (await text()).includes("Je n’ai pas trouvé") || (await text()).includes("Je n'ai pas trouvé"));
} catch (error) {
  check("scenario completed", false, error.message);
  await page.screenshot({ path: `${OUT}99-erreur.png` }).catch(() => {});
} finally {
  const relevantErrors = consoleErrors.filter((line) => !/Download the React DevTools|Failed to load resource/.test(line));
  check("no browser console errors", relevantErrors.length === 0, relevantErrors.slice(0, 5).join(" || "));
  await browser.close();
  const failed = results.filter((result) => !result.ok).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
}
