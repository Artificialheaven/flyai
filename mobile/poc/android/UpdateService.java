package top.eastcloud.flyai.poc;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.Uri;
import android.os.Build;
import android.os.IBinder;
import android.provider.Settings;
import android.util.Log;

import androidx.core.content.FileProvider;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 更新前台服务：拉清单、验签、下载 overlay / 完整 APK。
 *
 * 不负责应用 overlay——应用由 BootService 在下次启动前完成；
 * 完整 APK 下载完成后直接调系统安装器。
 */
public class UpdateService extends Service {
    private static final String TAG = "FlyAI-Update";
    private static final String CHANNEL = "flyai_update";
    private static final int NOTIFICATION_ID = 1002;
    private static final long MAX_MANIFEST = 64 * 1024;
    private static final long MAX_OVERLAY = 20L * 1024 * 1024;
    private static final long MAX_APK = 220L * 1024 * 1024;

    static final String ACTION_STATUS = "top.eastcloud.flyai.poc.UPDATE_STATUS";
    static final String ACTION_CHECK = "top.eastcloud.flyai.poc.UPDATE_CHECK";
    static final String ACTION_DOWNLOAD_OVERLAY = "top.eastcloud.flyai.poc.UPDATE_OVERLAY";
    static final String ACTION_DOWNLOAD_FULL = "top.eastcloud.flyai.poc.UPDATE_FULL";
    static final String ACTION_CANCEL = "top.eastcloud.flyai.poc.UPDATE_CANCEL";

    private final AtomicBoolean busy = new AtomicBoolean(false);
    private volatile boolean cancelled;
    private volatile long lastProgressAt;
    private volatile long lastProgressBytes;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        startForegroundCompat("正在检查更新…", 0, 0);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? "" : intent.getAction();
        if (ACTION_CANCEL.equals(action)) {
            cancelled = true;
            broadcastStatus("idle", null, "已取消");
            stopSelf();
            return START_NOT_STICKY;
        }
        if (!busy.compareAndSet(false, true)) {
            broadcastStatus("busy", null, "已有更新任务在进行");
            return START_NOT_STICKY;
        }
        cancelled = false;
        new Thread(() -> {
            try {
                if (ACTION_DOWNLOAD_OVERLAY.equals(action)) {
                    downloadOverlay();
                } else if (ACTION_DOWNLOAD_FULL.equals(action)) {
                    downloadFull();
                } else {
                    checkOnly();
                }
            } catch (Exception e) {
                Log.w(TAG, "update task failed", e);
                broadcastStatus("failed", null, messageOf(e));
            } finally {
                busy.set(false);
                stopForegroundSafe();
                stopSelf();
            }
        }, "flyai-update").start();
        return START_NOT_STICKY;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager manager = getSystemService(NotificationManager.class);
        NotificationChannel channel = new NotificationChannel(
                CHANNEL, "FlyAI 更新", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("下载 FlyAI 代码增量包或完整安装包");
        manager.createNotificationChannel(channel);
    }

