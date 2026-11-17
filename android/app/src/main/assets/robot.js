/* Robot Vinted Deals : exécuté dans le navigateur intégré, sur ton compte Vinted (connecté par toi).
   Il clique sur les boutons comme tu le ferais. Si Vinted change son site, il s'arrête et envoie
   au journal la liste des boutons visibles pour qu'on corrige. */
(function () {
  if (window.__vdRobotRunning) return;
  window.__vdRobotRunning = true;
  var A;
  try { A = JSON.parse(RobotBridge.action()); } catch (e) { return; }
  if (!A || !A.kind) return;

  function report(status, detail, extra) {
    var o = extra || {};
    o.status = status; o.detail = detail || "";
    try { RobotBridge.report(JSON.stringify(o)); } catch (e) {}
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function norm(t) { return (t || "").replace(/\s+/g, " ").trim().toLowerCase(); }
  function vis(el) {
    if (!el || el.disabled) return false;
    var r = el.getBoundingClientRect();
    var st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
  }
  function label(b) { return norm(b.innerText || b.value || b.getAttribute("aria-label") || b.getAttribute("title")); }
  function buttons(root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(
      'button, a, [role=button], input[type=submit]')).filter(vis);
  }
  function findBtn(re, root) {
    var list = buttons(root);
    for (var i = 0; i < list.length; i++) if (re.test(label(list[i]))) return list[i];
    return null;
  }
  function dialog() {
    var d = Array.prototype.slice.call(document.querySelectorAll('[role=dialog], [aria-modal=true], dialog[open]')).filter(vis);
    return d.length ? d[d.length - 1] : null;
  }
  async function waitFor(fn, ms) {
    var t = Date.now();
    while (Date.now() - t < (ms || 15000)) {
      var r; try { r = fn(); } catch (e) { r = null; }
      if (r) return r;
      await sleep(300);
    }
    return null;
  }
  function pageText() { return norm(document.body ? document.body.innerText : ""); }
  function debug() {
    return { url: location.href, title: document.title,
      buttons: buttons().map(label).filter(Boolean).slice(0, 50), text: pageText().slice(0, 800) };
  }
  function setValue(el, v) {
    var proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, String(v));
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }
  function loggedIn() {
    return !findBtn(/^(s'inscrire|se connecter|s'inscrire \| se connecter|inscris-toi|connecte-toi|sign up|log in|sign up \| log in)$/);
  }
  function onCheckout() {
    return /checkout|transaction|payment|paiement/.test(location.href) ||
      !!findBtn(/^(payer|pay|payer maintenant|confirmer et payer|confirm and pay|pay now)/);
  }
  var PAID_RE = /(merci pour ta commande|merci pour votre commande|commande confirm|paiement (réussi|effectué|accepté)|payment (successful|complete)|thank you for your (order|purchase)|achat effectué|tu as acheté)/;

  async function watchPaid(minutes) {
    var done = await waitFor(function () { return PAID_RE.test(pageText()); }, minutes * 60000);
    if (done) report("paid", "Paiement effectué");
  }

  async function checkout() {
    await sleep(1500);
    if (!A.pay) {
      report("checkout", "Page de paiement prête : vérifie l'article et valide le paiement", { checkout_url: location.href });
      return watchPaid(15);
    }
    var p = await waitFor(function () {
      return findBtn(/^(payer|pay|payer maintenant|confirmer et payer|confirm and pay|pay now)/);
    }, 15000);
    if (!p) return report("checkout", "Bouton « Payer » introuvable : valide toi-même", { checkout_url: location.href, debug: debug() });
    p.click();
    var ok = await waitFor(function () { return PAID_RE.test(pageText()); }, 45000);
    if (ok) report("paid", "Paiement effectué par le robot");
    else report("checkout", "Paiement envoyé mais confirmation non détectée : vérifie", { checkout_url: location.href, debug: debug() });
  }

  async function buy() {
    if (onCheckout()) return checkout();
    var b = await waitFor(function () { return findBtn(/^(acheter|buy now|acheter maintenant|buy)$/); }, 15000);
    if (!b) {
      if (/(vendu|réservé|sold|reserved)/.test(norm((document.querySelector("main") || document.body).innerText).slice(0, 3000)))
        return report("refused", "Article déjà vendu ou réservé", { debug: debug() });
      return report("failed", "Bouton « Acheter » introuvable", { debug: debug() });
    }
    b.click();
    var ok = await waitFor(onCheckout, 20000);
    if (!ok) return report("failed", "La page de paiement ne s'est pas ouverte", { debug: debug() });
    return checkout();
  }

  async function sendMessage(text) {
    var box = await waitFor(function () {
      var t = Array.prototype.slice.call(document.querySelectorAll("textarea, [contenteditable=true]")).filter(vis);
      return t[t.length - 1];
    }, 10000);
    if (!box) return false;
    if (box.tagName === "TEXTAREA") setValue(box, text);
    else { box.focus(); document.execCommand("insertText", false, text); }
    await sleep(500);
    var s = findBtn(/^(envoyer|send)$/) || document.querySelector('button[type=submit]');
    if (!s) return false;
    s.click();
    await sleep(1500);
    return true;
  }

  async function offer() {
    var b = await waitFor(function () { return findBtn(/^(faire une offre|make an offer|proposer un prix|faire une proposition)$/); }, 15000);
    if (!b) return report("failed", "Bouton « Faire une offre » introuvable (vendeur sans offres, article vendu ?)", { debug: debug() });
    b.click();
    var input = await waitFor(function () {
      var root = dialog() || document;
      var ins = Array.prototype.slice.call(root.querySelectorAll("input")).filter(vis);
      return ins.find(function (i) {
        var k = (i.name || "") + " " + (i.id || "") + " " + (i.placeholder || "") + " " + (i.getAttribute("aria-label") || "");
        return /price|prix|offer|offre|amount|montant/i.test(k) || i.inputMode === "decimal" || i.type === "number";
      }) || ins[0];
    }, 10000);
    if (!input) return report("failed", "Champ du prix de l'offre introuvable", { debug: debug() });
    input.focus();
    setValue(input, String(A.amount).replace(".", ","));
    await sleep(700);
    var root = dialog() || document;
    var send = findBtn(/^(proposer|envoyer|envoyer l'offre|faire une offre|faire l'offre|offer|send|make offer|send offer)/, root);
    if (!send || send === b) send = findBtn(/^(proposer|envoyer|envoyer l'offre|send offer|send)/);
    if (!send) return report("failed", "Bouton d'envoi de l'offre introuvable", { debug: debug() });
    send.click();
    var done = await waitFor(function () {
      return !vis(input) || /(offre envoyée|offer sent|tu as proposé|vous avez proposé|offre de)/.test(pageText());
    }, 12000);
    if (!done) return report("failed", "Envoi de l'offre non confirmé", { debug: debug() });
    if (A.message) await sendMessage(A.message);
    report("sent", "Offre de " + A.amount + " € envoyée");
  }

  async function message() {
    var b = await waitFor(function () { return findBtn(/^(envoyer un message|message|contacter|poser une question|ask seller|message seller)$/); }, 15000);
    if (!b) return report("failed", "Bouton « Message » introuvable", { debug: debug() });
    b.click();
    var ok = await sendMessage(A.message || "");
    report(ok ? "sent" : "failed", ok ? "Message envoyé" : "Envoi du message impossible", ok ? {} : { debug: debug() });
  }

  async function run() {
    await waitFor(function () { return document.readyState === "complete"; }, 20000);
    await sleep(1500);
    if (A.kind === "login_check") return report("info", "", { logged_in: loggedIn() });
    if (A.kind === "watch_paid") return watchPaid(30);
    if (!loggedIn()) return report("failed", "Pas connecté à Vinted : onglet Robot → « Connexion Vinted »", { logged_in: false, debug: debug() });
    if (A.kind === "buy") return buy();
    if (A.kind === "offer") return offer();
    if (A.kind === "message") return message();
    report("failed", "Action inconnue : " + A.kind);
  }
  run().catch(function (e) { report("failed", "Erreur du robot : " + e, { debug: debug() }); });
})();
