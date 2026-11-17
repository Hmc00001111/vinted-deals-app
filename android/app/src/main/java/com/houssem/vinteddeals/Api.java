package com.houssem.vinteddeals;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Appels au moteur Python local (http://127.0.0.1). À utiliser hors du fil principal. */
final class Api {
    private Api() {}

    static String base() {
        return "http://127.0.0.1:" + MonitorService.PORT;
    }

    static String get(String path) throws Exception {
        return request("GET", path, null);
    }

    static String post(String path, String json) throws Exception {
        return request("POST", path, json);
    }

    private static String request(String method, String path, String body) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(base() + path).openConnection();
        try {
            c.setRequestMethod(method);
            c.setConnectTimeout(3000);
            c.setReadTimeout(15000);
            if (body != null) {
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                try (OutputStream o = c.getOutputStream()) {
                    o.write(body.getBytes(StandardCharsets.UTF_8));
                }
            }
            InputStream in = c.getResponseCode() < 400 ? c.getInputStream() : c.getErrorStream();
            if (in == null) return "";
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        } finally {
            c.disconnect();
        }
    }
}
