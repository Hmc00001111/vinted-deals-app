package com.houssem.vinteddeals;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import android.widget.Toast;

import org.json.JSONObject;

/**
 * Navigateur Vinted visible : connexion à ton compte, exécution d'une action sous tes yeux,
 * et page de paiement à valider toi-même.
 */
public class RobotActivity extends Activity {
    static final String EXTRA_URL = "url";
    static final String EXTRA_ACTION = "action";

    private WebView web;
    private RobotEngine engine;

    static void open(Context c, String url, String actionJson) {
        Intent i = new Intent(c, RobotActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if (url != null) i.putExtra(EXTRA_URL, url);
        if (actionJson != null) i.putExtra(EXTRA_ACTION, actionJson);
        c.startActivity(i);
    }

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        web = new WebView(this);
        setContentView(web);
        web.setWebChromeClient(new WebChromeClient());
        engine = new RobotEngine(this, web);
        handle(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handle(intent);
    }

    private void handle(Intent i) {
        String act = i.getStringExtra(EXTRA_ACTION);
        String url = i.getStringExtra(EXTRA_URL);
        if (act != null) {
            try {
                JSONObject a = new JSONObject(act);
                if (url != null && !a.has("url")) a.put("url", url);
                setTitle("Robot : " + a.optString("kind"));
                engine.run(a, (action, res, done) -> runOnUiThread(() -> {
                    String d = res.optString("detail");
                    if (!d.isEmpty()) Toast.makeText(this, d, Toast.LENGTH_LONG).show();
                }));
                return;
            } catch (Exception ignored) {
            }
        }
        setTitle("Vinted");
        engine.open(url != null ? url : "https://www.vinted.fr/");
    }

    @Override
    protected void onPause() {
        CookieManager.getInstance().flush(); // garde la connexion
        super.onPause();
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else {
            MonitorService.loginCheckSoon = true; // à la sortie, on vérifie si on est bien connecté
            finish();
        }
    }

    static JSONObject loginCheck() {
        JSONObject a = new JSONObject();
        try {
            a.put("kind", "login_check");
            a.put("url", "https://www.vinted.fr/");
        } catch (Exception ignored) {
        }
        return a;
    }
}