    private void startForegroundCompat(String text, long loaded, long total) {
        Intent open = new Intent(this, MainActivity.class);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pending = PendingIntent.getActivity(this, 0, open, flags);
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL)
                : new Notification.Builder(this);
        Notification notification = builder
                .setContentTitle("FlyAI 更新")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setOngoing(true)
                .setContentIntent(pending)
                .build();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void updateNotification(String text, long loaded, long total) {
        try {
            Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                    ? new Notification.Builder(this, CHANNEL)
                    : new Notification.Builder(this);
            builder.setContentTitle("FlyAI 更新")
                    .setContentText(text)
                    .setSmallIcon(android.R.drawable.stat_sys_download)
                    .setOngoing(true);
            if (total > 0) {
                builder.setProgress(100, (int) Math.min(100, loaded * 100 / total), false);
            } else {
                builder.setProgress(0, 0, true);
            }
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) manager.notify(NOTIFICATION_ID, builder.build());
        } catch (Exception ignored) {
        }
    }

    private void stopForegroundSafe() {
        try {
            if (Build.VERSION.SDK_INT >= 24) {
                stopForeground(STOP_FOREGROUND_REMOVE);
            } else {
                stopForeground(true);
            }
        } catch (Exception ignored) {
        }
    }

    private static String messageOf(Throwable error) {
        String msg = error.getMessage();
        return msg == null || msg.isEmpty() ? error.getClass().getSimpleName() : msg;
    }

    private void broadcastStatus(String state, JSONObject data, String error) {
        JSONObject out = new JSONObject();
        try {
            out.put("state", state);
            if (data != null) {
                java.util.Iterator<String> keys = data.keys();
                while (keys.hasNext()) {
                    String key = keys.next();
                    out.put(key, data.opt(key));
                }
            }
            if (error != null) out.put("error", error);
            out.put("currentVersionCode", OtaState.versionCode(this));
            JSONObject stateJson = OtaState.read(getFilesDir());
            out.put("codeRev", stateJson.optString("codeRev", ""));
            out.put("overlayVersion", stateJson.optLong("overlayVersion", 0));
        } catch (Exception ignored) {
        }
        Intent intent = new Intent(ACTION_STATUS);
        intent.setPackage(getPackageName());
        intent.putExtra("json", out.toString());
        sendBroadcast(intent);
    }

    private void checkOnly() throws Exception {
        broadcastStatus("checking", null, null);
        File pending = new File(getFilesDir(), "update/pending/ready");
        if (pending.isFile()) {
            try {
                JSONObject m = OtaManifest.read(new File(getFilesDir(), "update/pending/manifest.json"));
                JSONObject data = summary(m, "overlay");
                data.put("staged", true);
                broadcastStatus("staged", data, null);
                return;
            } catch (Exception ignored) {
            }
        }

        Manifest manifest = fetchManifest();
        JSONObject choice = decide(manifest.json);
        if (choice == null) {
            broadcastStatus("uptodate", null, null);
            return;
        }
        if ("overlay".equals(choice.optString("kind"))) {
            // 上次下载完成但还没重启应用：直接复用固定目录里的包，不再重下
            File cached = cachedDownload(
                    manifest.json.optJSONObject("overlay"), "poc-overlay.tar.gz");
            if (cached != null) {
                stageOverlay(manifest.raw, cached, choice);
                return;
            }
        } else if ("full".equals(choice.optString("kind"))) {
            // 上次 APK 下载完成但安装被取消：标记出来，点击后直接安装
            File cached = cachedDownload(
                    manifest.json.optJSONObject("full"), "FlyAI-POC-update.apk");
            if (cached != null) {
                choice.put("cached", true);
            }
        }
        broadcastStatus("available", choice, null);
    }

    /** 返回 overlay / full 二选一；无可更新返回 null。 */
    private JSONObject decide(JSONObject manifest) throws Exception {
        JSONObject state = OtaState.read(getFilesDir());
        long current = OtaState.versionCode(this);
        long manifestCode = manifest.optLong("version_code", 0);
        long minApp = manifest.optLong("min_app_version_code", 0);
        long overlayVersion = manifest.optLong("overlay_version", 0);
        String codeRev = manifest.optString("code_rev", "");
        String req = manifest.optString("requirements_sha256", "");
        String stateReq = state.optString("requirementsSha256", "");
        if (stateReq.isEmpty()) {
            stateReq = OtaState.requirementsSha256(new File(getFilesDir(), "rootfs"));
        }
        JSONObject last = state.optJSONObject("lastRollback");
        String badRev = last == null ? "" : last.optString("codeRev", "");

        JSONObject overlay = manifest.optJSONObject("overlay");
        boolean overlayOk = overlay != null
                && req.equals(stateReq)
                && current >= minApp
                && (manifestCode <= 0 || current <= manifestCode)
                && overlayVersion > state.optLong("overlayVersion", 0)
                && !codeRev.isEmpty()
                && !codeRev.equals(state.optString("codeRev", ""))
                && !codeRev.equals(badRev);
        if (overlayOk) {
            JSONObject data = summary(manifest, "overlay");
            data.put("size", overlay.optLong("size", 0));
            return data;
        }

        JSONObject full = manifest.optJSONObject("full");
        if (full != null && full.optLong("version_code", 0) > current) {
            JSONObject data = summary(manifest, "full");
            data.put("size", full.optLong("size", 0));
            data.put("fullVersionCode", full.optLong("version_code", 0));
            return data;
        }
        return null;
    }

    private JSONObject summary(JSONObject manifest, String kind) throws Exception {
        JSONObject data = new JSONObject();
        data.put("kind", kind);
        data.put("version", manifest.optString("version", ""));
        data.put("versionCode", manifest.optLong("version_code", 0));
        data.put("overlayVersion", manifest.optLong("overlay_version", 0));
        data.put("notes", manifest.optString("notes", ""));
        return data;
    }

    private void downloadOverlay() throws Exception {
        Manifest manifest = fetchManifest();
        JSONObject choice = decide(manifest.json);
        if (choice == null || !"overlay".equals(choice.optString("kind"))) {
            broadcastStatus("uptodate", null, null);
            return;
        }
        JSONObject overlay = manifest.json.optJSONObject("overlay");
        if (overlay == null) throw new IllegalStateException("清单没有 overlay");
        String url = overlay.optString("url", "");
        if (url.isEmpty()) throw new IllegalStateException("overlay 下载地址为空");
        long size = overlay.optLong("size", 0);
        String sha = overlay.optString("sha256", "");

        File cached = cachedDownload(overlay, "poc-overlay.tar.gz");
        if (cached == null) {
            File dir = downloadDir();
            File archive = new File(dir, fileNameOf(url, "poc-overlay.tar.gz"));
            broadcastStatus("downloading", choice, null);
            download(url, archive, MAX_OVERLAY, size, sha, "代码增量包");
            cached = archive;
        }
        stageOverlay(manifest.raw, cached, choice);
    }

    /** 把已下载并校验过的 overlay 放进 pending，重启后由 BootService 应用。 */
    private void stageOverlay(String manifestRaw, File source, JSONObject choice)
            throws Exception {
        File pending = new File(getFilesDir(), "update/pending");
        OtaState.deleteRecursive(pending);
        if (!pending.mkdirs()) throw new RuntimeException("无法创建待生效目录");
        File payload = new File(pending, "payload.tar.gz");
        // 保留下载目录里的原件，下次重启前若 pending 丢失还能复用
        copyFile(source, payload);
        writeText(new File(pending, "manifest.json"), manifestRaw);
        writeText(new File(pending, "ready"), "");
        broadcastStatus("staged", choice, null);
    }

    private void downloadFull() throws Exception {
        Manifest manifest = fetchManifest();
        JSONObject full = manifest.json.optJSONObject("full");
        if (full == null) throw new IllegalStateException("清单没有完整 APK");
        long current = OtaState.versionCode(this);
        if (full.optLong("version_code", 0) <= current) {
            broadcastStatus("uptodate", null, null);
            return;
        }
        String url = full.optString("url", "");
        if (url.isEmpty()) throw new IllegalStateException("完整 APK 下载地址为空");
        JSONObject data = summary(manifest.json, "full");
        data.put("size", full.optLong("size", 0));
        File apk = cachedDownload(full, "FlyAI-POC-update.apk");
        if (apk == null) {
            File dir = downloadDir();
            apk = new File(dir, fileNameOf(url, "FlyAI-POC-update.apk"));
            broadcastStatus("downloading", data, null);
            download(url, apk, MAX_APK, full.optLong("size", 0), full.optString("sha256", ""), "完整 APK");
        }
        data.put("cached", true);
        broadcastStatus("installing", data, null);
        installApk(apk);
    }

    /** 所有下载统一放在 filesDir/update/download，文件名取自远端 URL。 */
    private File downloadDir() {
        File dir = new File(getFilesDir(), "update/download");
        if (!dir.isDirectory() && !dir.mkdirs()) {
            throw new RuntimeException("无法创建下载目录");
        }
        return dir;
    }

    private static String fileNameOf(String url, String fallback) {
        String name = url.substring(url.lastIndexOf('/') + 1);
        int cut = name.indexOf('?');
        if (cut >= 0) name = name.substring(0, cut);
        name = name.replaceAll("[^A-Za-z0-9._-]", "_");
        if (name.isEmpty() || name.startsWith(".")) name = fallback;
        return name;
    }

    /**
     * 下载目录里已有大小/SHA-256 都对得上的文件时直接复用；
     * 校验失败的文件删除，避免下次继续误用。
     */
    private File cachedDownload(JSONObject item, String fallbackName) {
        if (item == null) return null;
        String url = item.optString("url", "");
        if (url.isEmpty()) return null;
        File file = new File(downloadDir(), fileNameOf(url, fallbackName));
        if (!file.isFile()) return null;
        long size = item.optLong("size", 0);
        String sha = item.optString("sha256", "");
        boolean ok = size <= 0 || file.length() == size;
        if (ok && !sha.isEmpty()) {
            try {
                ok = OtaManifest.sha256(file).equalsIgnoreCase(sha);
            } catch (Exception e) {
                ok = false;
            }
        }
        if (!ok) {
            //noinspection ResultOfMethodCallIgnored
            file.delete();
            return null;
        }
        return file;
    }

    private Manifest fetchManifest() throws Exception {
        String raw = fetchText(OtaManifest.MANIFEST_URL, MAX_MANIFEST);
        JSONObject json = OtaManifest.read(raw);
        if (!OtaManifest.verify(json)) {
            throw new IllegalStateException("更新清单验签失败");
        }
        return new Manifest(json, raw);
    }

    private String fetchText(String url, long maxBytes) throws Exception {
        HttpURLConnection conn = null;
        try {
            URL target = new URL(url);
            if (!isAllowedUrl(target)) {
                throw new IllegalStateException("更新地址必须是 HTTPS");
            }
            conn = (HttpURLConnection) target.openConnection();
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(30000);
            conn.setInstanceFollowRedirects(true);
            conn.setRequestProperty("Accept", "application/json");
            conn.setRequestProperty("Accept-Encoding", "identity");
            conn.setRequestProperty("User-Agent", "FlyAI-POC/" + OtaState.versionCode(this));
            int code = conn.getResponseCode();
            if (code != 200) throw new IllegalStateException("HTTP " + code);
            if (!isAllowedUrl(conn.getURL())) {
                throw new IllegalStateException("重定向到非 HTTPS 地址");
            }
            long length = Math.max(0, conn.getContentLength());
            if (length > maxBytes) throw new IllegalStateException("清单过大");
            try (InputStream in = conn.getInputStream()) {
                java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
                byte[] buffer = new byte[8192];
                int n;
                long total = 0;
                while ((n = in.read(buffer)) > 0) {
                    total += n;
                    if (total > maxBytes) throw new IllegalStateException("清单过大");
                    out.write(buffer, 0, n);
                }
                return out.toString("UTF-8");
            }
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private void download(String url, File dest, long maxBytes, long expectedSize,
                          String expectedSha, String label) throws Exception {
        HttpURLConnection conn = null;
        File part = new File(dest.getPath() + ".part");
        //noinspection ResultOfMethodCallIgnored
        part.delete();
        try {
            URL target = new URL(url);
            if (!isAllowedUrl(target)) {
                throw new IllegalStateException("下载地址必须是 HTTPS");
            }
            conn = (HttpURLConnection) target.openConnection();
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(30000);
            conn.setInstanceFollowRedirects(true);
            conn.setRequestProperty("Accept-Encoding", "identity");
            conn.setRequestProperty("User-Agent", "FlyAI-POC/" + OtaState.versionCode(this));
            int code = conn.getResponseCode();
            if (code != 200) throw new IllegalStateException("HTTP " + code);
            if (!isAllowedUrl(conn.getURL())) {
                throw new IllegalStateException("重定向到非 HTTPS 地址");
            }
            long total = Math.max(0, conn.getContentLength());
            if (total <= 0 && expectedSize > 0) total = expectedSize;
            if (total > maxBytes) throw new IllegalStateException("下载包过大");
            lastProgressAt = 0;
            lastProgressBytes = 0;
            long loaded = 0;
            try (InputStream in = conn.getInputStream();
                 OutputStream out = new FileOutputStream(part)) {
                byte[] buffer = new byte[1 << 16];
                int n;
                while ((n = in.read(buffer)) > 0) {
                    if (cancelled) throw new IllegalStateException("已取消");
                    out.write(buffer, 0, n);
                    loaded += n;
                    if (loaded > maxBytes) throw new IllegalStateException("下载包过大");
                    long now = System.currentTimeMillis();
                    if (now - lastProgressAt > 200 || loaded - lastProgressBytes > 256 * 1024) {
                        lastProgressAt = now;
                        lastProgressBytes = loaded;
                        JSONObject progress = new JSONObject();
                        progress.put("loaded", loaded);
                        progress.put("total", total);
                        progress.put("percent", total > 0 ? Math.min(100, loaded * 100 / total) : 0);
                        progress.put("label", label);
                        broadcastStatus("downloading", progress, null);
                        updateNotification(label + " " + (total > 0
                                ? (loaded * 100 / total) + "%"
                                : (loaded / 1024 / 1024) + " MB"), loaded, total);
                    }
                }
            }
            if (expectedSize > 0 && part.length() != expectedSize) {
                throw new IllegalStateException("大小不符");
            }
            if (!expectedSha.isEmpty() && !OtaManifest.sha256(part).equalsIgnoreCase(expectedSha)) {
                throw new IllegalStateException("SHA-256 校验失败");
            }
            File parent = dest.getParentFile();
            if (parent != null && !parent.isDirectory()) parent.mkdirs();
            //noinspection ResultOfMethodCallIgnored
            dest.delete();
            if (!part.renameTo(dest)) {
                copyFile(part, dest);
                //noinspection ResultOfMethodCallIgnored
                part.delete();
            }
        } catch (Exception e) {
            //noinspection ResultOfMethodCallIgnored
            part.delete();
            throw e;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private void installApk(File apk) throws Exception {
        if (Build.VERSION.SDK_INT >= 26
                && !getPackageManager().canRequestPackageInstalls()) {
            Intent settings = new Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + getPackageName()));
            settings.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(settings);
            throw new IllegalStateException("请先允许安装未知应用");
        }
        Uri uri = FileProvider.getUriForFile(
                this, getPackageName() + ".fileprovider", apk);
        Intent intent = new Intent(Intent.ACTION_VIEW);
        intent.setDataAndType(uri, "application/vnd.android.package-archive");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                | Intent.FLAG_ACTIVITY_NEW_TASK);
        intent.setClipData(ClipData.newRawUri(apk.getName(), uri));
        startActivity(intent);
    }

    /** OTA 只允许 HTTPS；本机调试允许 http://127.0.0.1。 */
    private static boolean isAllowedUrl(URL url) {
        if (url == null) return false;
        if ("https".equalsIgnoreCase(url.getProtocol())) return true;
        if (!"http".equalsIgnoreCase(url.getProtocol())) return false;
        String host = url.getHost();
        return "127.0.0.1".equals(host) || "localhost".equals(host) || "::1".equals(host);
    }

    private static void writeText(File file, String text) throws Exception {
        File parent = file.getParentFile();
        if (parent != null && !parent.isDirectory()) parent.mkdirs();
        try (FileOutputStream out = new FileOutputStream(file)) {
            out.write(text.getBytes("UTF-8"));
        }
    }

    private static void copyFile(File source, File target) throws Exception {
        try (InputStream in = new java.io.FileInputStream(source);
             OutputStream out = new FileOutputStream(target)) {
            byte[] buffer = new byte[1 << 16];
            int n;
            while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
        }
    }

    private static final class Manifest {
        final JSONObject json;
        final String raw;

        Manifest(JSONObject json, String raw) {
            this.json = json;
            this.raw = raw;
        }
    }
}
