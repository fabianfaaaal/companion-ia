package com.companionia.mobile;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.widget.*;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.X509TrustManager;
import java.security.MessageDigest;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import com.google.zxing.integration.android.IntentIntegrator;
import com.google.zxing.integration.android.IntentResult;

public class ChatActivity extends Activity {
    private EditText message;
    private URL host;
    private String pin, session;
    private TextView transcript;
    private Button send;
    private TextView status;
    private String phase = "Desconectado";
    private static final String[] DOTS = {"", ".", "..", "..."};
    private int dots;
    private boolean checking;
    private boolean busy;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final Runnable animate = new Runnable() {
        @Override public void run() {
            status.setText(phase + (session == null ? "" : DOTS[dots]));
            dots = (dots + 1) % 4;
            ui.postDelayed(this, 400);
        }
    };

    private final Runnable heartbeat = new Runnable() {
        @Override public void run() {
            if (session != null && !busy && !checking) {
                URL address = host;
                String fingerprint = pin;
                String code = session;
                checking = true;
                new Thread(() -> {
                    boolean connected;
                    try {
                        connected = request(address, fingerprint, code, "/status", "")
                                .getBoolean("connected");
                    } catch (Exception error) {
                        connected = false;
                    }
                    boolean reachable = connected;
                    runOnUiThread(() -> {
                        checking = false;
                        if (!isDestroyed() && !busy && code.equals(session) && !reachable) {
                            loseConnection();
                        }
                    });
                }).start();
            }
            ui.postDelayed(this, 5000);
        }
    };

    private void loseConnection() {
        session = null;
        setPhase("Conexión perdida · Volvé a escanear");
        send.setEnabled(false);
    }

    private void setPhase(String value) {
        runOnUiThread(() -> {
            if (isDestroyed()) return;
            phase = value;
            status.setText(value);
        });
    }

