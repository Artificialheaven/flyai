package top.eastcloud.flyai.poc;

import android.Manifest;
import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.ContentResolver;
import android.content.ContentUris;
import android.content.ContentValues;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Message;
import android.provider.MediaStore;
import android.provider.Settings;
import android.text.Spannable;
import android.text.SpannableStringBuilder;
import android.text.style.ForegroundColorSpan;
import android.util.Base64;
import android.util.Size;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.view.inputmethod.InputMethodManager;
import android.webkit.JavascriptInterface;
import android.webkit.MimeTypeMap;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URL;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** 本地一体化 POC：前台服务拉起 rootfs 里的 FlyAI，WebView 连 127.0.0.1:8000。 */
public class MainActivity extends Activity {
    private static final int PORT = 8000;
    private static final int FILE_CHOOSER = 1001;
    private static final long CONSOLE_INTERVAL = 700;
    /** 页面返回处理超过该时间仍未回调就按未消费处理，避免 WebView 卡死时退不出去 */
    private static final long BACK_JS_TIMEOUT = 700;
    /** 应用内相册选图 */
    private static final int GALLERY_PERMISSION_REQUEST = 2002;
    private static final int GALLERY_THUMB_SIZE = 240;
    private static final int GALLERY_PAGE_MAX = 100;
    private static final int GALLERY_MAX_BYTES = 25 * 1024 * 1024;

    /** 后台回答完成提醒（对应标准壳的 FlyNotify 插件） */
    private static final String REPLY_CHANNEL = "flyai_reply";
    private static final int REPLY_NOTIFY_ID = 2001;
    private static final long WATCH_INTERVAL_MS = 4000;
    private static final long WATCH_MAX_MS = 10 * 60 * 1000;
    private static final Pattern STATUS_RE =
            Pattern.compile("\"status\"\\s*:\\s*\"([^\"]+)\"");

    private FrameLayout rootView;
    private WebView webView;
    private LinearLayout splash;
    private ProgressBar spinner;
    private TextView status;
    private TextView hint;
    private TextView console;
    private ScrollView consoleScroll;
    private Button retry;
    private ValueCallback<Uri[]> fileCallback;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean polling;
    private int backSeq;
    /** 后台完成提醒：只保留最新一个 run；切前台不取消，完成时按前后台决定是否通知 */
    private volatile String watchRunId;
    private Thread watchThread;
    private volatile boolean appForeground = true;
    /** 注入主页面的随机 token；卡片 iframe 读不到主页面闭包，不能调用更新桥。 */
    private final String updateToken = UUID.randomUUID().toString();

