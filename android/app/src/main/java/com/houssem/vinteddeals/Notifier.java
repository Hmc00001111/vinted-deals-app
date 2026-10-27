package com.houssem.vinteddeals;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.util.Log;

/** Notifications Android. La méthode {@link #deal} est appelée depuis le code Python. */
public final class Notifier {
    static final String CHANNEL_DEALS = "deals";
    static final String CHANNEL_PRIORITY = "deals_priority";
    static final String CHANNEL_SERVICE = "service";
    private static Context app;

    private Notifier() {}

    static synchronized void init(Context context) {
        if (app != null) return;
        app = context.getApplicationContext();
        NotificationManager nm = app.getSystemService(NotificationManager.class);

        NotificationChannel deals = new NotificationChannel(
                CHANNEL_DEALS, "Bonnes affaires", NotificationManager.IMPORTANCE_HIGH);
        deals.setDescription("Une nouvelle annonce intéressante vient d'être publiée");
        deals.enableVibration(true);
        nm.createNotificationChannel(deals);

        NotificationChannel prio = new NotificationChannel(
                CHANNEL_PRIORITY, "Affaires exceptionnelles", NotificationManager.IMPORTANCE_HIGH);
        prio.setDescription("Gemini conseille d'acheter et le score est très élevé");
        prio.enableVibration(true);
        prio.setVibrationPattern(new long[]{0, 400, 150, 400, 150, 700});
        prio.enableLights(true);
        prio.setLightColor(0xFFFF5C5C);
        prio.setBypassDnd(true);
        nm.createNotificationChannel(prio);

        NotificationChannel service = new NotificationChannel(
                CHANNEL_SERVICE, "Surveillance en cours", NotificationManager.IMPORTANCE_LOW);
        service.setDescription("Indique que la surveillance tourne en arrière-plan");
        service.setShowBadge(false);
        nm.createNotificationChannel(service);
    }

    /** Appelée depuis Python (android_main.py) pour chaque bonne affaire. */
    public static void deal(long itemId, String title, String text, String url) {
        deal(itemId, title, text, url, false);
    }

    /** Variante avec alerte prioritaire (vibration longue, passe outre « Ne pas déranger » si autorisé). */
    public static void deal(long itemId, String title, String text, String url, boolean priority) {
        Context c = app;
        if (c == null) return;
        try {
            Intent open = new Intent(Intent.ACTION_VIEW, Uri.parse(url))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            PendingIntent pi = PendingIntent.getActivity(c, (int) (itemId % Integer.MAX_VALUE), open,
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);

            Notification n = new Notification.Builder(c, priority ? CHANNEL_PRIORITY : CHANNEL_DEALS)
                    .setSmallIcon(R.drawable.ic_stat)
                    .setColor(priority ? 0xFFFF5C5C : 0xFF09B1BA)
                    .setContentTitle(title)
                    .setContentText(text)
                    .setStyle(new Notification.BigTextStyle().bigText(text))
                    .setContentIntent(pi)
                    .setAutoCancel(true)
                    .setCategory(Notification.CATEGORY_RECOMMENDATION)
                    .setWhen(System.currentTimeMillis())
                    .setShowWhen(true)
                    .build();
            c.getSystemService(NotificationManager.class)
                    .notify((int) (itemId % Integer.MAX_VALUE), n);
        } catch (Exception e) {
            Log.w("VintedDeals", "Notification impossible", e);
        }
    }
}
