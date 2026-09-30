import os
import sys
import re
import time
import json
import urllib.request
import urllib.parse
import subprocess
import websocket

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE_DIR = r"C:\Users\29580\.chrome-automation-profile"
DOWNLOAD_DIR = r"C:\Users\29580\Downloads"
DEBUG_PORT = 9222

def extract_canonical_url(text):
    m = re.search(r'https?://[^\s]+', text)
    raw_url = m.group(0) if m else text.strip()

    req = urllib.request.Request(raw_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
    try:
        with opener.open(req) as resp:
            final_url = resp.geturl()
    except Exception:
        final_url = raw_url

    vid_match = re.search(r'/video/(\d+)', final_url)
    if vid_match:
        return f"https://www.douyin.com/video/{vid_match.group(1)}"
    return final_url

def ensure_chrome_running():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=1):
            return True
    except Exception:
        pass

    print("[*] Launching Chrome automation window (with persistent profile)...")
    ps1_script = os.path.join(os.path.dirname(__file__), "start_chrome.ps1")
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1_script], check=True)
    
    for _ in range(15):
        time.sleep(1)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=1):
                print("[+] Chrome CDP ready on port 9222.")
                return True
        except Exception:
            pass
    raise RuntimeError("Timed out waiting for Chrome on port 9222.")

def get_targets():
    with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json") as r:
        return json.load(r)

def eval_cdp(ws_url, expression):
    ws = websocket.create_connection(ws_url)
    try:
        msg_id = int(time.time() * 1000) % 1000000
        req = {
            "id": msg_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True
            }
        }
        ws.send(json.dumps(req))
        while True:
            res = json.loads(ws.recv())
            if res.get("id") == msg_id:
                return res.get("result", {}).get("result", {}).get("value")
    finally:
        ws.close()

def navigate_page(ws_url, url):
    ws = websocket.create_connection(ws_url)
    try:
        msg_id = 101
        ws.send(json.dumps({"id": msg_id, "method": "Page.navigate", "params": {"url": url}}))
        while True:
            res = json.loads(ws.recv())
            if res.get("id") == msg_id:
                break
    finally:
        ws.close()

def download_stream(video_url, output_path):
    print(f"[*] Starting download from stream URL...")
    req = urllib.request.Request(video_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.douyin.com/"
    })

    with urllib.request.urlopen(req) as resp, open(output_path, "wb") as f:
        downloaded = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            print(f"\rProgress: {downloaded / (1024 * 1024):.2f} MB", end="", flush=True)
    print()
    print(f"[+] Download complete: {output_path} ({downloaded} bytes)")
    return output_path

def download_by_url(video_input, custom_title=None):
    target_url = extract_canonical_url(video_input)
    print(f"[*] Target Video URL: {target_url}")

    ensure_chrome_running()
    targets = get_targets()
    douyin_tabs = [t for t in targets if t.get("type") == "page" and "douyin.com" in t.get("url", "")]

    if douyin_tabs:
        page_target = douyin_tabs[0]
        ws_url = page_target["webSocketDebuggerUrl"]
        curr_url = page_target.get("url", "")
        if target_url not in curr_url:
            print(f"[*] Navigating page to: {target_url}")
            navigate_page(ws_url, target_url)
            time.sleep(3)
    else:
        put_url = f"http://127.0.0.1:{DEBUG_PORT}/json/new?{urllib.parse.quote(target_url)}"
        req = urllib.request.Request(put_url, method="PUT")
        page_target = json.load(urllib.request.urlopen(req))
        ws_url = page_target["webSocketDebuggerUrl"]
        time.sleep(3)

    print("[*] Inspecting video stream via CDP in page...")
    stream_url = None
    title = custom_title

    for attempt in range(12):
        if not title:
            title = eval_cdp(ws_url, "document.title") or ""
        
        # 1. Check video src directly
        srcs = eval_cdp(ws_url, "Array.from(document.querySelectorAll('video')).map(v => v.currentSrc || v.src).filter(Boolean)") or []
        for s in srcs:
            if s.startswith("http") and not s.startswith("blob:"):
                stream_url = s
                break
        if stream_url:
            break

        # 2. Check performance entries
        res_urls = eval_cdp(ws_url, "performance.getEntriesByType('resource').map(r => r.name).filter(n => n.includes('.douyinvod.com') && !n.includes('media-audio'))") or []
        if res_urls:
            stream_url = res_urls[-1]
            break

        time.sleep(1)

    clean_title = re.sub(r'[\\/*?:"<>|\r\n\t]', "_", title or "")[:50].strip(" ._")
    if not clean_title:
        clean_title = f"douyin_{int(time.time())}"
    output_file = os.path.join(DOWNLOAD_DIR, f"{clean_title}.mp4")

    if stream_url:
        print(f"[+] Found stream URL: {stream_url[:90]}...")
        return download_stream(stream_url, output_file)
    else:
        print("[-] Could not extract video stream directly. If a verification slider is showing, complete it in the Chrome window.")
        return None

if __name__ == "__main__":
    url_arg = sys.argv[1] if len(sys.argv) > 1 else "https://www.douyin.com/video/7662394154408639844"
    download_by_url(url_arg)
