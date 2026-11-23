// TrioWindow.exe — opens Trio (the local server) in its own Windows window with Microsoft Edge WebView2: no address bar, no tabs.
//   TrioWindow.exe <url> [--title Trio] [--key main] [--till]
// --till (Trio Cashier): every page prints silently, like the old «chrome --kiosk-printing --app=». Other windows print silently
// only on the till (/pos/) and show a printer dialog elsewhere. The window remembers its size/place in %LOCALAPPDATA%\Trio\window.json.
// No WebView2 runtime → an Arabic message, the page opens in the browser as before, exit code 3 (the launcher then keeps the old ways).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Printing;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace TrioWindow
{
    static class Program
    {
        internal static string Home = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Trio");
        internal static CoreWebView2Environment Env;
        internal static Icon AppIcon;
        internal static string ServerHost = "localhost";
        internal static int Open;

        // window.print(): silent on the till (and in a --till window), our printer dialog elsewhere; Ctrl+P the same way; F11 = full screen
        internal const string Script = @"(function(){if(window.__trio)return;window.__trio=1;var P=window.print;
function silent(){var p=location.pathname;return !!window.__trioTill||(p.indexOf('/pos/')===0&&p.indexOf('/pos/display')!==0);}
window.print=function(){if(silent())return P.call(window);try{chrome.webview.postMessage('trio:print')}catch(e){P.call(window)}};
window.addEventListener('keydown',function(e){if(e.key==='F11'){e.preventDefault();try{chrome.webview.postMessage('trio:f11')}catch(x){}}
else if((e.ctrlKey||e.metaKey)&&!e.altKey&&(e.key==='p'||e.key==='P'||e.code==='KeyP')&&!silent()){e.preventDefault();window.print();}},true);})();";

        [STAThread]
        static int Main(string[] args)
        {
            string url = null, title = "Trio", key = "main";
            bool till = false;
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--title" && i + 1 < args.Length) title = args[++i];
                else if (args[i] == "--key" && i + 1 < args.Length) key = args[++i];
                else if (args[i] == "--till") till = true;
                else if (url == null) url = args[i];
            }
            if (string.IsNullOrEmpty(url)) url = "http://localhost:8000/";
            try { ServerHost = new Uri(url).Host; } catch { }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try { AppIcon = new Icon(typeof(Program).Assembly.GetManifestResourceStream("trio.ico")); } catch { AppIcon = SystemIcons.Application; }
            try { CoreWebView2Environment.GetAvailableBrowserVersionString(); }
            catch (Exception)
            {
                MessageBox.Show("مكوّن «Microsoft Edge WebView2» مش متسطب على الجهاز ده، فالبرنامج هيفتح في المتصفح زي الأول.\n\n" +
                    "علشان يفتح في شباكه الخاص: سطّب WebView2 من موقع مايكروسوفت (أو شغّل Trio-Setup.exe تاني وانت متصل بالنت).",
                    "Trio", MessageBoxButtons.OK, MessageBoxIcon.Information, MessageBoxDefaultButton.Button1,
                    MessageBoxOptions.RightAlign | MessageBoxOptions.RtlReading);
                try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch { }
                return 3;
            }
            Directory.CreateDirectory(Home);
            var ctx = new ApplicationContext();
            var f = new TrioForm(title, key, till, url, null);
            f.Show();
            Application.Run(ctx);
            return 0;
        }

        internal static async Task<CoreWebView2Environment> GetEnv()
        {
            if (Env == null)
            {
                var opt = new CoreWebView2EnvironmentOptions("--kiosk-printing") { Language = "ar" };
                Env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(Home, "WebView2"), opt);
            }
            return Env;
        }

        internal static bool IsLocal(Uri u)
        {
            if (u == null) return true;
            if (u.Scheme != "http" && u.Scheme != "https") return true; // about:, blob:, data:
            return u.Host == ServerHost || u.Host == "localhost" || u.Host == "127.0.0.1";
        }

        internal static void OpenOutside(string url)
        {
            try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); } catch { }
        }
    }

    class WindowState
    {
        public int x, y, w, h;
        public bool max;
    }

    static class States
    {
        static string FilePath => Path.Combine(Program.Home, "window.json");

        static Dictionary<string, WindowState> Load()
        {
            try { return new JavaScriptSerializer().Deserialize<Dictionary<string, WindowState>>(File.ReadAllText(FilePath)) ?? new Dictionary<string, WindowState>(); }
            catch { return new Dictionary<string, WindowState>(); }
        }

        internal static WindowState Get(string key)
        {
            WindowState s;
            return Load().TryGetValue(key, out s) ? s : null;
        }

        internal static void Put(string key, WindowState s)
        {
            try
            {
                var all = Load();
                all[key] = s;
                File.WriteAllText(FilePath, new JavaScriptSerializer().Serialize(all));
            }
            catch { }
        }
    }

    class TrioForm : Form
    {
        readonly WebView2 web = new WebView2();
        readonly string key, defaultTitle;
        readonly bool till;
        bool full;
        FormWindowState beforeFull;

        // first: the url to open (main window), or the new-window request to answer (window.open / target=_blank)
        internal TrioForm(string title, string key, bool till, string url, CoreWebView2NewWindowRequestedEventArgs req)
        {
            this.key = key;
            this.till = till;
            defaultTitle = title;
            Text = title;
            Icon = Program.AppIcon;
            BackColor = Color.FromArgb(2, 13, 28);
            MinimumSize = new Size(480, 360);
            web.Dock = DockStyle.Fill;
            web.DefaultBackgroundColor = Color.White;
            Controls.Add(web);
            Program.Open++;
            FormClosed += (s, e) => { if (--Program.Open <= 0) Application.ExitThread(); };
            FormClosing += (s, e) => SaveState();

            var st = States.Get(key);
            if (st != null && OnScreen(new Rectangle(st.x, st.y, st.w, st.h)))
            {
                StartPosition = FormStartPosition.Manual;
                Bounds = new Rectangle(st.x, st.y, st.w, st.h);
                if (st.max) WindowState = FormWindowState.Maximized;
            }
            else if (req != null && req.WindowFeatures != null && req.WindowFeatures.HasSize)
            {
                var wf = req.WindowFeatures;
                StartPosition = wf.HasPosition ? FormStartPosition.Manual : FormStartPosition.CenterParent;
                ClientSize = new Size((int)Math.Max(wf.Width, 400), (int)Math.Max(wf.Height, 300));
                if (wf.HasPosition) Location = new Point((int)wf.Left, (int)wf.Top);
            }
            else if (req != null)
            {
                StartPosition = FormStartPosition.CenterScreen;
                Size = new Size(1000, 760);
            }
            else
            {
                StartPosition = FormStartPosition.CenterScreen;
                Size = new Size(1280, 800);
                WindowState = FormWindowState.Maximized; // the first time: the whole screen
            }
            Init(url, req);
        }

        static bool OnScreen(Rectangle r)
        {
            return r.Width >= 200 && r.Height >= 150 && Screen.AllScreens.Any(s => s.WorkingArea.IntersectsWith(r));
        }

        void SaveState()
        {
            var b = WindowState == FormWindowState.Normal && !full ? Bounds : RestoreBounds;
            States.Put(key, new WindowState { x = b.X, y = b.Y, w = b.Width, h = b.Height, max = WindowState == FormWindowState.Maximized || full });
        }

        async void Init(string url, CoreWebView2NewWindowRequestedEventArgs req)
        {
            CoreWebView2Deferral deferral = req?.GetDeferral();
            try
            {
                var env = await Program.GetEnv();
                await web.EnsureCoreWebView2Async(env);
                var core = web.CoreWebView2;
                var set = core.Settings;
#if DEBUG
                set.AreDevToolsEnabled = true;
                set.AreDefaultContextMenusEnabled = true;
#else
                set.AreDevToolsEnabled = false;
                set.AreDefaultContextMenusEnabled = false;
#endif
                set.IsZoomControlEnabled = true;
                set.IsStatusBarEnabled = false;
                set.IsPasswordAutosaveEnabled = false;
                if (till) await core.AddScriptToExecuteOnDocumentCreatedAsync("window.__trioTill=1;");
                await core.AddScriptToExecuteOnDocumentCreatedAsync(Program.Script);
                core.DocumentTitleChanged += (s, e) => UpdateTitle();
                core.NewWindowRequested += OnNewWindow;
                core.WebMessageReceived += OnMessage;
                core.NavigationStarting += (s, e) =>
                {
                    Uri u;
                    if (Uri.TryCreate(e.Uri, UriKind.Absolute, out u) && !Program.IsLocal(u)) { e.Cancel = true; Program.OpenOutside(e.Uri); }
                };
                core.WindowCloseRequested += (s, e) => Close();
                core.ProcessFailed += (s, e) =>
                {
                    if (e.ProcessFailedKind == CoreWebView2ProcessFailedKind.RenderProcessExited || e.ProcessFailedKind == CoreWebView2ProcessFailedKind.RenderProcessUnresponsive)
                        try { core.Reload(); } catch { }
                };
                if (req != null)
                {
                    req.NewWindow = core; // the opener keeps its handle (customer display, printed cards)
                    req.Handled = true;
                }
                else core.Navigate(url);
            }
            catch (Exception ex)
            {
                MessageBox.Show("الشباك مفتحش: " + ex.Message, "Trio", MessageBoxButtons.OK, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button1,
                    MessageBoxOptions.RightAlign | MessageBoxOptions.RtlReading);
                if (url != null) Program.OpenOutside(url);
                Close();
            }
            finally { deferral?.Complete(); }
        }

        void UpdateTitle()
        {
            var t = (web.CoreWebView2?.DocumentTitle ?? "").Trim();
            if (t.Length == 0 || t.StartsWith("http") || t.StartsWith("localhost") || t.StartsWith("127.0.0.1") || t.StartsWith("about:")) Text = defaultTitle;
            else Text = t.IndexOf("Trio", StringComparison.OrdinalIgnoreCase) >= 0 ? t : t + " — Trio";
        }

        void OnNewWindow(object sender, CoreWebView2NewWindowRequestedEventArgs e)
        {
            Uri u;
            if (Uri.TryCreate(e.Uri, UriKind.Absolute, out u) && !Program.IsLocal(u)) // WhatsApp, the site, maps … → the normal browser
            {
                e.Handled = true;
                Program.OpenOutside(e.Uri);
                return;
            }
            string k = "popup";
            if (u != null && u.Scheme.StartsWith("http")) k = "popup:" + u.AbsolutePath.TrimEnd('/'); // e.g. the customer display keeps its screen
            var popTill = till;
            try { var p = new Uri(web.CoreWebView2.Source).AbsolutePath; popTill = till || p.StartsWith("/pos/") && !p.StartsWith("/pos/display"); } catch { }
            var f = new TrioForm(defaultTitle, k, popTill, null, e);
            f.Show();
        }

        async void OnMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            string m;
            try { m = e.TryGetWebMessageAsString(); } catch { return; }
            if (m == "trio:f11") ToggleFull();
            else if (m == "trio:print") await PrintWithDialog();
        }

        void ToggleFull()
        {
            if (!full)
            {
                beforeFull = WindowState;
                full = true;
                if (WindowState == FormWindowState.Maximized) WindowState = FormWindowState.Normal;
                FormBorderStyle = FormBorderStyle.None;
                WindowState = FormWindowState.Maximized;
            }
            else
            {
                full = false;
                FormBorderStyle = FormBorderStyle.Sizable;
                WindowState = FormWindowState.Normal;
                WindowState = beforeFull;
            }
        }

        // reports, invoices …: choose the printer / copies, then print
        async Task PrintWithDialog()
        {
            using (var dlg = new PrintDialog { UseEXDialog = true, AllowSomePages = true, PrinterSettings = new PrinterSettings() })
            {
                if (dlg.ShowDialog(this) != DialogResult.OK) return;
                var ps = dlg.PrinterSettings;
                var s = Program.Env.CreatePrintSettings();
                s.PrinterName = ps.PrinterName;
                s.Copies = Math.Max(1, (int)ps.Copies);
                s.ShouldPrintHeaderAndFooter = false;
                s.ShouldPrintBackgrounds = true;
                try
                {
                    var pg = ps.DefaultPageSettings;
                    s.Orientation = pg.Landscape ? CoreWebView2PrintOrientation.Landscape : CoreWebView2PrintOrientation.Portrait;
                    if (pg.PaperSize != null && pg.PaperSize.Width > 0 && pg.PaperSize.Height > 0)
                    {
                        s.PageWidth = pg.PaperSize.Width / 100.0;
                        s.PageHeight = pg.PaperSize.Height / 100.0;
                    }
                }
                catch { }
                if (ps.PrintRange == PrintRange.SomePages) s.PageRanges = ps.FromPage + "-" + ps.ToPage;
                var r = await web.CoreWebView2.PrintAsync(s);
                if (r != CoreWebView2PrintStatus.Succeeded)
                    MessageBox.Show(this, r == CoreWebView2PrintStatus.PrinterUnavailable ? "الطابعة مش متاحة — اتأكد إنها شغالة ومتوصلة." : "الطباعة مكملتش — جرّب تاني.",
                        "Trio", MessageBoxButtons.OK, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button1, MessageBoxOptions.RightAlign | MessageBoxOptions.RtlReading);
            }
        }
    }
}
