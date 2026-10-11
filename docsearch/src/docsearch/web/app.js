document.addEventListener("DOMContentLoaded", function () {
  var searchInput = document.getElementById("searchInput");
  var searchBtn = document.getElementById("searchBtn");
  var statusText = document.getElementById("statusText");
  var resultsHeader = document.getElementById("resultsHeader");
  var resultsCount = document.getElementById("resultsCount");
  var queryEcho = document.getElementById("queryEcho");
  var resultsContainer = document.getElementById("resultsContainer");
  var loadingSpinner = document.getElementById("loadingSpinner");
  var toggleAllBtn = document.getElementById("toggleAllBtn");

  var isAllExpanded = false;

  function fetchStatus() {
    fetch("/api/status")
      .then(function (res) {
        if (!res.ok) throw new Error("status error");
        return res.json();
      })
      .then(function (data) {
        if (statusText) {
          statusText.textContent = "宸茬储寮?" + data.total_files + " 涓枃浠?(" + data.total_chunks + " 涓墖娈? | 鑷姩鐑洃鍚繍琛屼腑";
        }
      })
      .catch(function (e) {
        if (statusText) {
          statusText.textContent = "鏈嶅姟杩炴帴寮傚父";
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

  function toggleCard(card) {
    var chunksEl = card.querySelector(".chunks-container");
    var indicator = card.querySelector(".expand-indicator");
    if (!chunksEl) return;
    var expanded = chunksEl.classList.toggle("expanded");
    if (indicator) {
      indicator.textContent = expanded ? "鏀惰捣 鈻? : "灞曞紑 鈻?;
      indicator.style.color = expanded ? "#3b82f6" : "var(--text-muted)";
    }
  }

  function performSearch() {
    var query = (searchInput.value || "").trim();
    if (!query) return;

    loadingSpinner.style.display = "block";
    resultsContainer.innerHTML = "";
    resultsHeader.style.display = "none";
    isAllExpanded = false;
    if (toggleAllBtn) toggleAllBtn.textContent = "鍏ㄩ儴灞曞紑";

    fetch("/api/search?q=" + encodeURIComponent(query) + "&top_k=25")
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
          queryEcho.textContent = '鍖归厤鍏抽敭璇? "' + data.query + '"';
          if (toggleAllBtn) toggleAllBtn.style.display = "none";
          resultsContainer.innerHTML = '<p style="color: var(--text-muted); padding: 1.5rem 0;">娌℃湁鎵惧埌鍖归厤鐗囨銆傝纭鐩稿叧鏂囨。鏄惁鍦ㄧ洃鍚洰褰曞唴锛屾垨灏濊瘯鎹竴绉嶆弿杩般€?/p>';
          return;
        }

        if (toggleAllBtn) toggleAllBtn.style.display = "inline-block";

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

        resultsCount.textContent = fileGroups.length + " 涓枃浠?(" + data.total + " 澶勭墖娈?";
        queryEcho.textContent = '鍖归厤鍏抽敭璇? "' + data.query + '"';

        fileGroups.forEach(function (group) {
          var card = document.createElement("div");
          card.className = "file-card";

          var maxScorePct = (group.maxScore * 100).toFixed(1) + "%";
          var countText = group.chunks.length + " 澶勭墖娈?;

          var cardHtml = '<div class="file-card-header">' +
            '<div class="file-meta-group">' +
              '<div class="file-title-row">' +
                '<span class="file-name">' + escapeHtml(group.filename) + '</span>' +
                '<span class="file-count-tag">' + countText + '</span>' +
              '</div>' +
              '<div class="file-path" title="鐐瑰嚮澶嶅埗瀹屾暣璺緞" data-path="' + escapeHtml(group.filepath) + '">' +
                '馃搫 <span>' + escapeHtml(group.filepath) + '</span>' +
              '</div>' +
            '</div>' +
            '<div class="file-badges">' +
              '<span class="score-badge">鐩镐技搴?' + maxScorePct + '</span>' +
              '<span class="expand-indicator">灞曞紑 鈻?/span>' +
            '</div>' +
          '</div>';

          cardHtml += '<div class="chunks-container">';
          group.chunks.forEach(function (chunk, cIdx) {
            var headingLabel = chunk.heading ? ("绔犺妭: " + escapeHtml(chunk.heading)) : ("鐗囨 #" + (cIdx + 1));
            var chunkScorePct = (chunk.score * 100).toFixed(1) + "%";
            cardHtml += '<div class="chunk-item">' +
              '<div class="chunk-meta">' +
                '<span class="chunk-heading">' + headingLabel + '</span>' +
                '<span class="chunk-lines">琛?' + chunk.start_line + '-' + chunk.end_line + ' 路 ' + chunkScorePct + '</span>' +
              '</div>' +
              '<div class="chunk-body">' + escapeHtml(chunk.content.trim()) + '</div>' +
            '</div>';
          });
          cardHtml += '</div>';

          card.innerHTML = cardHtml;

          var headerEl = card.querySelector(".file-card-header");
          headerEl.addEventListener("click", function () {
            toggleCard(card);
          });

          var pathEl = card.querySelector(".file-path");
          pathEl.addEventListener("click", function (e) {
            e.stopPropagation();
            navigator.clipboard.writeText(group.filepath);
            var span = pathEl.querySelector("span");
            var oldText = span.textContent;
            span.textContent = "宸插鍒舵枃浠跺畬鏁磋矾寰勫埌鍓创鏉匡紒";
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
        resultsContainer.innerHTML = '<p style="color: #ef4444;">缃戠粶璇锋眰閿欒: ' + escapeHtml(err.message) + '</p>';
      });
  }

  if (toggleAllBtn) {
    toggleAllBtn.addEventListener("click", function () {
      isAllExpanded = !isAllExpanded;
      toggleAllBtn.textContent = isAllExpanded ? "鍏ㄩ儴鎶樺彔" : "鍏ㄩ儴灞曞紑";
      var cards = document.querySelectorAll(".file-card");
      cards.forEach(function (card) {
        var chunksEl = card.querySelector(".chunks-container");
        var indicator = card.querySelector(".expand-indicator");
        if (chunksEl) {
          if (isAllExpanded) {
            chunksEl.classList.add("expanded");
            if (indicator) {
              indicator.textContent = "鏀惰捣 鈻?;
              indicator.style.color = "#3b82f6";
            }
          } else {
            chunksEl.classList.remove("expanded");
            if (indicator) {
              indicator.textContent = "灞曞紑 鈻?;
              indicator.style.color = "var(--text-muted)";
            }
          }
        }
      });
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