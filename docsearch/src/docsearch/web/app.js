document.addEventListener("DOMContentLoaded", () => {
  const searchInput = document.getElementById("searchInput");
  const searchBtn = document.getElementById("searchBtn");
  const statusText = document.getElementById("statusText");
  const resultsHeader = document.getElementById("resultsHeader");
  const resultsCount = document.getElementById("resultsCount");
  const queryEcho = document.getElementById("queryEcho");
  const resultsContainer = document.getElementById("resultsContainer");
  const loadingSpinner = document.getElementById("loadingSpinner");

  // 加载系统状态
  async function fetchStatus() {
    try {
      const res = await fetch("/api/status");
      if (res.ok) {
        const data = await res.json();
        statusText.textContent = `已索引 ${data.total_files} 个文件 (${data.total_chunks} 个片段) | 自动热监听运行中`;
      }
    } catch (e) {
      statusText.textContent = "服务连接异常";
    }
  }

  fetchStatus();

  // 执行检索
  async function performSearch() {
    const query = searchInput.value.trim();
    if (!query) return;

    loadingSpinner.style.display = "block";
    resultsContainer.innerHTML = "";
    resultsHeader.style.display = "none";

    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(query)}&top_k=8`);
      loadingSpinner.style.display = "none";

      if (!res.ok) {
        resultsContainer.innerHTML = `<p style="color: #ef4444;">检索失败: ${res.statusText}</p>`;
        return;
      }

      const data = await res.json();
      resultsHeader.style.display = "flex";
      resultsCount.textContent = data.total;
      queryEcho.textContent = `匹配关键词: "${data.query}"`;

      if (data.total === 0) {
        resultsContainer.innerHTML = `<p style="color: var(--text-muted); padding: 1.5rem 0;">没有找到匹配片段。请确认相关文档是否在监听目录内，或尝试换一种描述。</p>`;
        return;
      }

      data.results.forEach((item, idx) => {
        const card = document.createElement("div");
        card.className = "result-card";

        const scorePct = `${(item.score * 100).toFixed(1)}%`;
        card.innerHTML = `
          <div class="card-top">
            <div class="card-title-group">
              <span class="card-filename">${escapeHtml(item.filename)}</span>
              <span class="card-heading">章节: ${escapeHtml(item.heading || "全部")}</span>
            </div>
            <span class="score-badge">相似度 ${scorePct}</span>
          </div>
          <div class="card-path" title="点击复制路径" data-path="${escapeHtml(item.filepath)}">
            📄 ${escapeHtml(item.filepath)} (第 ${item.start_line}-${item.end_line} 行)
          </div>
          <div class="card-content">${escapeHtml(item.content)}</div>
        `;

        // 点击复制路径交互
        const pathEl = card.querySelector(".card-path");
        pathEl.addEventListener("click", () => {
          navigator.clipboard.writeText(item.filepath);
          const original = pathEl.textContent;
          pathEl.textContent = "✅ 已复制文件路径到剪贴板！";
          setTimeout(() => { pathEl.textContent = original; }, 1500);
        });

        resultsContainer.appendChild(card);
      });
    } catch (err) {
      loadingSpinner.style.display = "none";
      resultsContainer.innerHTML = `<p style="color: #ef4444;">网络请求错误: ${err.message}</p>`;
    }
  }

  function escapeHtml(text) {
    if (!text) return "";
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  searchBtn.addEventListener("click", performSearch);
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      performSearch();
    }
  });
});