    private final BroadcastReceiver updateReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String json = intent.getStringExtra("json");
            if (json != null) {
                evaluateJs("window.__flyaiPocEvent&&window.__flyaiPocEvent(" + json + ")");
            }
        }
    };

    private final Runnable consoleTick = new Runnable() {
        @Override
        public void run() {
            if (!polling) return;
            refreshConsole();
            handler.postDelayed(this, CONSOLE_INTERVAL);
        }
    };

    private final BroadcastReceiver statusReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            String text = intent.getStringExtra("text");
            if (text != null && status != null && splash.getVisibility() == View.VISIBLE) {
                status.setText(text);
                refreshConsole();
            }
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().setStatusBarColor(Color.parseColor("#0F1115"));
        getWindow().setNavigationBarColor(Color.parseColor("#0F1115"));

        FrameLayout root = new FrameLayout(this);
        rootView = root;
        root.setBackgroundColor(Color.parseColor("#0F1115"));

        webView = new WebView(this);
        webView.setVisibility(View.INVISIBLE);
        webView.setBackgroundColor(Color.WHITE);
        root.addView(webView, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        splash = buildSplash();
        root.addView(splash, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
        applySystemInsets(root);

        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 2001);
        }
        configureWebView();
        startServerService();
        startConsole();
        waitForServer(0);
    }

    /** 首启页：上半品牌/进度，下半实时控制台；失败时同一页给出日志与重试。 */
    private LinearLayout buildSplash() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Color.parseColor("#0F1115"));
        page.setFitsSystemWindows(true);
        page.setPadding(dp(24), dp(24), dp(24), dp(16));

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.VERTICAL);
        header.setGravity(Gravity.CENTER);
        page.addView(header, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        TextView brand = new TextView(this);
        brand.setText("FlyAI");
        brand.setTextColor(Color.WHITE);
        brand.setTextSize(32);
        brand.setTypeface(Typeface.DEFAULT_BOLD);
        brand.setGravity(Gravity.CENTER);
        header.addView(brand);

        TextView sub = new TextView(this);
        sub.setText("本地服务 · 数据保存在本机");
        sub.setTextColor(Color.parseColor("#9AA4B2"));
        sub.setTextSize(12);
        sub.setGravity(Gravity.CENTER);
        header.addView(sub, marginTop(dp(6)));

        spinner = new ProgressBar(this);
        header.addView(spinner, marginTop(dp(28)));
        spinner.getLayoutParams().width = dp(40);
        spinner.getLayoutParams().height = dp(40);

        status = new TextView(this);
        status.setText("正在启动本地服务…");
        status.setTextColor(Color.parseColor("#E6EAF0"));
        status.setTextSize(14);
        status.setGravity(Gravity.CENTER);
        header.addView(status, marginTop(dp(14)));

        hint = new TextView(this);
        hint.setText("首次启动需解压运行环境（约 350MB），请保持前台等待");
        hint.setTextColor(Color.parseColor("#7C8794"));
        hint.setTextSize(11);
        hint.setGravity(Gravity.CENTER);
        header.addView(hint, marginTop(dp(8)));

        retry = new Button(this);
        retry.setText("重试启动");
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(v -> {
            retry.setVisibility(View.GONE);
            spinner.setVisibility(View.VISIBLE);
            status.setTextColor(Color.parseColor("#E6EAF0"));
            status.setText("正在重新启动本地服务…");
            startServerService();
            waitForServer(0);
        });
        header.addView(retry, marginTop(dp(14)));

        // ---- 实时控制台 ----
        LinearLayout consoleBox = new LinearLayout(this);
        consoleBox.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.parseColor("#151A21"));
        bg.setCornerRadius(dp(10));
        bg.setStroke(dp(1), Color.parseColor("#232A34"));
        consoleBox.setBackground(bg);
        consoleBox.setPadding(dp(12), dp(10), dp(12), dp(8));

        LinearLayout titleRow = new LinearLayout(this);
        titleRow.setOrientation(LinearLayout.HORIZONTAL);
        titleRow.setGravity(Gravity.CENTER_VERTICAL);
        TextView dot = new TextView(this);
        dot.setText("●");
        dot.setTextColor(Color.parseColor("#4ADE80"));
        dot.setTextSize(8);
        titleRow.addView(dot);
        TextView title = new TextView(this);
        title.setText("  启动控制台 · 实时");
        title.setTextColor(Color.parseColor("#8B96A5"));
        title.setTextSize(10);
        titleRow.addView(title, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        TextView copy = new TextView(this);
        copy.setText("复制");
        copy.setTextColor(Color.parseColor("#7DD3FC"));
        copy.setTextSize(10);
        copy.setPadding(dp(10), dp(2), dp(4), dp(2));
        copy.setOnClickListener(v -> {
            ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            cm.setPrimaryClip(ClipData.newPlainText("FlyAI 启动日志", console.getText().toString()));
            android.widget.Toast.makeText(this, "日志已复制", android.widget.Toast.LENGTH_SHORT).show();
        });
        titleRow.addView(copy);
        consoleBox.addView(titleRow);

        consoleScroll = new ScrollView(this);
        consoleScroll.setVerticalScrollBarEnabled(true);
        console = new TextView(this);
        console.setTextColor(Color.parseColor("#9FE3B0"));
        console.setTextSize(9);
        console.setTypeface(Typeface.MONOSPACE);
        console.setText("等待服务输出…");
        consoleScroll.addView(console, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        LinearLayout.LayoutParams scrollLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(190));
        scrollLp.topMargin = dp(6);
        consoleBox.addView(consoleScroll, scrollLp);

        page.addView(consoleBox, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return page;
    }

    private LinearLayout.LayoutParams marginTop(int px) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = px;
        lp.gravity = Gravity.CENTER_HORIZONTAL;
        return lp;
    }

    private void startConsole() {
        polling = true;
        handler.post(consoleTick);
    }

    private void stopConsole() {
        polling = false;
        handler.removeCallbacks(consoleTick);
    }

    private void refreshConsole() {
        File files = getFilesDir();
        StringBuilder text = new StringBuilder();
        String boot = readText(new File(files, "boot.log"), 4000);
        if (!boot.isEmpty()) text.append(boot);
        String server = BootService.tail(new File(files, "server.log"), 4000);
        if (!server.isEmpty()) {
            if (text.length() > 0 && !text.toString().endsWith("\n")) text.append('\n');
            text.append("---- server.log ----\n").append(server);
        }
        if (text.length() == 0) return;
        String next = text.toString();
        if (!next.equals(console.getText().toString())) {
            console.setText(colorizeLog(next));
            consoleScroll.post(() -> consoleScroll.fullScroll(View.FOCUS_DOWN));
        }
    }

    private static String readText(File file, int maxBytes) {
        if (!file.isFile()) return "";
        try {
            if (file.length() > maxBytes) {
                return BootService.tail(file, maxBytes);
            }
            byte[] data = new byte[(int) file.length()];
            try (java.io.FileInputStream in = new java.io.FileInputStream(file)) {
                int off = 0;
                while (off < data.length) {
                    int n = in.read(data, off, data.length - off);
                    if (n < 0) break;
                    off += n;
                }
                return new String(data, 0, off, "UTF-8");
            }
        } catch (Exception e) {
            return "";
        }
    }

    /** 按行着色：错误红、阶段灰、正常输出绿。 */
    private Spannable colorizeLog(String text) {
        SpannableStringBuilder out = new SpannableStringBuilder();
        for (String line : text.split("\n", -1)) {
            int start = out.length();
            out.append(line).append('\n');
            String lower = line.toLowerCase();
            int color;
            if (lower.contains("失败") || lower.contains("error") || lower.contains("traceback")
                    || lower.contains("exception") || lower.contains("fatal")) {
                color = Color.parseColor("#FF9B9B");
            } else if (line.startsWith("[") || line.startsWith("----")) {
                color = Color.parseColor("#7C8794");
            } else {
                color = Color.parseColor("#9FE3B0");
            }
            out.setSpan(new ForegroundColorSpan(color), start, out.length(),
                    Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        }
        return out;
    }

    /** 全面屏：内容避开状态栏/导航栏与挖孔，背景仍铺满。 */
    private void applySystemInsets(View root) {
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            int top;
            int bottom;
            int left;
            int right;
            if (Build.VERSION.SDK_INT >= 30) {
                Insets bars = insets.getInsets(
                        WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                top = bars.top;
                bottom = bars.bottom;
                left = bars.left;
                right = bars.right;
            } else {
                top = insets.getSystemWindowInsetTop();
                bottom = insets.getSystemWindowInsetBottom();
                left = insets.getSystemWindowInsetLeft();
                right = insets.getSystemWindowInsetRight();
            }
            view.setPadding(left, top, right, bottom);
            return insets;
        });
        root.requestApplyInsets();
    }

    /** 进入 Web 后切回浅色系统栏，避免白页面配黑色状态栏。 */
    private void applyLightSystemBars() {
        if (rootView != null) rootView.setBackgroundColor(Color.WHITE);
        getWindow().setStatusBarColor(Color.WHITE);
        getWindow().setNavigationBarColor(Color.WHITE);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().getInsetsController().setSystemBarsAppearance(
                    WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                            | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
                    WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                            | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
        } else if (Build.VERSION.SDK_INT >= 23) {
            getWindow().getDecorView().setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
        }
    }

    private void configureWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setSupportMultipleWindows(true);
        if (Build.VERSION.SDK_INT >= 21) {
            settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
        if (Build.VERSION.SDK_INT >= 19) {
            WebView.setWebContentsDebuggingEnabled(true);
        }
        webView.addJavascriptInterface(new UpdateBridge(), "FlyAIPoc");
        // 兜底：页面里直接 <a download> 的 http(s) 资源（WebView 默认不处理）
        webView.setDownloadListener((url, userAgent, contentDisposition, mimetype, contentLength) -> {
            if (url == null || (!url.startsWith("http://") && !url.startsWith("https://"))) {
                return;
            }
            final String name = URLUtil.guessFileName(url, contentDisposition, mimetype);
            final boolean image = mimetype != null && mimetype.startsWith("image/");
            new Thread(() -> {
                String result = downloadToStorage(url, name);
                boolean ok = false;
                try {
                    ok = new JSONObject(result).optBoolean("ok");
                } catch (Exception ignored) {
                }
                final boolean saved = ok;
                runOnUiThread(() -> android.widget.Toast.makeText(MainActivity.this,
                        saved ? (image ? "已保存到相册" : "已保存到下载目录") : "下载失败",
                        android.widget.Toast.LENGTH_SHORT).show());
            }, "flyai-download").start();
        });
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return openExternal(request.getUrl());
            }

            @SuppressWarnings("deprecation")
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return openExternal(Uri.parse(url));
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                injectPocBridge();
            }
        });
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent intent = new Intent(Intent.ACTION_GET_CONTENT);
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                intent.setType("*/*");
                try {
                    startActivityForResult(Intent.createChooser(intent, "选择角色卡文件"), FILE_CHOOSER);
                } catch (Exception e) {
                    fileCallback = null;
                    return false;
                }
                return true;
            }

            /** target=_blank（如 GitHub 链接）不新建窗口，交给系统浏览器 */
            @Override
            public boolean onCreateWindow(WebView view, boolean isDialog, boolean isUserGesture,
                                          Message resultMsg) {
                WebView popup = new WebView(MainActivity.this);
                popup.setWebViewClient(new WebViewClient() {
                    @Override
                    public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) {
                        boolean handled = openExternal(request.getUrl());
                        if (handled) v.destroy();
                        return handled;
                    }

                    @SuppressWarnings("deprecation")
                    @Override
                    public boolean shouldOverrideUrlLoading(WebView v, String url) {
                        boolean handled = openExternal(Uri.parse(url));
                        if (handled) v.destroy();
                        return handled;
                    }
                });
                WebView.WebViewTransport transport = (WebView.WebViewTransport) resultMsg.obj;
                transport.setWebView(popup);
                resultMsg.sendToTarget();
                return true;
            }
        });
    }

    private void evaluateJs(String js) {
        if (webView == null) return;
        webView.evaluateJavascript(js, null);
    }

    private void injectPocBridge() {
        try {
            JSONObject info = new JSONObject(pocStateJson());
            info.put("token", updateToken);
            evaluateJs("window.__flyaiPoc=" + info.toString()
                    + ";window.dispatchEvent(new Event('flyai-poc-ready'));");
        } catch (Exception ignored) {
        }
    }

    private String pocStateJson() {
        JSONObject out = new JSONObject();
        try {
            out.put("supported", true);
            try {
                android.content.pm.PackageInfo info = getPackageManager()
                        .getPackageInfo(getPackageName(), 0);
                out.put("versionCode", info.versionCode);
                out.put("versionName", info.versionName == null ? "" : info.versionName);
            } catch (Exception ignored) {
            }
            JSONObject state = OtaState.read(getFilesDir());
            out.put("baseRev", state.optString("baseRev", ""));
            out.put("codeRev", state.optString("codeRev", ""));
            out.put("overlayVersion", state.optLong("overlayVersion", 0));
            out.put("pending", false);
            File pending = new File(getFilesDir(), "update/pending");
            if (new File(pending, "ready").isFile()) {
                try {
                    JSONObject m = OtaManifest.read(new File(pending, "manifest.json"));
                    JSONObject overlay = m.optJSONObject("overlay");
                    out.put("pending", true);
                    out.put("pendingVersion", m.optString("version", ""));
                    out.put("pendingOverlayVersion", m.optLong("overlay_version", 0));
                    out.put("pendingSize", overlay == null ? 0 : overlay.optLong("size", 0));
                } catch (Exception ignored) {
                }
            }
        } catch (Exception e) {
            try {
                out.put("supported", true);
                out.put("error", e.getMessage());
            } catch (Exception ignored) {
            }
        }
        return out.toString();
    }

    private String startUpdateAction(String action, String token) {
        if (!updateToken.equals(token)) return "{\"ok\":false,\"error\":\"forbidden\"}";
        try {
            Intent intent = new Intent(this, UpdateService.class);
            intent.setAction(action);
            if (Build.VERSION.SDK_INT >= 26) {
                startForegroundService(intent);
            } else {
                startService(intent);
            }
            return "{\"ok\":true}";
        } catch (Exception e) {
            return errorJson(e.getMessage());
        }
    }

    private static String errorJson(String message) {
        JSONObject out = new JSONObject();
        try {
            out.put("ok", false);
            out.put("error", message == null ? "unknown" : message);
        } catch (Exception ignored) {
        }
        return out.toString();
    }

    /**
     * 停止 BootService（杀死 Python），然后退出界面，由用户手动重新打开。
     * 下次启动时 BootService 会先应用 pending overlay 再拉起服务，避免
     * 重启与 WebView 重载/端口就绪之间的竞态（白屏）。
     */
    private void restartForUpdate() {
        runOnUiThread(() -> {
            android.widget.Toast.makeText(this, "更新已就绪，请重新打开应用",
                    android.widget.Toast.LENGTH_LONG).show();
            evaluateJs("window.__flyaiPocEvent&&window.__flyaiPocEvent({state:'applying'})");
        });
        new Thread(() -> {
            stopService(new Intent(this, BootService.class));
            for (int i = 0; i < 40 && probe(); i++) {
                try {
                    Thread.sleep(250);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
            runOnUiThread(() -> {
                if (webView != null) {
                    webView.stopLoading();
                    webView.loadUrl("about:blank");
                }
                finishAndRemoveTask();
            });
        }, "flyai-apply-update").start();
    }

    private final class UpdateBridge {
        @JavascriptInterface
        public String getState(String token) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            return pocStateJson();
        }

        @JavascriptInterface
        public String check(String token) {
            return startUpdateAction(UpdateService.ACTION_CHECK, token);
        }

        @JavascriptInterface
        public String downloadOverlay(String token) {
            return startUpdateAction(UpdateService.ACTION_DOWNLOAD_OVERLAY, token);
        }

        @JavascriptInterface
        public String downloadFull(String token) {
            return startUpdateAction(UpdateService.ACTION_DOWNLOAD_FULL, token);
        }

        @JavascriptInterface
        public String cancel(String token) {
            return startUpdateAction(UpdateService.ACTION_CANCEL, token);
        }

        @JavascriptInterface
        public String applyOverlay(String token) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            File ready = new File(getFilesDir(), "update/pending/ready");
            if (!ready.isFile()) return errorJson("没有已下载的更新");
            restartForUpdate();
            return "{\"ok\":true}";
        }

        @JavascriptInterface
        public String markReady(String token) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            OtaState.markReady(getFilesDir());
            return "{\"ok\":true}";
        }

        @JavascriptInterface
        public String openInstallSettings(String token) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            if (Build.VERSION.SDK_INT >= 26) {
                Intent intent = new Intent(
                        android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:" + getPackageName()));
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
            }
            return "{\"ok\":true}";
        }

        @JavascriptInterface
        public String getKeepAlive(String token) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            boolean enabled = keepAlivePrefs()
                    .getBoolean(BootService.KEY_KEEPALIVE_OVERLAY, false);
            JSONObject out = new JSONObject();
            try {
                out.put("ok", true);
                out.put("enabled", enabled);
                out.put("permission", hasOverlayPermission());
            } catch (Exception ignored) {
            }
            return out.toString();
        }

        @JavascriptInterface
        public String setKeepAlive(String token, String value) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            boolean enabled = "1".equals(value) || "true".equalsIgnoreCase(value);
            if (enabled && !hasOverlayPermission()) {
                return "{\"ok\":false,\"error\":\"permission\",\"permission\":false}";
            }
            keepAlivePrefs().edit()
                    .putBoolean(BootService.KEY_KEEPALIVE_OVERLAY, enabled)
                    .putBoolean(BootService.KEY_KEEPALIVE_PENDING, false)
                    .apply();
            applyKeepAlive(enabled);
            return "{\"ok\":true,\"enabled\":" + enabled + ",\"permission\":true}";
        }

        @JavascriptInterface
        public String requestOverlayPermission(String token) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            if (hasOverlayPermission()) return "{\"ok\":true,\"permission\":true}";
            // 记下「用户想开」：WebView 不一定会派发 visibilitychange，
            // 授权页返回时由 onResume 兜底补开
            keepAlivePrefs().edit()
                    .putBoolean(BootService.KEY_KEEPALIVE_PENDING, true).apply();
            openOverlaySettings();
            return "{\"ok\":true,\"permission\":false}";
        }

        /** 下载远程资源（图片/音频）：裸 WebView 不接管 <a download>，由原生落盘 */
        @JavascriptInterface
        public String saveUrl(String token, String url, String name) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            return downloadToStorage(url, name);
        }

        /** 保存前端生成的 Blob（导出 JSON / 图片转码后的数据） */
        @JavascriptInterface
        public String saveBase64(String token, String data, String name, String mime) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            try {
                byte[] bytes = Base64.decode(data, Base64.DEFAULT);
                try (InputStream in = new java.io.ByteArrayInputStream(bytes)) {
                    return saveStream(in, name, mime);
                }
            } catch (Exception e) {
                return errorJson(e.getMessage());
            }
        }

        /** 后台回答完成提醒：native 线程轮询 run 状态（对应标准壳的 FlyNotify） */
        @JavascriptInterface
        public String watchRun(String token, String runId, String statusUrl,
                               String title, String body, String iconUrl) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            if (runId == null || runId.isEmpty()
                    || statusUrl == null || statusUrl.isEmpty()) {
                return errorJson("missing runId/statusUrl");
            }
            startRunWatch(runId, statusUrl, token, title, body, iconUrl);
            return "{\"ok\":true}";
        }

        @JavascriptInterface
        public String cancelWatch(String token) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            stopRunWatch();
            return "{\"ok\":true}";
        }

        /** 系统状态栏/导航栏颜色随页面主题同步（对应 StatusBar 插件） */
        @JavascriptInterface
        public String setStatusBar(String token, String color, String dark) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            final int parsed;
            try {
                parsed = Color.parseColor(color);
            } catch (Exception e) {
                return errorJson("bad color");
            }
            final boolean darkTheme = "1".equals(dark) || "true".equalsIgnoreCase(dark);
            runOnUiThread(() -> {
                try {
                    getWindow().setStatusBarColor(parsed);
                    getWindow().setNavigationBarColor(parsed);
                    int lightBars = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                            | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                    if (Build.VERSION.SDK_INT >= 30) {
                        getWindow().getInsetsController().setSystemBarsAppearance(
                                darkTheme ? 0 : lightBars, lightBars);
                    } else if (Build.VERSION.SDK_INT >= 23) {
                        getWindow().getDecorView().setSystemUiVisibility(
                                darkTheme ? 0 : View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR);
                    }
                } catch (Exception ignored) {
                }
            });
            return "{\"ok\":true}";
        }

        /** 相册选图：权限状态 */
        @JavascriptInterface
        public String galleryPermission(String token) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            JSONObject out = new JSONObject();
            try {
                out.put("ok", true);
                out.put("granted", hasGalleryPermission());
                out.put("sdk", Build.VERSION.SDK_INT);
            } catch (Exception ignored) {
            }
            return out.toString();
        }

        @JavascriptInterface
        public String requestGalleryPermission(String token) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            requestGalleryPermissionImpl();
            return "{\"ok\":true}";
        }

        /** 分页列出系统相册图片（缩略图为 data URI） */
        @JavascriptInterface
        public String listGallery(String token, String offsetRaw, String limitRaw) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            return listGalleryImages(offsetRaw, limitRaw);
        }

        /** 读取相册原图，返回 base64（网页侧转成 File 走正常附件流程） */
        @JavascriptInterface
        public String readGalleryImage(String token, String idRaw) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            return readGalleryImageBytes(idRaw);
        }

        @JavascriptInterface
        public String openAppSettings(String token) {
            if (!updateToken.equals(token)) return errorJson("forbidden");
            try {
                Intent intent = new Intent(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.parse("package:" + getPackageName()));
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
                return "{\"ok\":true}";
            } catch (Exception e) {
                return errorJson(e.getMessage());
            }
        }
    }

    private boolean hasGalleryPermission() {
        if (Build.VERSION.SDK_INT < 23) return true;
        if (checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED) {
            return true;
        }
        return Build.VERSION.SDK_INT >= 33
                && checkSelfPermission(Manifest.permission.READ_MEDIA_IMAGES)
                == PackageManager.PERMISSION_GRANTED;
    }

    private void requestGalleryPermissionImpl() {
        if (Build.VERSION.SDK_INT < 23 || hasGalleryPermission()) return;
        // targetSdk 28：Android 13+ 仍走 READ_EXTERNAL_STORAGE 兼容授权
        runOnUiThread(() -> {
            try {
                requestPermissions(
                        new String[]{Manifest.permission.READ_EXTERNAL_STORAGE},
                        GALLERY_PERMISSION_REQUEST);
            } catch (Exception ignored) {
            }
        });
    }

    private static int parseInt(String raw, int fallback) {
        try {
            return Integer.parseInt((raw == null ? "" : raw).trim());
        } catch (Exception e) {
            return fallback;
        }
    }

    private String listGalleryImages(String offsetRaw, String limitRaw) {
        if (!hasGalleryPermission()) {
            return "{\"ok\":false,\"error\":\"permission\"}";
        }
        int offset = Math.max(0, parseInt(offsetRaw, 0));
        int limit = Math.max(1, Math.min(GALLERY_PAGE_MAX, parseInt(limitRaw, 60)));
        ContentResolver resolver = getContentResolver();
        Uri collection = MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
        String[] projection = {
                MediaStore.Images.Media._ID,
                MediaStore.Images.Media.DISPLAY_NAME,
                MediaStore.Images.Media.MIME_TYPE,
                MediaStore.Images.Media.DATE_ADDED,
        };
        Cursor cursor = null;
        JSONArray images = new JSONArray();
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                Bundle args = new Bundle();
                args.putInt(ContentResolver.QUERY_ARG_LIMIT, limit);
                args.putInt(ContentResolver.QUERY_ARG_OFFSET, offset);
                args.putStringArray(ContentResolver.QUERY_ARG_SORT_COLUMNS,
                        new String[]{MediaStore.Images.Media.DATE_ADDED});
                args.putInt(ContentResolver.QUERY_ARG_SORT_DIRECTION,
                        ContentResolver.QUERY_SORT_DIRECTION_DESCENDING);
                cursor = resolver.query(collection, projection, args, null);
            } else {
                cursor = resolver.query(collection, projection, null, null,
                        MediaStore.Images.Media.DATE_ADDED + " DESC LIMIT "
                                + limit + " OFFSET " + offset);
            }
            if (cursor != null) {
                while (cursor.moveToNext()) {
                    long id = cursor.getLong(0);
                    String name = cursor.getString(1);
                    String mime = cursor.getString(2);
                    long date = cursor.getLong(3);
                    String thumb = encodeGalleryThumb(
                            resolver, ContentUris.withAppendedId(collection, id));
                    JSONObject item = new JSONObject();
                    item.put("id", String.valueOf(id));
                    item.put("name", name == null || name.isEmpty() ? "image" : name);
                    item.put("mime", mime == null ? "" : mime);
                    item.put("date", date);
                    if (thumb != null) item.put("thumb", thumb);
                    images.put(item);
                }
            }
            JSONObject out = new JSONObject();
            out.put("ok", true);
            out.put("images", images);
            out.put("hasMore", images.length() >= limit);
            return out.toString();
        } catch (Exception e) {
            return errorJson(e.getMessage());
        } finally {
            if (cursor != null) cursor.close();
        }
    }

    /** 生成 240px JPEG 缩略图 data URI；失败返回 null（前端显示占位） */
    private String encodeGalleryThumb(ContentResolver resolver, Uri uri) {
        Bitmap bitmap = null;
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                bitmap = resolver.loadThumbnail(
                        uri, new Size(GALLERY_THUMB_SIZE, GALLERY_THUMB_SIZE), null);
            } else {
                BitmapFactory.Options bounds = new BitmapFactory.Options();
                bounds.inJustDecodeBounds = true;
                try (InputStream in = resolver.openInputStream(uri)) {
                    BitmapFactory.decodeStream(in, null, bounds);
                }
                if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null;
                int sample = 1;
                int longest = Math.max(bounds.outWidth, bounds.outHeight);
                while (longest / (sample * 2) >= GALLERY_THUMB_SIZE) sample *= 2;
                BitmapFactory.Options options = new BitmapFactory.Options();
                options.inSampleSize = sample;
                try (InputStream in = resolver.openInputStream(uri)) {
                    bitmap = BitmapFactory.decodeStream(in, null, options);
                }
            }
            if (bitmap == null) return null;
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            bitmap.compress(Bitmap.CompressFormat.JPEG, 72, buffer);
            String prefix = "data:" + "image/jpeg" + ";" + "base64,";
            return prefix + Base64.encodeToString(buffer.toByteArray(), Base64.NO_WRAP);
        } catch (Exception e) {
            return null;
        } finally {
            if (bitmap != null) bitmap.recycle();
        }
    }

    private String readGalleryImageBytes(String idRaw) {
        if (!hasGalleryPermission()) {
            return "{\"ok\":false,\"error\":\"permission\"}";
        }
        long id;
        try {
            id = Long.parseLong((idRaw == null ? "" : idRaw).trim());
        } catch (Exception e) {
            return errorJson("bad id");
        }
        ContentResolver resolver = getContentResolver();
        Uri uri = ContentUris.withAppendedId(
                MediaStore.Images.Media.EXTERNAL_CONTENT_URI, id);
        String name = "image_" + id;
        String mime = "";
        try (Cursor cursor = resolver.query(uri,
                new String[]{
                        MediaStore.Images.Media.DISPLAY_NAME,
                        MediaStore.Images.Media.MIME_TYPE,
                }, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                String value = cursor.getString(0);
                if (value != null && !value.isEmpty()) name = value;
                String type = cursor.getString(1);
                if (type != null) mime = type;
            }
        } catch (Exception ignored) {
        }
        try (InputStream in = resolver.openInputStream(uri)) {
            if (in == null) return errorJson("open failed");
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[64 * 1024];
            int read;
            while ((read = in.read(chunk)) != -1) {
                buffer.write(chunk, 0, read);
                if (buffer.size() > GALLERY_MAX_BYTES) {
                    return errorJson("image too large");
                }
            }
            JSONObject out = new JSONObject();
            out.put("ok", true);
            out.put("name", name);
            out.put("mime", mime);
            out.put("data", Base64.encodeToString(buffer.toByteArray(), Base64.NO_WRAP));
            return out.toString();
        } catch (Exception e) {
            return errorJson(e.getMessage());
        }
    }

    private synchronized void startRunWatch(final String runId, final String statusUrl,
                                            final String token, final String title,
                                            final String body, final String iconUrl) {
        stopRunWatch();
        watchRunId = runId;
        Thread thread = new Thread(() -> {
            long deadline = System.currentTimeMillis() + WATCH_MAX_MS;
            while (runId.equals(watchRunId) && System.currentTimeMillis() < deadline) {
                try {
                    Thread.sleep(WATCH_INTERVAL_MS);
                } catch (InterruptedException e) {
                    return;
                }
                if (!runId.equals(watchRunId)) return;
                String status = pollRunStatus(statusUrl, token);
                if (status == null) continue; // 网络抖动：下轮再试
                if ("not-found".equals(status) || "error".equals(status)
                        || "cancelled".equals(status)) {
                    return;
                }
                if ("done".equals(status)) {
                    if (!runId.equals(watchRunId)) return;
                    watchRunId = null;
                    // 前台完成时用户能直接看到，不打扰
                    if (!appForeground) postReplyNotification(title, body, iconUrl);
                    return;
                }
            }
        }, "flyai-run-watch");
        thread.setDaemon(true);
        watchThread = thread;
        thread.start();
    }

    private synchronized void stopRunWatch() {
        watchRunId = null;
        if (watchThread != null) {
            watchThread.interrupt();
            watchThread = null;
        }
    }

    /** 轮询 run 状态：running / done / error / cancelled / not-found / null(网络错误) */
    private String pollRunStatus(String statusUrl, String token) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(statusUrl).openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(5000);
            conn.setRequestProperty("User-Agent", "FlyAI-POC");
            if (token != null && !token.isEmpty()) {
                conn.setRequestProperty("Authorization", "Bearer " + token);
            }
            int code = conn.getResponseCode();
            if (code == 404) return "not-found";
            if (code != HttpURLConnection.HTTP_OK) return null;
            String resp;
            try (InputStream is = conn.getInputStream();
                 ByteArrayOutputStream bos = new ByteArrayOutputStream()) {
                byte[] buf = new byte[2048];
                int read;
                while ((read = is.read(buf)) != -1) {
                    bos.write(buf, 0, read);
                }
                resp = bos.toString("UTF-8");
            }
            Matcher m = STATUS_RE.matcher(resp);
            return m.find() ? m.group(1) : null;
        } catch (Exception e) {
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private void postReplyNotification(String title, String body, String iconUrl) {
        try {
            ensureReplyChannel();
            Intent open = new Intent(this, MainActivity.class);
            int flags = PendingIntent.FLAG_UPDATE_CURRENT;
            if (Build.VERSION.SDK_INT >= 23) flags |= PendingIntent.FLAG_IMMUTABLE;
            PendingIntent pending = PendingIntent.getActivity(this, 0, open, flags);
            Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                    ? new Notification.Builder(this, REPLY_CHANNEL)
                    : new Notification.Builder(this);
            builder.setContentTitle(title == null || title.isEmpty() ? "FlyAI" : title)
                    .setContentText(body == null || body.isEmpty() ? "回答已完成，点击查看" : body)
                    .setSmallIcon(android.R.drawable.stat_notify_chat)
                    .setContentIntent(pending)
                    .setAutoCancel(true);
            if (Build.VERSION.SDK_INT < 26) {
                builder.setPriority(Notification.PRIORITY_DEFAULT);
            }
            Bitmap icon = loadNotificationIcon(iconUrl);
            if (icon != null) {
                builder.setLargeIcon(icon);
            }
            NotificationManager manager =
                    (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (manager != null) {
                manager.notify(REPLY_NOTIFY_ID, builder.build());
            }
        } catch (Exception ignored) {
        }
    }

    private void ensureReplyChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager manager =
                (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (manager == null) return;
        NotificationChannel channel = new NotificationChannel(
                REPLY_CHANNEL, "回复提醒", NotificationManager.IMPORTANCE_DEFAULT);
        channel.setDescription("AI 在后台回答完毕时提醒");
        manager.createNotificationChannel(channel);
    }

    /** 通知大图：下载角色头像，失败时退化为默认小图标 */
    private Bitmap loadNotificationIcon(String iconUrl) {
        if (iconUrl == null || iconUrl.isEmpty()) return null;
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(iconUrl).openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(5000);
            conn.setRequestProperty("User-Agent", "FlyAI-POC");
            if (conn.getResponseCode() != HttpURLConnection.HTTP_OK) return null;
            try (InputStream in = conn.getInputStream()) {
                ByteArrayOutputStream bos = new ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                int read;
                while ((read = in.read(buf)) != -1) {
                    bos.write(buf, 0, read);
                    if (bos.size() > 4 * 1024 * 1024) return null;
                }
                byte[] data = bos.toByteArray();
                return BitmapFactory.decodeByteArray(data, 0, data.length);
            }
        } catch (Exception e) {
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private String downloadToStorage(String url, String name) {
        HttpURLConnection conn = null;
        try {
            URL parsed = new URL(url);
            String scheme = parsed.getProtocol();
            if (!"http".equals(scheme) && !"https".equals(scheme)) {
                return errorJson("unsupported scheme");
            }
            conn = (HttpURLConnection) parsed.openConnection();
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(120000);
            conn.setInstanceFollowRedirects(true);
            int code = conn.getResponseCode();
            if (code >= 400) return errorJson("HTTP " + code);
            try (InputStream in = conn.getInputStream()) {
                return saveStream(in, name, conn.getContentType());
            }
        } catch (Exception e) {
            return errorJson(e.getMessage());
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private String saveStream(InputStream in, String rawName, String mime) throws Exception {
        String name = sanitizeFileName(rawName);
        String type = mime == null || mime.trim().isEmpty()
                ? guessMime(name) : mime.split(";")[0].trim();
        if (type.isEmpty()) type = "application/octet-stream";
        // 图片进系统相册（Pictures/FlyAI），其它文件进下载目录
        boolean image = type.startsWith("image/");
        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues values = new ContentValues();
            values.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
            values.put(MediaStore.MediaColumns.MIME_TYPE, type);
            values.put(
                    MediaStore.MediaColumns.RELATIVE_PATH,
                    image
                            ? Environment.DIRECTORY_PICTURES + "/FlyAI"
                            : Environment.DIRECTORY_DOWNLOADS);
            values.put(MediaStore.MediaColumns.IS_PENDING, 1);
            Uri collection = image
                    ? MediaStore.Images.Media.EXTERNAL_CONTENT_URI
                    : MediaStore.Downloads.EXTERNAL_CONTENT_URI;
            Uri uri = getContentResolver().insert(collection, values);
            if (uri == null) throw new IOException("无法创建保存条目");
            try (OutputStream out = getContentResolver().openOutputStream(uri)) {
                if (out == null) throw new IOException("无法写入文件");
                copyStream(in, out);
            }
            values.clear();
            values.put(MediaStore.MediaColumns.IS_PENDING, 0);
            getContentResolver().update(uri, values, null, null);
            JSONObject result = new JSONObject();
            result.put("ok", true);
            result.put("album", image);
            return result.toString();
        }
        // Android 9 及以下：落到应用专属目录（无需存储权限）
        File dir = getExternalFilesDir(
                image ? Environment.DIRECTORY_PICTURES : Environment.DIRECTORY_DOWNLOADS);
        if (dir == null) dir = new File(getFilesDir(), image ? "pictures" : "downloads");
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("无法创建保存目录");
        File target = new File(dir, name);
        if (target.exists()) target = new File(dir, System.currentTimeMillis() + "-" + name);
        try (OutputStream out = new FileOutputStream(target)) {
            copyStream(in, out);
        }
        JSONObject out = new JSONObject();
        out.put("ok", true);
        out.put("album", image);
        out.put("path", target.getAbsolutePath());
        return out.toString();
    }

    private static void copyStream(InputStream in, OutputStream out) throws IOException {
        byte[] buffer = new byte[64 * 1024];
        int read;
        while ((read = in.read(buffer)) != -1) {
            out.write(buffer, 0, read);
        }
    }

    private static String sanitizeFileName(String raw) {
        String name = raw == null ? "" : raw.trim();
        if (name.isEmpty()) name = "download";
        name = name.replaceAll("[\\\\/:*?\"<>|\\r\\n\\t]", "_");
        return name.length() > 120 ? name.substring(name.length() - 120) : name;
    }

    private static String guessMime(String name) {
        int dot = name.lastIndexOf('.');
        if (dot < 0 || dot == name.length() - 1) return "application/octet-stream";
        String ext = name.substring(dot + 1).toLowerCase(java.util.Locale.US);
        String type = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext);
        return type == null ? "application/octet-stream" : type;
    }

    private SharedPreferences keepAlivePrefs() {
        return getSharedPreferences(BootService.PREFS_NAME, MODE_PRIVATE);
    }

    private boolean hasOverlayPermission() {
        return Build.VERSION.SDK_INT < 23 || Settings.canDrawOverlays(this);
    }

    private void applyKeepAlive(boolean enabled) {
        try {
            Intent intent = new Intent(this, BootService.class);
            intent.setAction(BootService.ACTION_APPLY_KEEPALIVE);
            intent.putExtra("enabled", enabled);
            startService(intent);
        } catch (Exception ignored) {
        }
    }

    private void openOverlaySettings() {
        try {
            Intent intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName()));
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(intent);
        } catch (Exception e) {
            try {
                Intent fallback = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION);
                fallback.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(fallback);
            } catch (Exception ignored) {
            }
        }
    }

    /** 从悬浮窗授权页返回后立即应用已保存的开关 */
    @Override
    protected void onResume() {
        super.onResume();
        appForeground = true;
        try {
            SharedPreferences prefs = keepAlivePrefs();
            boolean pending = prefs.getBoolean(BootService.KEY_KEEPALIVE_PENDING, false);
            boolean enabled = prefs.getBoolean(BootService.KEY_KEEPALIVE_OVERLAY, false);
            if ((pending || enabled) && hasOverlayPermission()) {
                if (pending) {
                    prefs.edit()
                            .putBoolean(BootService.KEY_KEEPALIVE_OVERLAY, true)
                            .putBoolean(BootService.KEY_KEEPALIVE_PENDING, false)
                            .apply();
                }
                applyKeepAlive(true);
            }
        } catch (Exception ignored) {
        }
    }

    @Override
    protected void onPause() {
        appForeground = false;
        super.onPause();
    }

    /** 站外 http(s) 链接一律交给系统浏览器，避免把 App 自身导航走 */
    private boolean openExternal(Uri uri) {
        if (uri == null) return false;
        String scheme = uri.getScheme();
        String host = uri.getHost();
        if (!"http".equals(scheme) && !"https".equals(scheme)) return false;
        if ("127.0.0.1".equals(host) || "localhost".equals(host) || "::1".equals(host)) {
            return false;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (Exception e) {
            android.widget.Toast.makeText(this, "未找到可打开链接的应用",
                    android.widget.Toast.LENGTH_SHORT).show();
        }
        return true;
    }

    private void startServerService() {
        Intent service = new Intent(this, BootService.class);
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(service);
        } else {
            startService(service);
        }
    }

    private void waitForServer(int attempt) {
        new Thread(() -> {
            boolean ready = probe();
            handler.post(() -> {
                if (ready) {
                    showWebView();
                } else if (attempt < 600) {
                    int seconds = attempt / 2;
                    status.setText("正在启动本地服务… " + seconds + "s");
                    if (seconds > 20) {
                        hint.setText("首次启动解压较慢属正常现象，请继续等待");
                    }
                    handler.postDelayed(() -> waitForServer(attempt + 1), 500);
                } else {
                    showFailure();
                }
            });
        }, "flyai-probe").start();
    }

    private void showWebView() {
        stopConsole();
        splash.animate().alpha(0f).setDuration(220).withEndAction(() -> {
            splash.setVisibility(View.GONE);
        }).start();
        applyLightSystemBars();
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl("http://127.0.0.1:" + PORT + "/");
    }

    private void showFailure() {
        spinner.setVisibility(View.GONE);
        status.setTextColor(Color.parseColor("#FF8A80"));
        status.setText("本地服务启动失败");
        hint.setText("可先重试；仍失败请把控制台日志发给我");
        retry.setVisibility(View.VISIBLE);
        refreshConsole();
    }

    private boolean probe() {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("127.0.0.1", PORT), 400);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onStart() {
        super.onStart();
        IntentFilter bootFilter = new IntentFilter(BootService.ACTION_STATUS);
        IntentFilter updateFilter = new IntentFilter(UpdateService.ACTION_STATUS);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(statusReceiver, bootFilter, Context.RECEIVER_NOT_EXPORTED);
            registerReceiver(updateReceiver, updateFilter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(statusReceiver, bootFilter);
            registerReceiver(updateReceiver, updateFilter);
        }
    }

    @Override
    protected void onStop() {
        try {
            unregisterReceiver(statusReceiver);
        } catch (Exception ignored) {
        }
        try {
            unregisterReceiver(updateReceiver);
        } catch (Exception ignored) {
        }
        super.onStop();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_CHOOSER) {
            Uri[] result = null;
            if (resultCode == RESULT_OK && data != null && data.getData() != null) {
                result = new Uri[]{data.getData()};
            }
            if (fileCallback != null) {
                fileCallback.onReceiveValue(result);
                fileCallback = null;
            }
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    /**
     * 系统返回键统一交给网页处理：
     * - 按下先吃掉并通知页面（不触发系统默认返回）；
     * - 松开再执行页面返回逻辑，未消费才退出 App；
     * - 全面屏手势在 targetSdk 28 下走 onBackPressed，同样路由到页面。
     */
    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        if (event.getKeyCode() != KeyEvent.KEYCODE_BACK) {
            return super.dispatchKeyEvent(event);
        }
        if (event.getAction() == KeyEvent.ACTION_DOWN) {
            if (event.getRepeatCount() == 0) {
                notifyBackPressedDown();
            }
            return true;
        }
        if (event.getAction() == KeyEvent.ACTION_UP) {
            handleBack();
            return true;
        }
        return true;
    }

    @Override
    public void onBackPressed() {
        handleBack();
    }

    private void notifyBackPressedDown() {
        if (webView == null || webView.getVisibility() != View.VISIBLE) {
            return;
        }
        webView.evaluateJavascript(
                "(function(){try{if(window.__flyaiNativeBackDown)window.__flyaiNativeBackDown()}"
                        + "catch(e){}})()",
                null);
    }

    private boolean hideImeIfVisible() {
        boolean visible;
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsets insets = getWindow().getDecorView().getRootWindowInsets();
            visible = insets != null && insets.isVisible(WindowInsets.Type.ime());
        } else {
            InputMethodManager imm = (InputMethodManager)
                    getSystemService(Context.INPUT_METHOD_SERVICE);
            visible = imm != null && imm.isAcceptingText();
        }
        if (!visible) {
            return false;
        }
        InputMethodManager imm = (InputMethodManager)
                getSystemService(Context.INPUT_METHOD_SERVICE);
        if (imm != null) {
            imm.hideSoftInputFromWindow(webView.getWindowToken(), 0);
        }
        return true;
    }

    private void handleBack() {
        if (webView == null || webView.getVisibility() != View.VISIBLE) {
            finish();
            return;
        }
        // 键盘展开时先收键盘，符合系统返回键习惯；再按一次才交给页面
        if (hideImeIfVisible()) {
            return;
        }
        final int seq = ++backSeq;
        final boolean[] done = {false};
        handler.postDelayed(() -> {
            if (seq == backSeq && !done[0]) {
                done[0] = true;
                finish();
            }
        }, BACK_JS_TIMEOUT);
        webView.evaluateJavascript(
                "(function(){try{return !!(window.__flyaiNativeBack"
                        + "&&window.__flyaiNativeBack())}catch(e){return false}})()",
                value -> {
                    if (done[0]) return;
                    done[0] = true;
                    if (!"true".equals(value)) {
                        finish();
                    }
                });
    }

    @Override
    protected void onDestroy() {
        stopRunWatch();
        stopConsole();
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
