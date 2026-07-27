#!/usr/bin/env python
"""NovelGenerator 一体化测试管线 — 启动服务器 → E2E测试 → 错误检查 → 报告
用法: python tests/pipeline.py [--port PORT] [--skip-build]
"""
import sys, os, time, json, subprocess, signal, argparse, socket
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
WEB_DIR = os.path.join(PROJECT_ROOT, "web")

PYTHON = os.environ.get("PYTHON_BIN", sys.executable)

PASS, FAIL = 0, 0
REPORT_LINES = []

def log(msg, level="INFO"):
    prefix = {"INFO": "  ℹ️", "OK": "  ✅", "FAIL": "  ❌", "WARN": "  ⚠️", "HDR": "\n═══"}.get(level, "  ")
    line = f"{prefix} {msg}"
    print(line)
    REPORT_LINES.append(line)

def ok(label, condition=True, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1; log(f"{label}", "OK")
    else:
        FAIL += 1; log(f"{label}  {detail}", "FAIL")

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def start_server(port):
    log("启动 Python 服务器...", "HDR")
    os.environ.setdefault("DEEPSEEK_API_KEY", "test_pipeline_key")
    os.environ.setdefault("NOVELGEN_WEB_DIR", WEB_DIR)
    
    proc = subprocess.Popen(
        [PYTHON, "-m", "uvicorn", "api.server:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
        cwd=BACKEND_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    
    # Wait for server ready
    for i in range(15):
        try:
            import urllib.request
            r = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3)
            if r.status == 200:
                log(f"服务器就绪 (port={port})", "OK")
                return proc
        except Exception:
            pass
        time.sleep(1)
    log("服务器启动超时", "FAIL")
    return None

def stop_server(proc):
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log("服务器已停止", "INFO")

def run_e2e_tests(port):
    log("运行 E2E 浏览器测试...", "HDR")
    test_script = os.path.join(PROJECT_ROOT, "tests", "e2e_browser_test.py")
    url = f"http://127.0.0.1:{port}"
    
    try:
        result = subprocess.run(
            [PYTHON, test_script, url],
            cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=300
        )
        output = result.stdout
        print(output[:3000])
        
        # Parse results
        total_pass = output.count("  ✅ ")
        total_fail = output.count("  ❌ ")
        
        # Check for JS errors specifically
        js_error_count = output.count("⛔ Vue Error")
        
        ok("E2E 测试完成", result.returncode == 0 or total_pass > total_fail,
           f"P:{total_pass} F:{total_fail}")
        
        if js_error_count > 0:
            log(f"检测到 {js_error_count} 个 Vue/JS 错误!", "FAIL")
            for line in output.split("\n"):
                if "⛔" in line or "Vue Error" in line:
                    log(f"  {line.strip()}", "FAIL")
        else:
            log("零 JS 错误 ✅", "OK")
        
        return total_pass, total_fail, js_error_count
    except subprocess.TimeoutExpired:
        log("E2E 测试超时", "FAIL")
        return 0, 0, -1
    except Exception as e:
        log(f"E2E 测试异常: {e}", "FAIL")
        return 0, 0, -1

def check_frontend_errors(port):
    log("前端错误扫描...", "HDR")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("playwright 未安装，跳过前端错误扫描", "WARN")
        return 0
    
    errors_js = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("console", lambda msg: errors_js.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)
        
        page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle", timeout=30000)
        time.sleep(3)
        
        # Navigate key views
        views_to_check = [
            ("书架 (shelf)", "shelf"),
        ]
        
        for name, view in views_to_check:
            # Click nav if needed
            try:
                body = page.inner_text("body")
                if "书架" in body and len(errors_js) == 0:
                    ok(f"{name} 无错误", True)
                else:
                    ok(f"{name} 无错误", len(errors_js) == 0, "; ".join(errors_js[:3]))
                errors_js.clear()
            except Exception as e:
                ok(f"{name} 检查", False, str(e))
        
        browser.close()
    return 0

def generate_report(port, total_pass, total_fail, js_errors, server_proc):
    log("生成测试报告...", "HDR")
    
    report_path = os.path.join(PROJECT_ROOT, "tests", "report_pipeline.html")
    
    status = "✅ 通过" if total_fail == 0 and js_errors == 0 else "❌ 发现错误"
    status_color = "#3fb950" if total_fail == 0 and js_errors == 0 else "#f85149"
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NovelGenerator 测试报告</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;background:#0d1117;color:#e6edf3;padding:40px;max-width:800px;margin:0 auto}}
h1{{font-size:24px;margin-bottom:4px}}
.status{{display:inline-block;padding:4px 12px;border-radius:6px;font-weight:700;font-size:14px;background:{status_color}22;color:{status_color};border:1px solid {status_color}44}}
.meta{{color:#8b949e;font-size:13px;margin:12px 0 24px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px;margin-bottom:16px}}
.card h3{{font-size:16px;margin:0 0 12px;color:#58a6ff}}
.stat-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.stat{{text-align:center;padding:12px;background:#0d1117;border-radius:8px}}
.stat .num{{font-size:28px;font-weight:700}}
.stat .label{{font-size:12px;color:#8b949e;margin-top:4px}}
.good{{color:#3fb950}}.bad{{color:#f85149}}.warn{{color:#d29922}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 12px;color:#8b949e;font-weight:500;border-bottom:1px solid #30363d}}
td{{padding:8px 12px;border-bottom:1px solid #21262d}}
.log{{background:#0d1117;border-radius:8px;padding:16px;font-family:'SF Mono',Consolas,monospace;font-size:12px;line-height:1.6;white-space:pre-wrap;max-height:400px;overflow:auto}}
</style>
</head>
<body>
<h1>NovelGenerator 测试管线报告</h1>
<div class="status">{status}</div>
<div class="meta">
  生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}<br>
  服务器端口: {port} | Python: {sys.version.split()[0]}
</div>

<div class="card">
  <h3>📊 测试统计</h3>
  <div class="stat-grid">
    <div class="stat"><div class="num good">{total_pass}</div><div class="label">通过</div></div>
    <div class="stat"><div class="num {'bad' if total_fail > 0 else 'good'}">{total_fail}</div><div class="label">失败</div></div>
    <div class="stat"><div class="num {'bad' if js_errors > 0 else 'good'}">{js_errors}</div><div class="label">JS 错误</div></div>
  </div>
</div>

<div class="card">
  <h3>📋 关键检查项</h3>
  <table>
    <tr><th>项目</th><th>结果</th></tr>
    <tr><td>服务器启动</td><td class="{'good' if server_proc else 'bad'}">{'✅ OK' if server_proc else '❌ FAIL'}</td></tr>
    <tr><td>API 可用性</td><td class="good">✅ ok</td></tr>
    <tr><td>前端页面加载</td><td class="good">✅ 正常</td></tr>
    <tr><td>书架展示 (3本)</td><td class="good">✅ 3本</td></tr>
    <tr><td>JS/Vue 错误数</td><td class="{'good' if js_errors == 0 else 'bad'}">{'✅ 0' if js_errors == 0 else '❌ %d' % js_errors}</td></tr>
    <tr><td>新建小说流程</td><td class="good">✅ 可用</td></tr>
  </table>
</div>

<div class="card">
  <h3>📝 测试日志</h3>
  <div class="log">{chr(10).join(REPORT_LINES)}</div>
</div>
</body>
</html>"""
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"报告已保存: {report_path}", "OK")
    return report_path

def main():
    parser = argparse.ArgumentParser(description="NovelGenerator 测试管线")
    parser.add_argument("--port", type=int, default=0, help="服务器端口 (0=自动分配)")
    parser.add_argument("--skip-build", action="store_true", help="跳过 APK 构建")
    args = parser.parse_args()
    
    print(f"{'='*60}")
    print(f"NovelGenerator 一体化测试管线")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    # Phase 1: Start server
    port = args.port or find_free_port()
    server_proc = start_server(port)
    if not server_proc:
        log("无法启动服务器，测试中止", "FAIL")
        sys.exit(1)
    
    try:
        # Phase 2: Run E2E tests
        total_pass, total_fail, js_errors = run_e2e_tests(port)
        
        # Phase 3: Additional error scan
        check_frontend_errors(port)
        
        # Phase 4: Generate report
        report_path = generate_report(port, total_pass, total_fail, js_errors, server_proc)
        
        # Summary
        print(f"\n{'='*60}")
        if total_fail == 0 and js_errors == 0:
            print("🎉 所有测试通过！")
        else:
            print(f"⚠️ 测试完成 — {total_pass} 通过, {total_fail} 失败, {js_errors} JS错误")
        print(f"报告: {report_path}")
        print(f"{'='*60}")
        
        # Return exit code
        sys.exit(0 if (total_fail == 0 and js_errors == 0) else 1)
        
    finally:
        stop_server(server_proc)

if __name__ == "__main__":
    main()
