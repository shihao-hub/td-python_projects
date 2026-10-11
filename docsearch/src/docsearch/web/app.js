document.addEventListener("DOMContentLoaded", () => {
  const searchInput = document.getElementById("searchInput");
  const searchBtn = document.getElementById("searchBtn");
  const statusText = document.getElementById("statusText");
  const resultsHeader = document.getElementById("resultsHeader");
  const resultsCount = document.getElementById("resultsCount");
  const queryEcho = document.getElementById("queryEcho");
  const resultsContainer = document.getElementById("resultsContainer");
  const loadingSpinner = document.getElementById("loadingSpinner");

  // 鍔犺浇绯荤粺鐘舵€?  async function fetchStatus() {
    try {
      const res = await fetch("/api/status");
      if (res.ok) {
        const data = await res.json();
        statusText.textContent = `宸茬储寮?${data.total_files} 涓枃浠?(${data.total_chunks} 涓墖娈? | 鑷姩鐑洃鍚繍琛屼腑`;
      }
    } catch (e) {
      statusText.textContent = "鏈嶅姟杩炴帴寮傚父";
    }
  }

  fetchStatus();

  // 鎵ц妫€绱?  async function performSearch() {
    const query = searchInput.value.trim();
    if (!query) return;

    loadingSpinner.style.display = "block";
    resultsContainer.innerHTML = "";
    resultsHeader.style.display = "none";

    try {
      // 鍏佽鎷夊彇澶氬嚑涓?chunks 鐢ㄤ簬鏂囦欢鑱氬悎灞曠ず
      const res = await fetch(`/api/search?q=${encodeURIComponent(query)}&top_k=15`);
      loadingSpinner.style.display = "none";

      if (!res.ok) {
        resultsContainer.innerHTML = `<p style="color: #ef4444;">妫€绱㈠け璐? ${res.statusText}</p>`;
        return;
      }

      const data = await res.json();
      resultsHeader.style.display = "flex";

      if (data.total === 0) {
        resultsCount.textContent = "0";
        queryEcho.textContent = `鍖归厤鍏抽敭璇? "${data.query}"`;
        resultsContainer.innerHTML = `<p style="color: var(--text-muted); padding: 1.5rem 0;">娌℃湁鎵惧埌鍖归厤鐗囨銆傝纭鐩稿叧鏂囨。鏄惁鍦ㄧ洃鍚洰褰曞唴锛屾垨灏濊瘯鎹竴绉嶆弿杩般€?/p>`;
        return;
      }

      // 鎸夋枃浠惰繘琛岃仛鍚?(Group by File)
      const fileMap = new Map();
      const fileGroups = [];

      data.results.forEach((item) => {
        if (!fileMap.has(item.filepath)) {
          const group = {
            filepath: item.filepath,
            filename: item.filename,
            maxScore: item.score,
            chunks: [],
          };
          fileMap.set(item.filepath, group);
          fileGroups.push(group);
        }
        const g = fileMap.get(item.filepath);
        if (item.score > g.maxScore) {
          g.maxScore = item.score;
        }
        // 避免内容完全重复的片段冗余
if (!g.chunks.some((c) => c.content.trim() === item.content.trim())) {
  g.chunks.push(item);
}
      });

      resultsCount.textContent = `${fileGroups.length} 涓枃浠?(${data.total} 澶勭墖娈?`;
      queryEcho.textContent = `鍖归厤鍏抽敭璇? "${data.query}"`;

      // 娓叉煋鍒嗙粍鍗＄墖
      fileGroups.forEach((group) => {
        const card = document.createElement("div");
        card.className = "file-card";

        const maxScorePct = `${(group.maxScore * 100).toFixed(1)}%`;
        const countText = `${group.chunks.length} 澶勭墖娈礰;

        // 鍗＄墖澶撮儴
        let cardHtml = `
          <div class="file-card-header">
            <div class="file-meta-group">
              <div class="file-title-row">
                <span class="file-name">${escapeHtml(group.filename)}</span>
                <span class="file-count-tag">${countText}</span>
              </div>
            </div>
            <div class="file-badges">
              <span class="score-badge">鐩镐技搴?${maxScorePct}</span>
            </div>
          </div>
          <div class="file-path" title="鐐瑰嚮澶嶅埗瀹屾暣璺緞" data-path="${escapeHtml(group.filepath)}">
            馃搫 <span>${escapeHtml(group.filepath)}</span>
          </div>
        `;

        // 鍐呴儴澶氫釜鐗囨鍒楄〃
        cardHtml += `<div class="chunks-container">`;
        group.chunks.forEach((chunk, cIdx) => {
          const headingLabel = chunk.heading ? `绔犺妭: ${escapeHtml(chunk.heading)}` : `鐗囨 #${cIdx + 1}`;
          const chunkScorePct = `${(chunk.score * 100).toFixed(1)}%`;
          cardHtml += `
            <div class="chunk-item">
              <div class="chunk-meta">
                <span class="chunk-heading">${headingLabel}</span>
                <span class="chunk-lines">琛?${chunk.start_line}-${chunk.end_line} 路 ${chunkScorePct}</span>
              </div>
              <div class="chunk-body">${escapeHtml(chunk.content.trim())}</div>
            </div>
          `;
        });
        cardHtml += `</div>`;

        card.innerHTML = cardHtml;

        // 鐐瑰嚮澶嶅埗璺緞浜や簰
        const pathEl = card.querySelector(".file-path");
        pathEl.addEventListener("click", () => {
          navigator.clipboard.writeText(group.filepath);
          const span = pathEl.querySelector("span");
          const oldText = span.textContent;
          span.textContent = "宸插鍒舵枃浠跺畬鏁磋矾寰勫埌鍓创鏉匡紒";
          pathEl.style.color = "#34d399";
          setTimeout(() => {
            span.textContent = oldText;
            pathEl.style.color = "";
          }, 1500);
        });

        resultsContainer.appendChild(card);
      });
    } catch (err) {
      loadingSpinner.style.display = "none";
      resultsContainer.innerHTML = `<p style="color: #ef4444;">缃戠粶璇锋眰閿欒: ${err.message}</p>`;
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