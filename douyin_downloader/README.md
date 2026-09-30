# Douyin Video Downloader (CDP-based)

基于 Chrome DevTools Protocol (CDP) 的无损高清抖音视频下载工具。

## 工作原理
1. **持久化浏览器会话**：通过独立专用 Profile（`C:\Users\29580\.chrome-automation-profile`）运行 Chrome，登录状态和 Cookie 永久保留。
2. **底层网络流嗅探**：通过 CDP 连接（端口 9222），在网页视频播放时直接从浏览器底层网络资源通道（`performance.getEntriesByType('resource')` 与 `<video>` 元素）截获无水印高清 MP4 CDN 直链。
3. **免签名免反爬**：不需要逆向或维护复杂的 a_bogus / msToken 签名算法，直接复用真实浏览器的播放鉴权与 Cookie，100% 稳定获取原画画质。
4. **自动落盘**：下载结果默认保存至 Windows 默认下载目录（`C:\Users\29580\Downloads`）。

## 使用方法

### 命令行快速下载
```bash
python douyin_dl.py "https://v.douyin.com/xxxx/"
# 或者传入完整链接 / 视频ID
python douyin_dl.py "https://www.douyin.com/video/7662394154408639844"
```
