/* End-to-end browser tests (Playwright library, no test runner needed).
   Run via e2e/run.sh, which starts a server with seeded fictional data. */
const { chromium } = require("playwright");
const fs = require("fs");
const assert = require("assert");

const BASE = process.env.E2E_BASE || "http://127.0.0.1:8765";
const PASSWORD = "Demo-pass-2026!";
const results = [];

async function step(name, fn) {
  const t = Date.now();
  try {
    await fn();
    results.push({ name, ok: true, ms: Date.now() - t });
    console.log(`  ✓ ${name} (${Date.now() - t}ms)`);
  } catch (e) {
    results.push({ name, ok: false, error: String(e && e.message || e) });
    console.log(`  ✗ ${name}\n    ${String(e && e.stack || e).split("\n").slice(0, 4).join("\n    ")}`);
  }
}

function tinyJpeg(path) {
  // 1x1 white JPEG
  const b64 = "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/yQALCAABAAEBAREA/8wABgAQEAX/2gAIAQEAAD8A0s8g/9k=";
  fs.writeFileSync(path, Buffer.from(b64, "base64"));
}

// RFC 6238 TOTP (SHA-1, 6 digits, 30 s) for the two-factor step.
function totp(secretB32, offsetSteps = 0) {
  const crypto = require("crypto");
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secretB32.replace(/\s|=/g, "").toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + offsetSteps));
  const h = crypto.createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1] & 15;
  return String((h.readUInt32BE(o) & 0x7fffffff) % 1000000).padStart(6, "0");
}

async function login(page, email) {
  await page.goto(BASE + "/login");
  await page.fill("#email", email);
  await page.fill("#password", PASSWORD);
  await Promise.all([page.waitForURL(/\/(discover|onboarding|verify)/), page.click("button[type=submit]")]);
}

async function outboxLatest(page, recipient, re) {
  await page.goto(BASE + "/dev/outbox");
  const text = await page.locator("tbody tr", { hasText: recipient }).first().innerText();
  const m = text.match(re);
  assert(m, "nothing found in outbox for " + recipient);
  return m[1];
}

