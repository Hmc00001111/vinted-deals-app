package com.houssem.vinteddeals;

import android.annotation.SuppressLint;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.View;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/**
 * Exécute une action (offre, achat, message…) dans un navigateur intégré connecté au compte Vinted.
 * Le navigateur peut être caché (robot en arrière-plan) ou visible (RobotActivity).
 * Les cookies sont partagés par toute l'application : la connexion faite dans RobotActivity sert ici.
 */
final class RobotEngine {
    interface Listener {
        void onResult(JSONObject action, JSONObject result, boolean finished);
    }

    private static final String TAG = "VintedRobot";
    private static String script;

    final WebView web;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final boolean visible;
    private JSONObject action;
    private Listener listener;
    private boolean busy;
    private final Runnable timeout = () -> finish(result("failed", "Délai dépassé (page trop lente ou bloquée)"), true);

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    RobotEngine(Context ctx, WebView existing) {
        visible = existing != null;
        web = existing != null ? existing : new WebView(ctx.getApplicationContext());
        if (script == null) script = readAsset(ctx, "robot.js");
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        // Navigateur mobile ordinaire (on retire la mention « wv » du WebView)
        s.setUserAgentString(s.getUserAgentString().replace("; wv", ""));
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);
        web.addJavascriptInterface(new Bridge(), "RobotBridge");
        if (!visible) {
            // Navigateur invisible : on lui donne une taille d'écran de téléphone pour que la page se mette en page
            int w = 1080, h = 2340;
            web.measure(View.MeasureSpec.makeMeasureSpec(w, View.MeasureSpec.EXACTLY),
                    View.MeasureSpec.makeMeasureSpec(h, View.MeasureSpec.EXACTLY));
            web.layout(0, 0, w, h);
        }
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String scheme = request.getUrl().getScheme();
                return !("http".equals(scheme) || "https".equals(scheme)); // pas d'ouverture d'autres applis
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                inject();
            }
        });
    }

    boolean isBusy() {
        return busy;
    }

    /** Lance l'action (JSON renvoyé par /api/robot/next, ou {kind:'login_check'|'watch_paid', url}). */
    void run(JSONObject a, Listener l) {
        main.post(() -> {
            action = a;
            listener = l;
            busy = true;
            main.removeCallbacks(timeout);
            main.postDelayed(timeout, "watch_paid".equals(a.optString("kind")) ? 30 * 60_000 : 120_000);
            String url = a.optString("url", "https://www.vinted.fr/");
            web.loadUrl(url);
        });
    }

    /** Affiche simplement une page (connexion, paiement) sans action. */
    void open(String url) {
        main.post(() -> web.loadUrl(url));
    }

    private void inject() {
        if (action == null || script == null) return;
        web.evaluateJavascript("window.__vdRobotRunning=false;" + script, null);
    }

    private static JSONObject result(String status, String detail) {
        JSONObject o = new JSONObject();
        try {
            o.put("status", status);
            o.put("detail", detail);
        } catch (Exception ignored) {
        }
        return o;
    }

    private void finish(JSONObject res, boolean done) {
        JSONObject a = action;
        Listener l = listener;
        if (a == null) return;
        if (done) {
            main.removeCallbacks(timeout);
            action = null;
            busy = false;
        }
        if (l != null) l.onResult(a, res, done);
        long id = a.optLong("id", 0);
        String status = res.optString("status");
        if (id > 0 && !"info".equals(status)) {
            new Thread(() -> {
                try {
                    Api.post("/api/robot/actions/" + id, res.toString());
                } catch (Exception e) {
                    Log.w(TAG, "Compte rendu impossible", e);
                }
            }, "robot-report").start();
        }
        if (res.has("logged_in")) {
            boolean in = res.optBoolean("logged_in");
            new Thread(() -> {
                try {
                    Api.post("/api/robot/executor", "{\"logged_in\":" + in + "}");
                } catch (Exception ignored) {
                }
            }, "robot-login").start();
        }
    }

    private class Bridge {
        @JavascriptInterface
        public String action() {
            JSONObject a = action;
            return a == null ? "{}" : a.toString();
        }

        @JavascriptInterface
        public void report(String json) {
            main.post(() -> {
                try {
                    JSONObject r = new JSONObject(json);
                    String st = r.optString("status");
                    // « checkout » sans paiement auto : le script continue de surveiller la confirmation
                    boolean done = !("checkout".equals(st) && visible) && !"running".equals(st);
                    finish(r, done);
                } catch (Exception e) {
                    Log.w(TAG, "Rapport illisible", e);
                }
            });
        }
    }

    private static String readAsset(Context c, String name) {
        try (InputStream in = c.getAssets().open(name)) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        } catch (Exception e) {
            Log.e(TAG, "robot.js introuvable", e);
            return null;
        }
    }
}
