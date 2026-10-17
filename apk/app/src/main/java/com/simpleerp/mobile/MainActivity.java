package com.simpleerp.mobile;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.webkit.CookieManager;
import android.webkit.ValueCallback;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

/**
 * 简单ERP 手机只读端的外壳：一个全屏 WebView 指向桌面端第二监听（默认 5001）的 /m/。
 *
 * 设计约束（对应 docs/需求/2026-10-17_安卓只读端架构决策.md）：
 * - 只读：这里不提供任何写操作入口。
 * - 地址存本地、可改：首次要求输入，之后右下角齿轮随时改。
 * - 加载失败要有自己的兜底页：电脑关机 / 不同 WiFi 时浏览器只会给原生错误页，
 *   这一层能显示人话 + 重试 + 改地址。
 */
public class MainActivity extends Activity {

    private static final String PREFS = "simple_erp";
    private static final String KEY_BASE = "base_url";
    private static final String KEY_COOKIE = "session_cookie";
    private static final int DEFAULT_PORT = 5001;

    private FrameLayout root;
    private WebView web;
    private ViewGroup errorPanel;
    private TextView errorDetail;
    private String baseUrl = "";
    private long lastBackPress = 0L;
    private boolean loadFailed = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        baseUrl = prefs().getString(KEY_BASE, "");
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.WHITE);
        setContentView(root);
        if (baseUrl == null || baseUrl.isEmpty()) {
            showSetup(false);
        } else {
            startWeb();
        }
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    private int dp(int v) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v,
                getResources().getDisplayMetrics());
    }

    /** 接受 192.168.10.5 / 192.168.10.5:5001 / http://192.168.10.5:5001/ 三种写法。 */
    static String normalize(String raw) {
        String s = raw == null ? "" : raw.trim().replace(" ", "");
        if (s.isEmpty()) {
            return "";
        }
        if (s.startsWith("http://")) {
            s = s.substring(7);
        } else if (s.startsWith("https://")) {
            s = s.substring(8);
        }
        int slash = s.indexOf('/');
        if (slash >= 0) {
            s = s.substring(0, slash);
        }
        while (s.endsWith("/")) {
            s = s.substring(0, s.length() - 1);
        }
        if (s.isEmpty()) {
            return "";
        }
        if (s.contains(":")) {
            String[] parts = s.split(":", 2);
            if (parts[0].isEmpty() || !parts[1].matches("\\d{1,5}")) {
                return "";
            }
            return "http://" + s;
        }
        return "http://" + s + ":" + DEFAULT_PORT;
    }

    /** 展示给用户看的形式：去掉 http:// 与默认端口。 */
    static String pretty(String url) {
        String s = url == null ? "" : url;
        if (s.startsWith("http://")) {
            s = s.substring(7);
        }
        String tail = ":" + DEFAULT_PORT;
        if (s.endsWith(tail)) {
            s = s.substring(0, s.length() - tail.length());
        }
        return s;
    }

    private void clearViews() {
        if (web != null) {
            web.setWebViewClient(new WebViewClient());
            web.stopLoading();
            root.removeView(web);
            web.destroy();
            web = null;
        }
        errorPanel = null;
        errorDetail = null;
        root.removeAllViews();
    }

    // ---------------- 设置页 ----------------

    private void showSetup(boolean cancellable) {
        clearViews();

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(28), dp(48), dp(28), dp(28));
        box.setBackgroundColor(Color.WHITE);

        TextView title = new TextView(this);
        title.setText("连接你的电脑");
        title.setTextSize(24f);
        title.setTextColor(0xFF111827);
        box.addView(title);

        TextView desc = new TextView(this);
        desc.setText("在电脑上打开「设置 → 手机访问」，把那里显示的地址填到这里。\n"
                + "例如 192.168.10.5 或 192.168.10.5:5001（不写端口按 5001）。\n"
                + "手机要和电脑连同一个 WiFi。");
        desc.setTextSize(14f);
        desc.setTextColor(0xFF6B7280);
        desc.setPadding(0, dp(14), 0, dp(22));
        box.addView(desc);

        final EditText input = new EditText(this);
        input.setHint("192.168.10.5:5001");
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT);
        input.setImeOptions(EditorInfo.IME_ACTION_DONE);
        input.setText(baseUrl.isEmpty() ? "" : pretty(baseUrl));
        box.addView(input);

        LinearLayout.LayoutParams btnLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        btnLp.topMargin = dp(18);

        Button save = new Button(this);
        save.setText("保存并连接");
        save.setOnClickListener(v -> {
            String url = normalize(input.getText().toString());
            if (url.isEmpty()) {
                Toast.makeText(this, "地址不对，形如 192.168.10.5:5001", Toast.LENGTH_LONG).show();
                return;
            }
            prefs().edit().putString(KEY_BASE, url).remove(KEY_COOKIE).apply();
            baseUrl = url;
            startWeb();
        });
        box.addView(save, btnLp);

        if (cancellable) {
            Button cancel = new Button(this);
            cancel.setText("取消");
            cancel.setOnClickListener(v -> {
                if (baseUrl.isEmpty()) {
                    showSetup(false);
                } else {
                    startWeb();
                }
            });
            box.addView(cancel, btnLp);
        }

        root.addView(box, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    // ---------------- WebView ----------------

    @SuppressLint("SetJavaScriptEnabled")
    private void startWeb() {
        clearViews();

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        }
        // 手机端靠 cookie 免配对，必须保留
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                loadFailed = false;
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                // WebView 自己的错误页加载完也会回调 here：失败时不能撤掉自家兜底层。
                if (loadFailed) {
                    return;
                }
                // 配对成功后服务端会 Set-Cookie（400 天）。WebView 的 cookie 是延迟落盘的，
                // 进程被系统杀掉时可能还没写盘 → 下次冷启动会被当成未配对。
                // 这里既 flush，也把整条 Cookie 头存进 SharedPreferences，启动时再注入。
                CookieManager.getInstance().flush();
                String cookie = CookieManager.getInstance().getCookie(baseUrl + "/");
                if (cookie != null && !cookie.isEmpty()) {
                    // commit() 同步落盘：apply() 是异步的，进程被强杀时可能还没写
                    prefs().edit().putString(KEY_COOKIE, cookie).commit();
                    android.util.Log.i("SimpleERP", "cookie saved, len=" + cookie.length());
                } else {
                    android.util.Log.i("SimpleERP", "no cookie at " + url);
                }
                if (errorPanel != null) {
                    root.removeView(errorPanel);
                    errorPanel = null;
                    errorDetail = null;
                }
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request == null || request.isForMainFrame()) {
                    loadFailed = true;
                    view.stopLoading();
                    showError();
                }
            }

            @Override
            @SuppressWarnings("deprecation")
            public void onReceivedError(WebView view, int errorCode, String description,
                                        String failingUrl) {
                if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
                    loadFailed = true;
                    view.stopLoading();
                    showError();
                }
            }
        });

        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        openHome();

        addSettingsFab();
    }

    /**
     * 打开首页前先把上次存下的会话 cookie 注入回去。
     *
     * 为什么不让 WebView 自己记：实测 force-stop（系统回收/用户划掉）后，
     * WebView 的 cookie 存储没能保住配对态，冷启动会被当成未配对。所以由 App
     * 自己保存整条 Cookie 头，启动时写回 —— 不依赖 WebView 的落盘时机。
     */
    private void openHome() {
        final String saved = prefs().getString(KEY_COOKIE, "");
        if (saved == null || saved.isEmpty()) {
            web.loadUrl(baseUrl + "/m/");
            return;
        }
        android.util.Log.i("SimpleERP", "inject cookie, len=" + saved.length());
        CookieManager.getInstance().setCookie(baseUrl + "/", saved, new ValueCallback<Boolean>() {
            @Override
            public void onReceiveValue(Boolean value) {
                web.loadUrl(baseUrl + "/m/");
            }
        });
    }

    private void addSettingsFab() {
        TextView fab = new TextView(this);
        fab.setText("\u2699");
        fab.setTextSize(20f);
        fab.setTextColor(0xFFFFFFFF);
        fab.setGravity(Gravity.CENTER);
        GradientDrawable bg = new GradientDrawable();
        bg.setShape(GradientDrawable.OVAL);
        bg.setColor(0x59000000);
        fab.setBackground(bg);
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(dp(42), dp(42));
        lp.gravity = Gravity.BOTTOM | Gravity.END;
        lp.setMargins(0, 0, dp(12), dp(12));
        fab.setOnClickListener(v -> showAddressDialog());
        root.addView(fab, lp);
    }

    private void showAddressDialog() {
        final EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint("192.168.10.5:5001");
        input.setText(baseUrl.isEmpty() ? "" : pretty(baseUrl));
        new AlertDialog.Builder(this)
                .setTitle("电脑地址")
                .setView(input)
                .setPositiveButton("保存并重连", (d, which) -> {
                    String url = normalize(input.getText().toString());
                    if (url.isEmpty()) {
                        Toast.makeText(this, "地址不对，形如 192.168.10.5:5001", Toast.LENGTH_LONG).show();
                        return;
                    }
                    prefs().edit().putString(KEY_BASE, url).remove(KEY_COOKIE).apply();
                    baseUrl = url;
                    if (web != null) {
                        web.loadUrl(baseUrl + "/m/");
                    } else {
                        startWeb();
                    }
                })
                .setNegativeButton("取消", null)
                .show();
    }

    // ---------------- 兜底页 ----------------

    private void showError() {
        if (errorPanel != null) {
            if (errorDetail != null) {
                errorDetail.setText("当前地址：" + pretty(baseUrl));
            }
            return;
        }

        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(28), dp(64), dp(28), dp(28));
        box.setBackgroundColor(0xFFF9FAFB);

        TextView title = new TextView(this);
        title.setText("连不上电脑");
        title.setTextSize(22f);
        title.setTextColor(0xFF111827);
        box.addView(title);

        errorDetail = new TextView(this);
        errorDetail.setText("当前地址：" + pretty(baseUrl));
        errorDetail.setTextSize(13f);
        errorDetail.setTextColor(0xFF6B7280);
        errorDetail.setPadding(0, dp(10), 0, dp(18));
        box.addView(errorDetail);

        TextView reasons = new TextView(this);
        reasons.setText("可能是：\n"
                + "1. 电脑上的简单ERP 没开着；\n"
                + "2. 手机和电脑不在同一个 WiFi；\n"
                + "3. 电脑换过 IP（路由重启后可能变）。");
        reasons.setTextSize(14f);
        reasons.setTextColor(0xFF374151);
        reasons.setLineSpacing(dp(4), 1f);
        box.addView(reasons);

        LinearLayout.LayoutParams btnLp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        btnLp.topMargin = dp(24);

        Button retry = new Button(this);
        retry.setText("重试");
        retry.setOnClickListener(v -> {
            if (errorPanel != null) {
                root.removeView(errorPanel);
                errorPanel = null;
                errorDetail = null;
            }
            if (web != null) {
                web.loadUrl(baseUrl + "/m/");
            } else {
                startWeb();
            }
        });
        box.addView(retry, btnLp);

        Button change = new Button(this);
        change.setText("改地址");
        change.setOnClickListener(v -> showAddressDialog());
        box.addView(change, btnLp);

        errorPanel = box;
        root.addView(box, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
    }

    // ---------------- 生命周期 ----------------

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
            return;
        }
        if (web == null) {
            super.onBackPressed();
            return;
        }
        long now = System.currentTimeMillis();
        if (now - lastBackPress < 2000L) {
            super.onBackPressed();
            return;
        }
        lastBackPress = now;
        Toast.makeText(this, "再按一次退出", Toast.LENGTH_SHORT).show();
    }

    @Override
    protected void onPause() {
        // 切后台/被系统回收前把 cookie 落盘（同上，避免下次冷启动丢配对态）
        if (web != null) {
            CookieManager.getInstance().flush();
        }
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (web != null) {
            web.setWebViewClient(new WebViewClient());
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }
}