(async () => {
  const browser = await chromium.launch();
  const photo = process.env.E2E_PHOTO || "/tmp/e2e-photo.jpg";
  tinyJpeg(photo);
  const errors = [];
  const watch = (page) => {
    page.on("pageerror", (e) => errors.push(`${page.url()} ${e.message}`));
    page.on("console", (m) => { if (m.type() === "error" && !/status of 4\d\d/.test(m.text())) errors.push(`${page.url()} ${m.text()}`); });
  };

  console.log("HeartSync e2e");
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await ctx.newPage();
  watch(page);
  const email = `e2e${Date.now()}@example.com`;

  await step("landing page renders with CTA", async () => {
    await page.goto(BASE + "/");
    await page.getByRole("link", { name: /create your free profile/i }).waitFor();
  });

  await step("sign up rejects under-18", async () => {
    await page.goto(BASE + "/signup");
    await page.fill("#email", "kid@example.com");
    await page.fill("#password", "Valid-pass-123");
    await page.fill("#dob", "2015-01-01");
    await page.check("input[name=confirm_adult]");
    await page.check("input[name=accept_terms]");
    await page.click("button[type=submit]");
    await page.locator("#err-dob").filter({ hasText: /at least 18/ }).waitFor();
  });

  await step("sign up → verify email → verify phone", async () => {
    await page.fill("#email", email);
    await page.fill("#dob", "1994-03-03");
    await Promise.all([page.waitForURL(/verify-email/), page.click("button[type=submit]")]);
    const link = await outboxLatest(page, email, /(\/verify-email\/confirm\?token=[\w-]+)/);
    await page.goto(BASE + link);
    await page.waitForURL(/verify-phone/);
    await page.fill("#phone", "+91 90000 12345");
    await page.click("#send-form button[type=submit]");
    await page.locator("#verify-form").waitFor({ state: "visible" });
    // Read the dev-outbox in a separate tab so the form state is kept.
    const reader = await ctx.newPage();
    const code = await outboxLatest(reader, "+919000012345", /\b(\d{6})\b/);
    await reader.close();
    await page.fill("#code", code === "111111" ? "222222" : "111111");  // wrong code first
    await page.click("#verify-form button[type=submit]");
    await page.locator("#verify-form [data-form-status]").filter({ hasText: /invalid or has expired/ }).waitFor();
    await page.fill("#code", code);
    await Promise.all([page.waitForURL(/onboarding/), page.click("#verify-form button[type=submit]")]);
  });

  await step("onboarding wizard with photo upload", async () => {
    await page.locator("[data-upload]").setInputFiles(photo);
    await page.locator(".photo-slot img").first().waitFor();
    await page.click("#next-step");
    await page.fill("#f-display_name", "Esha");
    await page.selectOption("#f-gender", "woman");
    await page.check('input[name=interested_in][value=man]');
    await page.selectOption("#f-city", "Uttar Pradesh|Lucknow");
    await page.check('input[name=languages][value=en]');
    await page.click("#next-step");
    await page.fill("#f-headline", "Testing in production-like conditions");
    await page.check('input[name=interests][value=reading]');
    await page.click("#next-step");
    await page.click("#next-step");
    await page.check('input[name=relationship_goals][value=long_term]');
    await page.click("#next-step");
    await Promise.all([page.waitForURL(/discover/), page.click("#finish")]);
  });

  await step("discover shows profile cards and swiping works", async () => {
    await page.locator(".profile-card:not(.behind):not(.skeleton)").first().waitFor();
    const before = await page.locator(".profile-card:not(.behind) .name").first().innerText();
    await page.click('[data-act="pass"]');
    await page.waitForFunction((b) => {
      const el = document.querySelector(".profile-card:not(.behind) .name");
      return !el || el.innerText !== b;
    }, before);
    await page.click('[data-act="like"]');
  });

  await step("search applies basic filters and shows chips", async () => {
    await page.goto(BASE + "/search");
    await page.locator("#result-count").filter({ hasText: /match/ }).waitFor();
    await page.click("#open-filters");
    await page.fill('input[name=age_min]', "25");
    await page.fill('input[name=age_max]', "40");
    await page.click("#apply-filters");
    await page.locator(".filter-chip", { hasText: "Min age" }).waitFor();
  });

  await step("profile edit saves changes", async () => {
    await page.goto(BASE + "/profile");
    await page.fill("#f-headline", "Updated headline from e2e");
    await page.click("#profile-form button[type=submit]");
    await page.locator(".toast", { hasText: "Profile saved" }).waitFor();
  });

  // ---------------------------------------------------------- realtime chat
  const ctxA = await browser.newContext();
  const ctxB = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const a = await ctxA.newPage();
  const b = await ctxB.newPage();
  watch(a); watch(b);

  await step("two matched users chat in realtime", async () => {
    await login(a, "demo@heartsync.test");
    await login(b, "demo2@heartsync.test");
    await a.goto(BASE + "/chat");
    await a.locator("[data-conv]").first().click();
    await a.locator("#msg-input").waitFor();
    await b.goto(BASE + "/chat");
    await b.locator("[data-conv]").first().click();
    await b.locator("#msg-input").waitFor();
    await b.waitForTimeout(800); // allow SSE to connect
    const text = "Realtime hello " + Date.now();
    await a.fill("#msg-input", text);
    await a.press("#msg-input", "Enter");
    await b.locator(".bubble", { hasText: text }).waitFor({ timeout: 8000 });
  });

  await step("typing indicator reaches the other person", async () => {
    await b.fill("#msg-input", "typing...");
    await a.locator("#typing", { hasText: /typing/ }).waitFor({ timeout: 8000 });
  });

  await step("phone number is hidden until shared, then visible", async () => {
    const before = await b.content();
    assert(!/\+91\d{10}/.test(before), "a phone number appears before sharing");
    await a.click("#thread-menu-btn");
    await a.click('[data-menu="phone"]');
    await a.check("#phone-confirm");
    await a.click("#phone-share-ok");
    await b.locator("#reveal-phone").waitFor({ timeout: 8000 });
    await b.click("#reveal-phone");
    await b.locator("#phone-value strong").waitFor();
  });

  await step("mobile layouts have no horizontal overflow", async () => {
    for (const path of ["/discover", "/likes", "/chat", "/profile", "/settings/account", "/billing", "/notifications", "/search"]) {
      await b.goto(BASE + path);
      await b.waitForTimeout(400);
      const overflow = await b.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert(overflow <= 1, `${path} overflows by ${overflow}px`);
    }
  });

  await step("mock checkout upgrades only after verified webhook", async () => {
    await a.goto(BASE + "/billing");
    await a.locator('[data-plan="plus"]').click();
    await a.waitForURL(/mock-checkout/);
    await a.locator('[data-mock="1"]').click();
    await a.waitForURL(/billing\?checkout=success/);
    await a.locator(".status.active").waitFor();
  });

  await step("block from chat removes the conversation", async () => {
    await b.goto(BASE + "/chat");
    await b.locator("[data-conv]").first().click();
    await b.click("#thread-menu-btn");
    await b.click('[data-menu="block"]');
    await b.getByRole("button", { name: "Block" }).last().click();
    await b.waitForURL(/\/chat$/);
    await b.locator("#conv-list").filter({ hasText: /No chats yet/ }).waitFor();
  });

  await step("profile writing help drafts a labelled suggestion", async () => {
    await a.goto(BASE + "/profile");
    await a.click("[data-assist=bio]");
    await a.locator(".assist-draft").waitFor();
    await a.click("[data-use-draft]");
    assert((await a.inputValue("#f-about")).length > 20, "draft not inserted");
  });

  await step("two-factor sign-in: enrol, then sign in with a code", async () => {
    await a.goto(BASE + "/settings/security");
    await a.fill("#mfa-pw", PASSWORD);
    await a.click("#mfa-start button[type=submit]");
    await a.locator("#mfa-setup:not([hidden])").waitFor();
    const secret = (await a.textContent("#mfa-secret")).replace(/\s/g, "");
    await a.fill("#mfa-first", totp(secret));
    await a.click("#mfa-confirm button[type=submit]");
    await a.locator("#mfa-codes:not([hidden])").waitFor();
    const fresh = await (await browser.newContext()).newPage();
    watch(fresh);
    const aEmail = await a.evaluate(() => fetch("/api/me").then((r) => r.json()).then((j) => j.data.email));
    await fresh.goto(BASE + "/login");
    await fresh.fill("#email", aEmail);
    await fresh.fill("#password", PASSWORD);
    await fresh.click("#login-form button[type=submit]");
    await fresh.locator("#mfa-form:not([hidden])").waitFor();
    await fresh.fill("#mfa-code", totp(secret, 1));
    await Promise.all([fresh.waitForURL(/\/discover/), fresh.click("#mfa-form button[type=submit]")]);
    await fresh.close();
  });

  await step("no uncaught browser errors", async () => {
    assert.deepStrictEqual(errors, []);
  });

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} e2e steps passed`);
  fs.writeFileSync(__dirname + "/last-results.json", JSON.stringify(results, null, 2));
  process.exit(failed.length ? 1 : 0);
})();