    @Override protected void onDestroy() {
        ui.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout safeArea = new LinearLayout(this);
        safeArea.setOrientation(LinearLayout.VERTICAL);
        safeArea.setPadding(dp(16), dp(8), dp(16), dp(8));
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            safeArea.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets edges = insets.getInsets(
                        WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout()
                                | WindowInsets.Type.ime());
                view.setPadding(edges.left + dp(16), edges.top + dp(8),
                        edges.right + dp(16), edges.bottom + dp(8));
                return insets;
            });
        }
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER_HORIZONTAL);
        status = new TextView(this);
        status.setGravity(Gravity.CENTER);
        status.setTextColor(0xff75e4c2);
        status.setTextSize(16);
        status.setText(phase);
        layout.addView(status);
        ImageView robot = new ImageView(this);
        robot.setImageResource(R.drawable.companion_robot);
        robot.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        layout.addView(robot, new LinearLayout.LayoutParams(dp(68), dp(68)));
        Button scan = new Button(this);
        scan.setText("Escanear QR de Companion");
        scan.setOnClickListener(view -> new IntentIntegrator(this)
                .setDesiredBarcodeFormats(IntentIntegrator.QR_CODE).setBeepEnabled(false).initiateScan());
        layout.addView(scan);
        ScrollView scroll = new ScrollView(this);
        transcript = new TextView(this);
        transcript.setTextIsSelectable(true);
        scroll.addView(transcript);
        layout.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        LinearLayout composer = new LinearLayout(this);
        composer.setGravity(Gravity.CENTER_VERTICAL);
        message = new EditText(this);
        message.setHint("Mensaje");
        message.setMaxLines(3);
        composer.addView(message, new LinearLayout.LayoutParams(0, -2, 1));
        send = new Button(this);
        send.setText("Enviar");
        send.setBackgroundTintList(new android.content.res.ColorStateList(
                new int[][]{{-android.R.attr.state_enabled}, {}},
                new int[]{0xff1a2636, 0xff75e4c2}));
        send.setTextColor(new android.content.res.ColorStateList(
                new int[][]{{-android.R.attr.state_enabled}, {}},
                new int[]{0xff9baec4, 0xff10251f}));
        send.setEnabled(false);
        send.setOnClickListener(view -> sendMessage());
        composer.addView(send);
        layout.addView(composer, new LinearLayout.LayoutParams(-1, -2));
        safeArea.addView(new View(this), new LinearLayout.LayoutParams(1, 0, 1));
        safeArea.addView(layout, new LinearLayout.LayoutParams(-1, 0, 4));
        safeArea.addView(new View(this), new LinearLayout.LayoutParams(1, 0, 1));
        safeArea.setFocusableInTouchMode(true);
        setContentView(safeArea);
        safeArea.requestFocus();
        safeArea.requestApplyInsets();
        ui.post(animate);
        ui.postDelayed(heartbeat, 5000);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        IntentResult result = IntentIntegrator.parseActivityResult(requestCode, resultCode, data);
        if (result != null && result.getContents() != null) {
            pair(result.getContents());
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void sendMessage() {
        String text = message.getText().toString().trim();
        URL address = host;
        String code = session;
        String fingerprint = pin;
        if (text.isEmpty() || text.length() > 4000 || code == null) {
            Toast.makeText(this, "Vinculá el teléfono e ingresá un mensaje de hasta 4000 caracteres.", Toast.LENGTH_LONG).show();
            return;
        }
        busy = true;
        send.setEnabled(false);
        setPhase("Enviando datos");
        new Thread(() -> {
            try {
                String answer = request(address, fingerprint, code, "/chat", text).getString("answer");
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    transcript.append("Vos: " + text + "\n\nCompanion: " + answer + "\n\n");
                    if (message.getText().toString().trim().equals(text)) message.setText("");
                    busy = false;
                    setPhase("Conectado · Esperando datos");
                    send.setEnabled(true);
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    Toast.makeText(this, error.getMessage(), Toast.LENGTH_LONG).show();
                    busy = false;
                    if (error instanceof RequestFailure && ((RequestFailure) error).status < 500
                            && ((RequestFailure) error).status != 401) {
                        setPhase("Conectado · Esperando datos");
                        send.setEnabled(true);
                    } else {
                        loseConnection();
                    }
                });
            }
        }).start();
    }

    private void pair(String contents) {
        if (contents.length() > 2048 || busy) return;
        busy = true;
        setPhase("Vinculando");
        send.setEnabled(false);
        new Thread(() -> {
            try {
                JSONObject qr = new JSONObject(contents);
                URL address = new URL(qr.getString("url"));
                String fingerprint = qr.getString("pin");
                if (!address.getProtocol().equals("https") || address.getUserInfo() != null
                        || address.getPort() != 18763
                        || !address.getHost().matches("[0-9.]+")
                        || !fingerprint.matches("[a-f0-9]{64}")) {
                    throw new IllegalArgumentException("QR inválido.");
                }
                String linked = request(address, fingerprint, qr.getString("code"),
                        "/pair", "").getString("session");
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    host = address;
                    pin = fingerprint;
                    session = linked;
                    busy = false;
                    setPhase("Conectado · Esperando datos");
                    transcript.setText("Conectado a Companion.\n\n");
                    send.setEnabled(true);
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    busy = false;
                    setPhase(session == null ? "Desconectado" : "Conectado · Esperando datos");
                    send.setEnabled(session != null);
                    Toast.makeText(this, error.getMessage(), Toast.LENGTH_LONG).show();
                });
            }
        }).start();
    }

    private static final class RequestFailure extends Exception {
        final int status;

        RequestFailure(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    private JSONObject request(URL address, String pin, String code, String path, String text) throws Exception {
        URL endpoint = new URL("https", address.getHost(), address.getPort(), path);
        HttpsURLConnection connection = (HttpsURLConnection) endpoint.openConnection();
        SSLContext tls = SSLContext.getInstance("TLS");
        tls.init(null, new X509TrustManager[]{new X509TrustManager() {
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
            public void checkClientTrusted(X509Certificate[] chain, String kind) throws CertificateException {
                throw new CertificateException("Certificado de cliente inesperado.");
            }
            public void checkServerTrusted(X509Certificate[] chain, String kind) throws CertificateException {
                if (chain == null || chain.length != 1) throw new CertificateException("Identidad desconocida.");
                chain[0].checkValidity();
                try {
                    byte[] digest = MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded());
                    StringBuilder actual = new StringBuilder();
                    for (byte value : digest) actual.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
                    if (!MessageDigest.isEqual(actual.toString().getBytes(StandardCharsets.US_ASCII),
                            pin.getBytes(StandardCharsets.US_ASCII))) {
                        throw new CertificateException("La PC no coincide con la identidad del QR.");
                    }
                } catch (java.security.NoSuchAlgorithmException error) {
                    throw new CertificateException(error);
                }
            }
        }}, null);
        connection.setSSLSocketFactory(tls.getSocketFactory());
        // The physical QR authenticates the certificate for this address.
        connection.setHostnameVerifier((hostname, sslSession) -> address.getHost().equals(hostname));
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(8000);
            connection.setReadTimeout(path.equals("/chat") ? 95000 : 5000);
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Authorization", "Bearer " + code);
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty("X-Device", android.os.Build.MODEL.replaceAll("[^ -~]", "?"));
            connection.setDoOutput(true);
            byte[] data = new JSONObject().put("message", text).toString().getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(data.length);
            try (OutputStream output = connection.getOutputStream()) {
                output.write(data);
            }
            if (path.equals("/chat")) setPhase("Esperando respuesta de la PC");
            int status = connection.getResponseCode();
            if (path.equals("/chat")) setPhase("Recibiendo datos");
            try (InputStream input = status == 200 ? connection.getInputStream() : connection.getErrorStream()) {
                if (input == null) throw new IllegalStateException("HTTP " + status);
                ByteArrayOutputStream body = new ByteArrayOutputStream();
                byte[] buffer = new byte[4096];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    if (body.size() + count > 262144) throw new IllegalStateException("Respuesta demasiado grande.");
                    body.write(buffer, 0, count);
                }
                JSONObject result = new JSONObject(body.toString("UTF-8"));
                if (status != 200) throw new RequestFailure(status, result.optString("error", "HTTP " + status));
                return result;
            }
        } finally {
            connection.disconnect();
        }
    }
}
