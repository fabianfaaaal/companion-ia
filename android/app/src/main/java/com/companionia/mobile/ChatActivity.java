package com.companionia.mobile;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.text.InputType;
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

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(24, 40, 24, 24);
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
        message = field(layout, "Mensaje");
        send = new Button(this);
        send.setText("Enviar");
        send.setEnabled(false);
        send.setOnClickListener(view -> sendMessage());
        layout.addView(send);
        setContentView(layout);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        IntentResult result = IntentIntegrator.parseActivityResult(requestCode, resultCode, data);
        if (result != null && result.getContents() != null) {
            pair(result.getContents());
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    private EditText field(LinearLayout layout, String hint) {
        EditText field = new EditText(this);
        field.setHint(hint);
        layout.addView(field);
        return field;
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
        send.setEnabled(false);
        new Thread(() -> {
            try {
                String answer = request(address, fingerprint, code, "/chat", text).getString("answer");
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    transcript.append("Vos: " + text + "\n\nCompanion: " + answer + "\n\n");
                    if (message.getText().toString().trim().equals(text)) message.setText("");
                    send.setEnabled(true);
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    Toast.makeText(this, error.getMessage(), Toast.LENGTH_LONG).show();
                    send.setEnabled(true);
                });
            }
        }).start();
    }

    private void pair(String contents) {
        if (contents.length() > 2048) return;
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
                    transcript.setText("Conectado a Companion.\n\n");
                    send.setEnabled(true);
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (isDestroyed()) return;
                    send.setEnabled(session != null);
                    Toast.makeText(this, error.getMessage(), Toast.LENGTH_LONG).show();
                });
            }
        }).start();
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
            connection.setReadTimeout(95000);
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
            int status = connection.getResponseCode();
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
                if (status != 200) throw new IllegalStateException(result.optString("error", "HTTP " + status));
                return result;
            }
        } finally {
            connection.disconnect();
        }
    }
}
