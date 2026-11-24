package top.eastcloud.flyai.poc;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.io.RandomAccessFile;
import java.net.InetAddress;
import java.util.ArrayList;
import java.util.List;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 前台服务：首次启动解包 Debian 用户态 + 用 PRoot 拉起 FlyAI（FastAPI）。
 * 原生侧只负责进程与文件，业务全部在 rootfs 里的 Python 中。
 */
public class BootService extends Service {
    private static final String TAG = "FlyAI-Boot";
    private static final String CHANNEL = "flyai_server";
    private static final int NOTIFICATION_ID = 1001;
    private static final int PORT = 8000;

    /**
     * 打包时由 mobile/poc/build_poc.py 注入 rootfs 内容版本（哈希前 16 位）。
     * 设备上安装过的版本与当前 APK 不一致时，只更新代码目录，保留 .venv / config / data。
     * 手动构建未注入时保留占位符也仍然可以运行。
     */
    private static final String ROOTFS_REV = "__ROOTFS_REV__";

    /** 打包时注入的代码树版本；与 OTA 清单的 code_rev 使用同一算法。 */
    private static final String CODE_REV = "__CODE_REV__";

    private volatile Process server;
    private PowerManager.WakeLock wakeLock;
    private volatile boolean starting;

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        try {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "FlyAI:server");
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire();
        } catch (Exception ignored) {
        }
        startForegroundCompat("正在准备运行环境…");
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (!starting && (server == null || !server.isAlive())) {
            starting = true;
            new Thread(this::boot, "flyai-boot").start();
        }
        return START_STICKY;
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager manager = getSystemService(NotificationManager.class);
        NotificationChannel channel = new NotificationChannel(
                CHANNEL, "FlyAI 本地服务", NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("在设备上运行 FlyAI 服务端");
        manager.createNotificationChannel(channel);
    }

    private void startForegroundCompat(String text) {
        Intent open = new Intent(this, MainActivity.class);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pending = PendingIntent.getActivity(this, 0, open, flags);
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL)
                : new Notification.Builder(this);
        Notification notification = builder
                .setContentTitle("FlyAI 本地服务")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setOngoing(true)
                .setContentIntent(pending)
                .build();
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void stage(String text) {
        try {
            Intent intent = new Intent(ACTION_STATUS);
            intent.setPackage(getPackageName());
            intent.putExtra("text", text);
            sendBroadcast(intent);
        } catch (Exception ignored) {
        }
        try (java.io.FileWriter writer = new java.io.FileWriter(new File(getFilesDir(), "boot.log"), true)) {
            String time = new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date());
            writer.write("[" + time + "] " + text + "\n");
        } catch (Exception ignored) {
        }
    }

    static final String ACTION_STATUS = "top.eastcloud.flyai.poc.BOOT_STATUS";

    private void notify(String text) {
        try {
            Intent intent = new Intent(ACTION_STATUS);
            intent.setPackage(getPackageName());
            intent.putExtra("text", text);
            sendBroadcast(intent);
        } catch (Exception ignored) {
        }
        try {
            startForegroundCompat(text);
        } catch (Exception ignored) {
        }
    }

    private void boot() {
        File files = getFilesDir();
        File rootfs = new File(files, "rootfs");
        File prootDir = new File(files, "proot");
        try {
            stage("准备 PRoot 运行环境");
            installProot(prootDir);

            JSONObject state = OtaState.read(files);
            recoverInterruptedApply(files, rootfs, state);
            state = OtaState.read(files);

            // 上一次 overlay 连续两次没有收到前端 markReady：先回滚再启动
            if (isOverlayCode(state) && state.optJSONObject("boot") != null
                    && state.optJSONObject("boot").optInt("attempts", 0) >= 2) {
                stage("上次更新未通过启动检测，回滚…");
                rollback(files, rootfs, state, "前端连续两次未就绪");
                state = OtaState.read(files);
            }

            File marker = new File(rootfs, ".installed");
            String baseRev = state.optString("baseRev", "");
            boolean codeMissing = !new File(rootfs, "opt/flyai/main.py").isFile();
            boolean fresh = codeMissing && baseRev.isEmpty() && !marker.isFile();
            boolean upgrade = !fresh && (codeMissing || !ROOTFS_REV.equals(baseRev));
            if (fresh || upgrade) {
                if (fresh) {
                    notify("首次启动：正在解压运行环境（约 350MB）…");
                    stage("解压 rootfs（约 350MB，首次启动）…");
                    deleteRecursive(rootfs);
                } else {
                    notify("正在更新运行环境…");
                    stage("rootfs 版本变化，更新代码（保留数据与配置）…");
                    deleteCode(rootfs);
                }
                try (InputStream payload = getAssets().open("rootfs_payload.bin")) {
                    TarGzExtractor.extract(payload, rootfs);
                }
                new File(rootfs, "opt/flyai/data").mkdirs();
                writeMarker(marker, ROOTFS_REV);

                state = OtaState.defaults();
                state.put("baseRev", ROOTFS_REV);
                state.put("codeRev", bundledCodeRev());
                state.put("overlayVersion", 0L);
                state.put("requirementsSha256", OtaState.requirementsSha256(rootfs));
                state.put("appVersionCode", OtaState.versionCode(this));
                OtaState.write(files, state);
                OtaState.deleteRecursive(new File(files, "update/pending"));
                OtaState.deleteRecursive(new File(files, "update/backup"));
                stage(fresh ? "rootfs 解压完成" : "rootfs 更新完成");
            } else {
                stage("rootfs 已安装（" + ROOTFS_REV + "），跳过解压");
            }

            // 有待生效的代码增量包：在启动 Python 前应用
            if (applyPending(files, rootfs)) {
                state = OtaState.read(files);
            }

            prepareDns(rootfs);
            notify("正在启动 FlyAI…");
            stage("启动 Python 服务（PRoot）…");
            startServer(files, rootfs, prootDir);

            if (!waitForPort(60000)) {
                stage("服务 60 秒内未就绪");
                state = OtaState.read(files);
                if (!state.optString("backupDir", "").isEmpty()) {
                    rollback(files, rootfs, state, "服务启动超时");
                    startServer(files, rootfs, prootDir);
                    if (!waitForPort(60000)) {
                        notify("回滚后服务仍未就绪，请查看控制台");
                    } else {
                        notify("更新失败，已恢复到上一版本");
                    }
                } else {
                    notify("启动失败：本地服务未就绪");
                }
            } else {
                state = OtaState.read(files);
                if (isOverlayCode(state)) {
                    JSONObject boot = state.optJSONObject("boot");
                    if (boot == null) boot = new JSONObject();
                    boot.put("attempts", boot.optInt("attempts", 0) + 1);
                    boot.put("lastCodeRev", state.optString("codeRev", ""));
                    state.put("boot", boot);
                    OtaState.write(files, state);
                }
                readLogTail(files);
                notify("FlyAI 正在运行");
                stage("服务进程已拉起，端口 8000 就绪");
            }
        } catch (Throwable error) {
            Log.e(TAG, "boot failed", error);
            notify("启动失败：" + error.getMessage());
            stage("启动失败：" + error);
        } finally {
            starting = false;
        }
    }

    private static String bundledCodeRev() {
        if (CODE_REV == null || CODE_REV.isEmpty() || CODE_REV.startsWith("__")) {
            return "";
        }
        return CODE_REV;
    }

    private static boolean isOverlayCode(JSONObject state) {
        String code = state == null ? "" : state.optString("codeRev", "");
        return !code.isEmpty() && !code.equals(bundledCodeRev());
    }

    private boolean waitForPort(long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (System.currentTimeMillis() < deadline) {
            try (java.net.Socket socket = new java.net.Socket()) {
                socket.connect(new java.net.InetSocketAddress("127.0.0.1", PORT), 500);
                return true;
            } catch (Exception ignored) {
                try {
                    Thread.sleep(500);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return false;
                }
            }
        }
        return false;
    }

    private void stopServer() {
        Process current = server;
        server = null;
        if (current == null) {
            return;
        }
        current.destroy();
        try {
            Thread.sleep(800);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        if (current.isAlive()) current.destroyForcibly();
        // 等旧 proot 真正退出，避免新实例启动时还占着 proot 可执行文件
        long deadline = System.currentTimeMillis() + 3000;
        while (current.isAlive() && System.currentTimeMillis() < deadline) {
            try {
                Thread.sleep(100);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }
    }

    private static void deleteCode(File rootfs) {
        File opt = new File(rootfs, "opt/flyai");
        deleteRecursive(new File(opt, "flyai"));
        deleteRecursive(new File(opt, "plugins"));
        deleteIfExists(new File(opt, "main.py"));
        deleteIfExists(new File(opt, "requirements.txt"));
        deleteIfExists(new File(opt, "start.sh"));
        deleteIfExists(new File(opt, "config/config.example.json"));
    }

    /**
     * 上次 apply 在解包中途被杀：恢复 backup；没有 backup 就强制全量。
     */
    private void recoverInterruptedApply(File files, File rootfs, JSONObject state)
            throws Exception {
        JSONObject applying = state.optJSONObject("applying");
        if (applying == null) return;
        stage("检测到未完成的代码更新，恢复上一版本");
        String backup = state.optString("backupDir", "");
        try {
            if (!backup.isEmpty() && new File(files, backup).isDirectory()) {
                restoreBackup(files, rootfs, state);
            } else {
                // 没有可用备份：清空 baseRev，让下面走全量解包
                state.put("applying", JSONObject.NULL);
                state.put("baseRev", "");
                OtaState.write(files, state);
            }
        } catch (Exception e) {
            stage("恢复失败，将重新解包基础 rootfs：" + e);
            state.put("applying", JSONObject.NULL);
            state.put("baseRev", "");
            OtaState.write(files, state);
        }
    }

    /**
     * 应用 update/pending。返回是否应用成功；失败时保持/恢复旧版本。
     */
    private boolean applyPending(File files, File rootfs) {
        File pending = new File(files, "update/pending");
        File ready = new File(pending, "ready");
        if (!ready.isFile()) return false;

        JSONObject manifest;
        try {
            manifest = OtaManifest.read(new File(pending, "manifest.json"));
        } catch (Exception e) {
            stage("更新清单读取失败：" + e.getMessage());
            dropPending(files);
            return false;
        }
        if (!OtaManifest.verify(manifest)) {
            stage("更新清单验签失败，已丢弃");
            dropPending(files);
            return false;
        }
        JSONObject overlay = manifest.optJSONObject("overlay");
        if (overlay == null) {
            stage("更新清单没有 overlay，已丢弃");
            dropPending(files);
            return false;
        }

        JSONObject state = OtaState.read(files);
        String stateReq = state.optString("requirementsSha256", "");
        if (stateReq.isEmpty()) {
            stateReq = OtaState.requirementsSha256(rootfs);
            try {
                state.put("requirementsSha256", stateReq);
            } catch (Exception ignored) {
            }
        }
        String manifestReq = manifest.optString("requirements_sha256", "");
        long currentVersion = OtaState.versionCode(this);
        long manifestVersion = manifest.optLong("version_code", 0);
        long minApp = manifest.optLong("min_app_version_code", 0);
        long overlayVersion = manifest.optLong("overlay_version", 0);
        String codeRev = manifest.optString("code_rev", "");
        JSONObject lastRollback = state.optJSONObject("lastRollback");
        String badRev = lastRollback == null ? "" : lastRollback.optString("codeRev", "");
        String reason = null;
        if (!manifestReq.equals(stateReq)) {
            reason = "requirements 与当前 base 不一致";
        } else if (currentVersion < minApp || (manifestVersion > 0 && currentVersion > manifestVersion)) {
            reason = "APK 版本不匹配";
        } else if (overlayVersion <= state.optLong("overlayVersion", 0)) {
            reason = "版本不高于已安装";
        } else if (codeRev.isEmpty() || codeRev.equals(state.optString("codeRev", ""))) {
            reason = "代码版本未变化";
        } else if (codeRev.equals(badRev)) {
            reason = "该版本上次已回滚";
        }
        if (reason != null) {
            stage("跳过更新：" + reason);
            dropPending(files);
            return false;
        }

        File payload = new File(pending, "payload.tar.gz");
        if (!payload.isFile()
                || payload.length() != overlay.optLong("size", -1)
                || !OtaManifest.sha256(payload).equals(overlay.optString("sha256", ""))) {
            stage("更新包大小/哈希校验失败，已丢弃");
            dropPending(files);
            return false;
        }

        String relBackup = "update/backup/" + System.currentTimeMillis();
        File backupOpt = new File(new File(files, relBackup), "opt/flyai");
        try {
            if (!backupOpt.isDirectory() && !backupOpt.mkdirs()) {
                throw new RuntimeException("无法创建更新备份目录");
            }
            // 先复制旧代码（保留相对路径，便于原样恢复）；复制失败时原目录不受影响
            File opt = new File(rootfs, "opt/flyai");
            copyIfExists(new File(opt, "flyai"), new File(backupOpt, "flyai"));
            copyIfExists(new File(opt, "plugins"), new File(backupOpt, "plugins"));
            copyIfExists(new File(opt, "main.py"), new File(backupOpt, "main.py"));
            copyIfExists(new File(opt, "requirements.txt"), new File(backupOpt, "requirements.txt"));
            copyIfExists(new File(opt, "start.sh"), new File(backupOpt, "start.sh"));
            copyIfExists(new File(opt, "config/config.example.json"),
                    new File(backupOpt, "config/config.example.json"));

            long prevOverlay = state.optLong("overlayVersion", 0);
            String prevCode = state.optString("codeRev", "");
            JSONObject applying = new JSONObject();
            applying.put("prevCodeRev", prevCode);
            applying.put("prevOverlayVersion", prevOverlay);
            state.put("applying", applying);
            state.put("backupDir", relBackup);
            state.put("prevCodeRev", prevCode);
            state.put("prevOverlayVersion", prevOverlay);
            OtaState.write(files, state);
            deleteCode(rootfs);

            TarGzExtractor.extract(payload, rootfs);
            if (!new File(rootfs, "opt/flyai/main.py").isFile()
                    || !new File(rootfs, "opt/flyai/flyai/app.py").isFile()
                    || !new File(rootfs, "opt/flyai/start.sh").isFile()) {
                throw new RuntimeException("增量包缺少关键代码文件");
            }

            state.put("codeRev", codeRev);
            state.put("overlayVersion", overlayVersion);
            state.put("applying", JSONObject.NULL);
            state.put("boot", new JSONObject().put("attempts", 0).put("lastCodeRev", codeRev));
            OtaState.write(files, state);
            // 已解包完成，pending 不再需要；backup 留到 markReady/回滚
            dropPending(files);
            stage("代码增量已应用：" + codeRev + "（旧版本已备份）");
            return true;
        } catch (Throwable error) {
            stage("应用增量失败，恢复上一版本：" + error);
            try {
                restoreBackup(files, rootfs, OtaState.read(files));
            } catch (Throwable restoreError) {
                stage("恢复备份失败：" + restoreError);
            }
            return false;
        }
    }

    private void restoreBackup(File files, File rootfs, JSONObject state) throws Exception {
        String relBackup = state.optString("backupDir", "");
        File backup = new File(files, relBackup);
        if (relBackup.isEmpty() || !backup.isDirectory()) {
            throw new RuntimeException("没有可用的更新备份");
        }
        deleteCode(rootfs);
        File backupOpt = new File(backup, "opt/flyai");
        File opt = new File(rootfs, "opt/flyai");
        if (!opt.isDirectory() && !opt.mkdirs()) {
            throw new RuntimeException("无法创建代码目录");
        }
        moveIfExists(new File(backupOpt, "flyai"), new File(opt, "flyai"));
        moveIfExists(new File(backupOpt, "plugins"), new File(opt, "plugins"));
        moveIfExists(new File(backupOpt, "main.py"), new File(opt, "main.py"));
        moveIfExists(new File(backupOpt, "requirements.txt"), new File(opt, "requirements.txt"));
        moveIfExists(new File(backupOpt, "start.sh"), new File(opt, "start.sh"));
        moveIfExists(new File(backupOpt, "config/config.example.json"),
                new File(opt, "config/config.example.json"));

        state.put("codeRev", state.optString("prevCodeRev", ""));
        state.put("overlayVersion", state.optLong("prevOverlayVersion", 0));
        state.put("applying", JSONObject.NULL);
        state.put("backupDir", "");
        state.put("prevCodeRev", "");
        state.put("prevOverlayVersion", 0);
        state.put("boot", new JSONObject().put("attempts", 0));
        OtaState.write(files, state);
        OtaState.deleteRecursive(backup);
        dropPending(files);
    }

    /** 回滚到 backup；没有 backup 时重新解包 APK 内置代码。 */
    private void rollback(File files, File rootfs, JSONObject state, String reason) {
        stopServer();
        String badRev = state.optString("codeRev", "");
        try {
            if (!state.optString("backupDir", "").isEmpty()) {
                restoreBackup(files, rootfs, state);
            } else {
                deleteCode(rootfs);
                try (InputStream payload = getAssets().open("rootfs_payload.bin")) {
                    TarGzExtractor.extract(payload, rootfs);
                }
                state = OtaState.defaults();
                state.put("baseRev", ROOTFS_REV);
                state.put("codeRev", bundledCodeRev());
                state.put("requirementsSha256", OtaState.requirementsSha256(rootfs));
                state.put("appVersionCode", OtaState.versionCode(this));
                OtaState.write(files, state);
                OtaState.deleteRecursive(new File(files, "update/pending"));
                OtaState.deleteRecursive(new File(files, "update/backup"));
            }
            state = OtaState.read(files);
            JSONObject last = new JSONObject();
            last.put("codeRev", badRev);
            last.put("at", System.currentTimeMillis());
            last.put("reason", reason == null ? "" : reason);
            state.put("lastRollback", last);
            OtaState.write(files, state);
            stage("已回滚，原因：" + reason);
        } catch (Throwable error) {
            stage("回滚失败：" + error);
        }
    }

    private static void dropPending(File files) {
        OtaState.deleteRecursive(new File(files, "update/pending"));
    }

    private static void copyIfExists(File source, File target) {
        if (source == null || !source.exists()) return;
        try {
            copyRecursive(source, target);
        } catch (Exception e) {
            throw new RuntimeException("备份失败: " + source + " → " + target, e);
        }
    }

    private static void moveIfExists(File source, File target) {
        if (source == null || !source.exists()) return;
        File parent = target.getParentFile();
        if (parent != null && !parent.isDirectory()) parent.mkdirs();
        if (source.renameTo(target)) return;
        try {
            copyRecursive(source, target);
            deleteRecursive(source);
        } catch (Exception e) {
            throw new RuntimeException("移动失败: " + source + " → " + target, e);
        }
    }

    private static void copyRecursive(File source, File target) throws Exception {
        File parent = target.getParentFile();
        if (parent != null && !parent.isDirectory()) parent.mkdirs();
        if (source.isDirectory()) {
            if (!target.isDirectory() && !target.mkdirs()) {
                throw new RuntimeException("无法创建目录: " + target);
            }
            File[] children = source.listFiles();
            if (children != null) {
                for (File child : children) {
                    copyRecursive(child, new File(target, child.getName()));
                }
            }
            return;
        }
        try (InputStream in = new java.io.FileInputStream(source);
             OutputStream out = new FileOutputStream(target)) {
            copy(in, out);
        }
    }

    /**
     * 把设备当前 DNS 写进 rootfs 的 /etc/resolv.conf。
     * Android 没有 glibc 可用的 resolv.conf，PRoot 里的 Ubuntu 必须自带一份；
     * 构建机上的集群 DNS（如 10.x）在手机上不可达，会导致 httpx 名称解析失败。
     */
    private void prepareDns(File rootfs) {
        List<String> servers = new ArrayList<>();
        try {
            ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
            Network network = cm == null ? null : cm.getActiveNetwork();
            LinkProperties props = network == null ? null : cm.getLinkProperties(network);
            if (props != null) {
                for (InetAddress address : props.getDnsServers()) {
                    String ip = address.getHostAddress();
                    if (ip != null && !ip.isEmpty() && !servers.contains(ip)) servers.add(ip);
                }
            }
        } catch (Exception ignored) {
        }
        if (servers.isEmpty()) {
            servers.add("223.5.5.5");
            servers.add("119.29.29.29");
        }
        try {
            File etc = new File(rootfs, "etc");
            if (!etc.isDirectory()) etc.mkdirs();
            try (PrintWriter out = new PrintWriter(new File(new File(rootfs, "etc"), "resolv.conf"), "UTF-8")) {
                for (String server : servers) out.println("nameserver " + server);
                out.println("options timeout:2 attempts:2");
            }
            try (PrintWriter out = new PrintWriter(new File(etc, "hosts"), "UTF-8")) {
                out.println("127.0.0.1 localhost");
                out.println("::1 localhost");
            }
            stage("DNS 已写入：" + String.join(", ", servers));
        } catch (Exception e) {
            stage("写入 DNS 失败：" + e);
        }
    }

    private void installProot(File dir) throws Exception {
        if (!dir.isDirectory() && !dir.mkdirs()) {
            throw new RuntimeException("无法创建 proot 目录");
        }
        String[] names = {"proot", "loader", "libtalloc.so.2", "libandroid-shmem.so"};
        for (String name : names) {
            File out = new File(dir, name);
            long expected = assetSize("proot/" + name);
            // 更新重启时旧 proot 可能仍在执行，直接写会 ETXTBSY；
            // 大小一致就保留，否则写临时文件后 rename 原子替换。
            if (out.isFile() && expected >= 0 && out.length() == expected) {
                out.setExecutable(true, true);
                continue;
            }
            File tmp = new File(dir, name + ".tmp");
            try (InputStream in = getAssets().open("proot/" + name);
                 FileOutputStream os = new FileOutputStream(tmp)) {
                copy(in, os);
            }
            if (!tmp.renameTo(out)) {
                try (InputStream in = new java.io.FileInputStream(tmp);
                     FileOutputStream os = new FileOutputStream(out)) {
                    copy(in, os);
                }
                if (!tmp.delete()) tmp.deleteOnExit();
            }
            out.setReadable(true, true);
            out.setExecutable(true, true);
        }
    }

    private long assetSize(String asset) {
        try (InputStream in = getAssets().open(asset)) {
            long total = 0;
            byte[] buffer = new byte[16 * 1024];
            int read;
            while ((read = in.read(buffer)) > 0) {
                total += read;
            }
            return total;
        } catch (Exception e) {
            return -1;
        }
    }

    private void startServer(File files, File rootfs, File prootDir) throws Exception {
        File loader = new File(prootDir, "loader");
        File proot = new File(prootDir, "proot");
        File tmp = new File(files, "proot-tmp");
        if (!tmp.isDirectory()) tmp.mkdirs();
        File log = new File(files, "server.log");
        if (log.exists()) log.delete();

        ProcessBuilder pb = new ProcessBuilder(
                proot.getAbsolutePath(),
                "--link2symlink",
                "-0",
                "-r", rootfs.getAbsolutePath(),
                "-b", "/dev",
                "-b", "/proc",
                "-b", "/sys",
                "-b", "/proc/self/fd",
                "-w", "/opt/flyai",
                "/usr/bin/env", "-i",
                "HOME=/root",
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "TERM=xterm",
                "PYTHONUNBUFFERED=1",
                "LANG=C.UTF-8",
                "/bin/sh", "/opt/flyai/start.sh"
        );
        pb.directory(files);
        pb.redirectErrorStream(true);
        pb.redirectOutput(ProcessBuilder.Redirect.appendTo(log));
        pb.environment().put("PROOT_LOADER", loader.getAbsolutePath());
        pb.environment().put("PROOT_TMP_DIR", tmp.getAbsolutePath());
        pb.environment().put("PROOT_NO_SECCOMP", "1");
        pb.environment().put("LD_LIBRARY_PATH", prootDir.getAbsolutePath());
        server = pb.start();

        new Thread(() -> {
            try {
                int code = server.waitFor();
                Log.w(TAG, "server exited: " + code);
                notify("FlyAI 已停止（退出码 " + code + "）");
            } catch (InterruptedException ignored) {
            }
        }, "flyai-watch").start();
    }

    private void readLogTail(File files) {
        File log = new File(files, "server.log");
        // 只用于调试：日志留在 filesDir，MainActivity 失败时会展示末尾内容
    }

    private static void copy(InputStream in, OutputStream out) throws Exception {
        byte[] buffer = new byte[1 << 16];
        int n;
        while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
    }

    private static void deleteRecursive(File file) {
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) for (File child : children) deleteRecursive(child);
        }
        //noinspection ResultOfMethodCallIgnored
        file.delete();
    }

    private static void deleteIfExists(File file) {
        if (file.exists()) {
            //noinspection ResultOfMethodCallIgnored
            file.delete();
        }
    }

    private static String readMarker(File marker) {
        if (!marker.isFile()) return "";
        try (RandomAccessFile raf = new RandomAccessFile(marker, "r")) {
            byte[] data = new byte[(int) raf.length()];
            raf.readFully(data);
            return new String(data, "UTF-8").trim();
        } catch (Exception e) {
            return "";
        }
    }

    private static void writeMarker(File marker, String value) {
        try {
            File parent = marker.getParentFile();
            if (parent != null && !parent.isDirectory()) parent.mkdirs();
            try (FileOutputStream out = new FileOutputStream(marker)) {
                out.write(value.getBytes("UTF-8"));
            }
        } catch (Exception e) {
            throw new RuntimeException("无法写入安装标记", e);
        }
    }

    static String tail(File log, int maxBytes) {
        if (!log.isFile()) return "";
        try (RandomAccessFile raf = new RandomAccessFile(log, "r")) {
            long length = raf.length();
            long start = Math.max(0, length - maxBytes);
            raf.seek(start);
            byte[] data = new byte[(int) (length - start)];
            raf.readFully(data);
            return new String(data, "UTF-8");
        } catch (Exception e) {
            return "";
        }
    }

    @Override
    public void onDestroy() {
        stopServer();
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        super.onDestroy();
    }
}
