package top.eastcloud.flyai.poc;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Message;
import android.text.Spannable;
import android.text.SpannableStringBuilder;
import android.text.style.ForegroundColorSpan;
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

import org.json.JSONObject;

import java.io.File;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.UUID;

/** 本地一体化 POC：前台服务拉起 rootfs 里的 FlyAI，WebView 连 127.0.0.1:8000。 */
public class MainActivity extends Activity {
    private static final int PORT = 8000;
    private static final int FILE_CHOOSER = 1001;
    private static final long CONSOLE_INTERVAL = 700;
    /** 页面返回处理超过该时间仍未回调就按未消费处理，避免 WebView 卡死时退不出去 */
    private static final long BACK_JS_TIMEOUT = 700;

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
        stopConsole();
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
