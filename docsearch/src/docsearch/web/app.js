document.addEventListener('DOMContentLoaded', function () {
  var searchInput = document.getElementById('searchInput');
  var searchBtn = document.getElementById('searchBtn');
  var statusText = document.getElementById('statusText');
  var resultsHeader = document.getElementById('resultsHeader');
  var resultsCount = document.getElementById('resultsCount');
  var queryEcho = document.getElementById('queryEcho');
  var resultsContainer = document.getElementById('resultsContainer');
  var loadingSpinner = document.getElementById('loadingSpinner');
  var toggleAllBtn = document.getElementById('toggleAllBtn');

  var isAllExpanded = false;

  function updateStatus() {
    fetch('/api/status')
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (statusText) {
          statusText.textContent = '\u5df2\u7d22\u5f15 ' + data.total_files + ' \u4e2a\u6587\u4ef6 (' + data.total_chunks + ' \u4e2a\u7247\u6bb5) | \u81ea\u52a8\u70ed\u76d1\u542c\u8fd0\u884c\u4e2d';
        }
      })
      .catch(function () {
        if (statusText) statusText.textContent = '\u670d\u52a1\u8fde\u63a5\u5f02\u5e38';
      });
  }

  updateStatus();

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function toggleCard(card) {
    var chunksEl = card.querySelector('.chunks-container');
    var indicator = card.querySelector('.expand-indicator');
    if (!chunksEl) return;
    var expanded = chunksEl.classList.toggle('expanded');
    if (indicator) {
      indicator.textContent = expanded ? '\u6536\u8d77 \u25b2' : '\u5c55\u5f00 \u25bc';
      indicator.style.color = expanded ? '#3b82f6' : 'var(--text-muted)';
    }
  }

  function doSearch() {
    var query = (searchInput.value || '').trim();
    if (!query) return;

    loadingSpinner.style.display = 'block';
    resultsContainer.innerHTML = '';
    resultsHeader.style.display = 'none';
    isAllExpanded = false;
    if (toggleAllBtn) toggleAllBtn.textContent = '\u5168\u90e8\u5c55\u5f00';

    fetch('/api/search?q=' + encodeURIComponent(query) + '&top_k=25')
      .then(function (res) {
        loadingSpinner.style.display = 'none';
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        resultsHeader.style.display = 'flex';

        if (!data.results || data.results.length === 0) {
          resultsCount.textContent = '0';
          queryEcho.textContent = '\u5339\u914d\u5173\u952e\u8bcd: "' + data.query + '"';
          if (toggleAllBtn) toggleAllBtn.style.display = 'none';
          resultsContainer.innerHTML = '<p style="color: var(--text-muted); padding: 1.5rem 0;">\u6ca1\u6709\u627e\u5230\u5339\u914d\u7247\u6bb5\u3002</p>';
          return;
        }

        if (toggleAllBtn) toggleAllBtn.style.display = 'inline-block';

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

        resultsCount.textContent = fileGroups.length + ' \u4e2a\u6587\u4ef6 (' + data.total + ' \u5904\u7247\u6bb5)';
        queryEcho.textContent = '\u5339\u914d\u5173\u952e\u8bcd: "' + data.query + '"';

        fileGroups.forEach(function (group) {
          var card = document.createElement('div');
          card.className = 'file-card';

          var maxScorePct = (group.maxScore * 100).toFixed(1) + '%';
          var countText = group.chunks.length + ' \u5904\u7247\u6bb5';

          var cardHtml = '<div class="file-card-header">' +
            '<div class="file-meta-group">' +
              '<div class="file-title-row">' +
                '<span class="file-name">' + escapeHtml(group.filename) + '</span>' +
                '<span class="file-count-tag">' + countText + '</span>' +
              '</div>' +
              '<div class="file-path" title="\u70b9\u51fb\u590d\u5236\u5b8c\u6574\u8def\u5f84" data-path="' + escapeHtml(group.filepath) + '">' +
                '\ud83d\udcc4 <span>' + escapeHtml(group.filepath) + '</span>' +
              '</div>' +
            '</div>' +
            '<div class="file-badges">' +
              '<span class="score-badge">\u76f8\u4f3c\u5ea6 ' + maxScorePct + '</span>' +
              '<span class="expand-indicator">\u5c55\u5f00 \u25bc</span>' +
            '</div>' +
          '</div>';

          cardHtml += '<div class="chunks-container">';
          group.chunks.forEach(function (chunk, cIdx) {
            var headingLabel = chunk.heading ? ('\u7ae0\u8282: ' + escapeHtml(chunk.heading)) : ('\u7247\u6bb5 #' + (cIdx + 1));
            var chunkScorePct = (chunk.score * 100).toFixed(1) + '%';
            cardHtml += '<div class="chunk-item">' +
              '<div class="chunk-meta">' +
                '<span class="chunk-heading">' + headingLabel + '</span>' +
                '<span class="chunk-lines">\u884c ' + chunk.start_line + '-' + chunk.end_line + ' \u00b7 ' + chunkScorePct + '</span>' +
              '</div>' +
              '<div class="chunk-body">' + escapeHtml(chunk.content.trim()) + '</div>' +
            '</div>';
          });
          cardHtml += '</div>';

          card.innerHTML = cardHtml;

          var headerEl = card.querySelector('.file-card-header');
          headerEl.addEventListener('click', function () {
            toggleCard(card);
          });

          var pathEl = card.querySelector('.file-path');
          pathEl.addEventListener('click', function (e) {
            e.stopPropagation();
            navigator.clipboard.writeText(group.filepath);
            var span = pathEl.querySelector('span');
            var oldText = span.textContent;
            span.textContent = '\u5df2\u590d\u5236\u6587\u4ef6\u5b8c\u6574\u8def\u5f84\u5230\u526a\u8d34\u677f\uff01';
            pathEl.style.color = '#34d399';
            setTimeout(function () {
              span.textContent = oldText;
              pathEl.style.color = '';
            }, 1500);
          });

          resultsContainer.appendChild(card);
        });
      })
      .catch(function (err) {
        loadingSpinner.style.display = 'none';
        resultsContainer.innerHTML = '<p style="color: #ef4444;">\u7f51\u7edc\u8bf7\u6c42\u9519\u8bef: ' + escapeHtml(err.message) + '</p>';
      });
  }

  if (toggleAllBtn) {
    toggleAllBtn.addEventListener('click', function () {
      isAllExpanded = !isAllExpanded;
      toggleAllBtn.textContent = isAllExpanded ? '\u5168\u90e8\u6298\u53e0' : '\u5168\u90e8\u5c55\u5f00';
      var cards = document.querySelectorAll('.file-card');
      cards.forEach(function (card) {
        var chunksEl = card.querySelector('.chunks-container');
        var indicator = card.querySelector('.expand-indicator');
        if (chunksEl) {
          if (isAllExpanded) {
            chunksEl.classList.add('expanded');
            if (indicator) {
              indicator.textContent = '\u6536\u8d77 \u25b2';
              indicator.style.color = '#3b82f6';
            }
          } else {
            chunksEl.classList.remove('expanded');
            if (indicator) {
              indicator.textContent = '\u5c55\u5f00 \u25bc';
              indicator.style.color = 'var(--text-muted)';
            }
          }
        }
      });
    });
  }

  if (searchBtn) {
    searchBtn.addEventListener('click', doSearch);
  }
  if (searchInput) {
    searchInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        doSearch();
      }
    });
  }
});