package top.eastcloud.flyai.poc;

import android.util.Base64;

import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.Locale;

/**
 * OTA 清单解析与验签。
 *
 * 清单是 GitHub Release 上的 poc-update.json；签名载荷是固定顺序的文本，
 * 与 mobile/poc/build_overlay.py 的 canonical_payload 必须逐字节一致。
 * OTA_PUBLIC_KEY 由构建脚本注入（base64 DER），未注入时所有更新都会被拒绝。
 */
final class OtaManifest {
    private OtaManifest() {}

    static final String OTA_PUBLIC_KEY = "__OTA_PUBLIC_KEY__";
    static final String MANIFEST_URL = "__OTA_MANIFEST_URL__";

    static JSONObject read(File file) throws Exception {
        if (file == null || !file.isFile() || file.length() > 64 * 1024) {
            throw new IllegalArgumentException("清单不存在或过大");
        }
        byte[] data = new byte[(int) file.length()];
        try (InputStream in = new FileInputStream(file)) {
            int off = 0;
            while (off < data.length) {
                int n = in.read(data, off, data.length - off);
                if (n < 0) break;
                off += n;
            }
        }
        return new JSONObject(new String(data, StandardCharsets.UTF_8));
    }

    static JSONObject read(String text) throws Exception {
        return new JSONObject(text);
    }

    static String canonicalPayload(JSONObject m) {
        JSONObject overlay = m.optJSONObject("overlay");
        JSONObject full = m.optJSONObject("full");
        StringBuilder sb = new StringBuilder();
        sb.append("flyai-poc-overlay-v1\n");
        sb.append(m.optLong("version_code", 0)).append('\n');
        sb.append(m.optLong("overlay_version", 0)).append('\n');
        sb.append(m.optString("code_rev", "")).append('\n');
        sb.append(m.optString("requirements_sha256", "")).append('\n');
        sb.append(m.optLong("min_app_version_code", 0)).append('\n');
        sb.append(overlay == null ? "-" : overlay.optString("sha256", "-")).append('\n');
        sb.append(overlay == null ? 0 : overlay.optLong("size", 0)).append('\n');
        sb.append(full == null ? "-" : full.optString("sha256", "-")).append('\n');
        sb.append(full == null ? 0 : full.optLong("size", 0)).append('\n');
        sb.append(full == null ? 0 : full.optLong("version_code", 0)).append('\n');
        sb.append(m.optString("signing_key_id", "")).append('\n');
        return sb.toString();
    }

    static boolean verify(JSONObject m) {
        if (m == null) return false;
        if (m.optInt("schema", 0) != 1) return false;
        if (!"poc".equals(m.optString("channel", ""))) return false;
        if (!isHex(m.optString("code_rev", ""), 16, 64)) return false;
        if (!isHex(m.optString("requirements_sha256", ""), 64, 64)) return false;
        JSONObject overlay = m.optJSONObject("overlay");
        if (overlay != null) {
            if (!isHex(overlay.optString("sha256", ""), 64, 64)) return false;
            if (overlay.optLong("size", 0) <= 0 || overlay.optLong("size", 0) > 20L * 1024 * 1024) {
                return false;
            }
        }
        JSONObject full = m.optJSONObject("full");
        if (full != null) {
            if (!isHex(full.optString("sha256", ""), 64, 64)) return false;
            if (full.optLong("size", 0) <= 0 || full.optLong("size", 0) > 200L * 1024 * 1024) {
                return false;
            }
        }
        String signature = m.optString("signature", "");
        if (signature.isEmpty() || OTA_PUBLIC_KEY.isEmpty()
                || OTA_PUBLIC_KEY.startsWith("__")) {
            return false;
        }
        try {
            byte[] keyBytes = Base64.decode(OTA_PUBLIC_KEY, Base64.DEFAULT);
            PublicKey key = KeyFactory.getInstance("RSA")
                    .generatePublic(new X509EncodedKeySpec(keyBytes));
            Signature verifier = Signature.getInstance("SHA256withRSA");
            verifier.initVerify(key);
            verifier.update(canonicalPayload(m).getBytes(StandardCharsets.UTF_8));
            return verifier.verify(Base64.decode(signature, Base64.DEFAULT));
        } catch (Exception e) {
            return false;
        }
    }

    static boolean isHex(String value, int min, int max) {
        if (value == null) return false;
        int len = value.length();
        if (len < min || len > max) return false;
        for (int i = 0; i < len; i++) {
            char c = value.charAt(i);
            boolean ok = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
            if (!ok) return false;
        }
        return true;
    }

    static String sha256(File file) {
        try {
            java.security.MessageDigest digest = java.security.MessageDigest.getInstance("SHA-256");
            try (InputStream in = new FileInputStream(file)) {
                byte[] buffer = new byte[1 << 16];
                int n;
                while ((n = in.read(buffer)) > 0) {
                    digest.update(buffer, 0, n);
                }
            }
            StringBuilder sb = new StringBuilder();
            for (byte b : digest.digest()) {
                sb.append(String.format(Locale.US, "%02x", b & 0xff));
            }
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    static String trim(String value) {
        return value == null ? "" : value.trim();
    }
}
