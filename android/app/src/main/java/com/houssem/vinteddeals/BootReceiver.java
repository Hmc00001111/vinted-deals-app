package com.houssem.vinteddeals;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Relance la surveillance après un redémarrage du téléphone (si elle tournait). */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) return;
        boolean wasRunning = context.getSharedPreferences(MonitorService.PREFS, Context.MODE_PRIVATE)
                .getBoolean(MonitorService.PREF_RUNNING, false);
        if (wasRunning) {
            try {
                MonitorService.start(context);
            } catch (Exception ignored) {
                // Démarrage refusé par le système : l'utilisateur relancera l'appli.
            }
        }
    }
}
