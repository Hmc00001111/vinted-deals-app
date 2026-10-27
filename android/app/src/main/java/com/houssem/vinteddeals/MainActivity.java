package com.houssem.vinteddeals;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.net.HttpURLConnection;
import java.net.URL;

/** Affiche l'interface (la même que sur PC) servie en local par le moteur Python. */
public class MainActivity extends Activity {
    private static final String BASE = "http://127.0.0.1:" + MonitorService.PORT + "/";
    private static final String PREF_BATTERY_ASKED = "battery_asked";

    private WebView web;
    private volatile boolean waiting = false;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Notifier.init(this);

        web = new WebView(this);
        web.setBackgroundColor(0xFF0F1115);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setSupportMultipleWindows(false); // les liens target=_blank passent par shouldOverrideUrlLoading
        s.setUserAgentString(s.getUserAgentString() + " VintedDealsApp/1.0");

        web.setWebChromeClient(new WebChromeClient()); // nécessaire pour les confirm() JavaScript
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if ("127.0.0.1".equals(uri.getHost())) return false;
                openExternal(uri); // annonce Vinted : ouvre l'appli Vinted ou le navigateur
                return true;
            }
        });

        showMessage("Démarrage du moteur de surveillance…",
                "La première ouverture peut prendre quelques secondes.");

        MonitorService.start(this);
        askNotificationPermission();
        waitForServer();
    }

    private void openExternal(Uri uri) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (ActivityNotFoundException ignored) {
        }
    }

    private void showMessage(String title, String detail) {
        String html = "<html><head><meta name='viewport' content='width=device-width'></head>"
                + "<body style='background:#0f1115;color:#e8eaf0;font-family:sans-serif;"
                + "display:flex;flex-direction:column;align-items:center;justify-content:center;"
                + "height:90vh;text-align:center;padding:24px'>"
                + "<h2 style='color:#09b1ba'>Vinted Deals</h2><p>" + title + "</p>"
                + "<p style='color:#8b93a7;font-size:13px'>" + detail + "</p></body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    /** Attend que le serveur local réponde, puis charge l'interface. */
    private void waitForServer() {
        if (waiting) return;
        waiting = true;
        new Thread(() -> {
            long deadline = System.currentTimeMillis() + 90_000;
            while (System.currentTimeMillis() < deadline) {
                if (MonitorService.startError != null) {
                    String err = MonitorService.startError.replace("<", "&lt;");
                    runOnUiThread(() -> showMessage("Le moteur n'a pas pu démarrer.", err));
                    waiting = false;
                    return;
                }
                if (serverUp()) {
                    runOnUiThread(() -> web.loadUrl(BASE));
                    waiting = false;
                    return;
                }
                try { Thread.sleep(400); } catch (InterruptedException e) { break; }
            }
            runOnUiThread(() -> showMessage("Le moteur met trop de temps à répondre.",
                    "Ferme l'application et relance-la."));
            waiting = false;
        }, "wait-server").start();
    }

    private static boolean serverUp() {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(BASE + "api/status").openConnection();
            c.setConnectTimeout(800);
            c.setReadTimeout(1500);
            return c.getResponseCode() == 200;
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private void askNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        } else {
            askBatteryExemption();
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        askBatteryExemption();
    }

    /** Demande (une seule fois) que le système n'endorme pas la surveillance. */
    @SuppressLint("BatteryLife")
    private void askBatteryExemption() {
        SharedPreferences p = getSharedPreferences(MonitorService.PREFS, MODE_PRIVATE);
        PowerManager pm = getSystemService(PowerManager.class);
        if (pm.isIgnoringBatteryOptimizations(getPackageName()) || p.getBoolean(PREF_BATTERY_ASKED, false)) {
            return;
        }
        p.edit().putBoolean(PREF_BATTERY_ASKED, true).apply();
        try {
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:" + getPackageName())));
        } catch (ActivityNotFoundException ignored) {
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
        // Si l'interface n'est pas encore chargée (ex. service relancé), on réessaie.
        String url = web.getUrl();
        if (url == null || !url.startsWith(BASE)) waitForServer();
    }

    @Override
    protected void onPause() {
        web.onPause();
        super.onPause();
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            moveTaskToBack(true); // la surveillance continue en arrière-plan
        }
    }
}
