document.addEventListener("DOMContentLoaded", function () {
  var searchInput = document.getElementById("searchInput");
  var searchBtn = document.getElementById("searchBtn");
  var statusText = document.getElementById("statusText");
  var resultsHeader = document.getElementById("resultsHeader");
  var resultsCount = document.getElementById("resultsCount");
  var queryEcho = document.getElementById("queryEcho");
  var resultsContainer = document.getElementById("resultsContainer");
  var loadingSpinner = document.getElementById("loadingSpinner");

  function fetchStatus() {
    fetch("/api/status")
      .then(function (res) {
        if (!res.ok) throw new Error("status error");
        return res.json();
      })
      .then(function (data) {
        if (statusText) {
          statusText.textContent = "已索引 " + data.total_files + " 个文件 (" + data.total_chunks + " 个片段) | 自动热监听运行中";
        }
      })
      .catch(function (e) {
        if (statusText) {
          statusText.textContent = "服务连接异常";
        }
      });
  }

  fetchStatus();

  function escapeHtml(text) {
    if (!text) return "";
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function performSearch() {
    var query = (searchInput.value || "").trim();
    if (!query) return;

    loadingSpinner.style.display = "block";
    resultsContainer.innerHTML = "";
    resultsHeader.style.display = "none";

    fetch("/api/search?q=" + encodeURIComponent(query) + "&top_k=15")
      .then(function (res) {
        loadingSpinner.style.display = "none";
        if (!res.ok) {
          throw new Error("HTTP " + res.status);
        }
        return res.json();
      })
      .then(function (data) {
        resultsHeader.style.display = "flex";

        if (!data.results || data.results.length === 0) {
          resultsCount.textContent = "0";
          queryEcho.textContent = '匹配关键词: "' + data.query + '"';
          resultsContainer.innerHTML = '<p style="color: var(--text-muted); padding: 1.5rem 0;">没有找到匹配片段。请确认相关文档是否在监听目录内，或尝试换一种描述。</p>';
          return;
        }

        var fileMap = new Map();
        var fileGroups = [];

        data.results.forEach(function (item) {
          if (!fileMap.has(item.filepath)) {
            var group = {
              filepath: item.filepath,
              filename: item.filename,
              maxScore: item.score,
              chunks: []
            };
            fileMap.set(item.filepath, group);
            fileGroups.push(group);
          }
          var g = fileMap.get(item.filepath);
          if (item.score > g.maxScore) {
            g.maxScore = item.score;
          }
          var isDuplicate = g.chunks.some(function (c) {
            return c.content.trim() === item.content.trim();
          });
          if (!isDuplicate) {
            g.chunks.push(item);
          }
        });

        resultsCount.textContent = fileGroups.length + " 个文件 (" + data.total + " 处片段)";
        queryEcho.textContent = '匹配关键词: "' + data.query + '"';

        fileGroups.forEach(function (group) {
          var card = document.createElement("div");
          card.className = "file-card";

          var maxScorePct = (group.maxScore * 100).toFixed(1) + "%";
          var countText = group.chunks.length + " 处片段";

          var cardHtml = '<div class="file-card-header">' +
            '<div class="file-meta-group">' +
              '<div class="file-title-row">' +
                '<span class="file-name">' + escapeHtml(group.filename) + '</span>' +
                '<span class="file-count-tag">' + countText + '</span>' +
              '</div>' +
            '</div>' +
            '<div class="file-badges">' +
              '<span class="score-badge">相似度 ' + maxScorePct + '</span>' +
            '</div>' +
          '</div>' +
          '<div class="file-path" title="点击复制完整路径" data-path="' + escapeHtml(group.filepath) + '">' +
            '📄 <span>' + escapeHtml(group.filepath) + '</span>' +
          '</div>';

          cardHtml += '<div class="chunks-container">';
          group.chunks.forEach(function (chunk, cIdx) {
            var headingLabel = chunk.heading ? ("章节: " + escapeHtml(chunk.heading)) : ("片段 #" + (cIdx + 1));
            var chunkScorePct = (chunk.score * 100).toFixed(1) + "%";
            cardHtml += '<div class="chunk-item">' +
              '<div class="chunk-meta">' +
                '<span class="chunk-heading">' + headingLabel + '</span>' +
                '<span class="chunk-lines">行 ' + chunk.start_line + '-' + chunk.end_line + ' · ' + chunkScorePct + '</span>' +
              '</div>' +
              '<div class="chunk-body">' + escapeHtml(chunk.content.trim()) + '</div>' +
            '</div>';
          });
          cardHtml += '</div>';

          card.innerHTML = cardHtml;

          var pathEl = card.querySelector(".file-path");
          pathEl.addEventListener("click", function () {
            navigator.clipboard.writeText(group.filepath);
            var span = pathEl.querySelector("span");
            var oldText = span.textContent;
            span.textContent = "已复制文件完整路径到剪贴板！";
            pathEl.style.color = "#34d399";
            setTimeout(function () {
              span.textContent = oldText;
              pathEl.style.color = "";
            }, 1500);
          });

          resultsContainer.appendChild(card);
        });
      })
      .catch(function (err) {
        loadingSpinner.style.display = "none";
        resultsContainer.innerHTML = '<p style="color: #ef4444;">网络请求错误: ' + escapeHtml(err.message) + '</p>';
      });
  }

  if (searchBtn) {
    searchBtn.addEventListener("click", performSearch);
  }
  if (searchInput) {
    searchInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        performSearch();
      }
    });
  }
});