from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width":1920,"height":1080})
    pg.goto("https://rocketito.com", wait_until="networkidle")
    print("TITLE", pg.title(), "HEIGHT", pg.evaluate("document.body.scrollHeight"))
    for h in pg.eval_on_selector_all("h1,h2,h3", "els=>els.map(e=>[e.tagName,e.innerText.slice(0,80),Math.round(e.getBoundingClientRect().top+scrollY)])")[:30]: print(h)
    print("LINKS", pg.eval_on_selector_all("header a, nav a", "els=>els.map(e=>[e.innerText.trim(),e.href])")[:20])
    pg.screenshot(path="shot_top.png")
    b.close()
