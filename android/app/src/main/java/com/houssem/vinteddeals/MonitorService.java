package com.houssem.vinteddeals;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import android.os.Handler;
import android.os.Looper;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

import org.json.JSONObject;

/**
 * Service au premier plan : démarre le moteur Python (le même code que sur PC :
 * surveillance Vinted + petit serveur web local) et le garde actif en arrière-plan.
 */
public class MonitorService extends Service {
    public static final int PORT = 5137;
    static final String ACTION_STOP = "com.houssem.vinteddeals.STOP";
    static final String PREFS = "vinted_deals";
    static final String PREF_RUNNING = "running";
    private static final String TAG = "VintedDeals";

    static volatile boolean pythonStarted = false;
    static volatile String startError = null;

    private PowerManager.WakeLock wakeLock;

    // ---------- robot en arrière-plan ----------
    static volatile boolean loginCheckSoon = true;
    private static volatile boolean robotStarted = false;
    private RobotEngine robot;
    private long lastLoginCheck = 0;

    static void start(Context c) {
        c.getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean(PREF_RUNNING, true).apply();
        c.startForegroundService(new Intent(c, MonitorService.class));
    }

    @Override
    public void onCreate() {
        super.onCreate();
        Notifier.init(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean(PREF_RUNNING, false).commit();
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
            // Les fils Python ne s'arrêtent pas proprement : on termine le processus.
            android.os.Process.killProcess(android.os.Process.myPid());
            return START_NOT_STICKY;
        }

        goForeground();

        if (wakeLock == null) {
            PowerManager pm = getSystemService(PowerManager.class);
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "VintedDeals:monitor");
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire();
        }

        synchronized (MonitorService.class) {
            if (!pythonStarted) {
                pythonStarted = true;
                new Thread(this::startPython, "python-start").start();
            }
            if (!robotStarted) {
                robotStarted = true;
                new Thread(this::robotLoop, "robot-poll").start();
            }
        }
        return START_STICKY;
    }

    private void goForeground() {
        PendingIntent openApp = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop = PendingIntent.getService(this, 1,
                new Intent(this, MonitorService.class).setAction(ACTION_STOP),
                PendingIntent.FLAG_IMMUTABLE);

        Notification n = new Notification.Builder(this, Notifier.CHANNEL_SERVICE)
                .setSmallIcon(R.drawable.ic_stat)
                .setColor(0xFF09B1BA)
                .setContentTitle("Vinted Deals surveille les annonces")
                .setContentText("Touchez pour ouvrir l'application")
                .setContentIntent(openApp)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(null, "Arrêter", stop).build())
                .build();

        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(1, n);
        }
    }

    private void startPython() {
        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(getApplicationContext()));
            }
            String html = readAsset("index.html");
            Python.getInstance().getModule("android_main")
                    .callAttr("start", getFilesDir().getAbsolutePath(), html, PORT);
            Log.i(TAG, "Moteur Python démarré");
        } catch (Throwable t) {
            startError = t.toString();
            Log.e(TAG, "Échec du démarrage Python", t);
        }
    }

    /** Demande au moteur les actions du robot (offres, achats) et les exécute dans un navigateur caché. */
    private void robotLoop() {
        Handler main = new Handler(Looper.getMainLooper());
        while (true) {
            try {
                Thread.sleep(12_000);
                if (robot == null) {
                    main.post(() -> {
                        if (robot == null) robot = new RobotEngine(this, null);
                    });
                    continue;
                }
                if (robot.isBusy()) continue;
                long now = System.currentTimeMillis();
                if (loginCheckSoon || now - lastLoginCheck > 30 * 60_000) {
                    loginCheckSoon = false;
                    lastLoginCheck = now;
                    robot.run(RobotActivity.loginCheck(), (a, r, d) -> { });
                    continue;
                }
                String res = Api.get("/api/robot/next?background=1&platform=android&version=" + BuildConfig.VERSION_NAME);
                JSONObject a = new JSONObject(res.isEmpty() ? "{}" : res);
                if (a.optLong("id", 0) > 0) robot.run(a, (act, r, d) -> { });
            } catch (InterruptedException e) {
                return;
            } catch (Exception e) {
                Log.w(TAG, "Robot : " + e);
            }
        }
    }

    private String readAsset(String name) throws IOException {
        try (InputStream in = getAssets().open(name)) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    @Override
    public void onDestroy() {
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
