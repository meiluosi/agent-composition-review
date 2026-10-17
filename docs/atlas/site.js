/* ACR 真实案例图谱 · 纯静态筛选脚本。
 *
 * 数据来自 index.html 中 id="case-data" 的内嵌 JSON；本脚本只读取本地 DOM，
 * 不发网络请求、不依赖任何外部库。新增案例时同时更新该 JSON 与 README 索引表。
 */
(function () {
  "use strict";

  var DATA_ID = "case-data";
  var GITHUB_BLOB = "https://github.com/meiluosi/agent-composition-review/blob/main/docs/atlas/";

  function byId(id) {
    return document.getElementById(id);
  }

  function normalize(value) {
    var text = value == null ? "" : String(value);
    if (typeof text.normalize === "function") {
      text = text.normalize("NFKC");
    }
    return text.toLocaleLowerCase("zh-CN");
  }

  function loadCases() {
    var dataElement = byId(DATA_ID);
    if (!dataElement) {
      return [];
    }
    try {
      var parsed = JSON.parse(dataElement.textContent || "{}");
      return Array.isArray(parsed.cases) ? parsed.cases : [];
    } catch (error) {
      return [];
    }
  }

  function makeLink(href, text, className) {
    var link = document.createElement("a");
    link.href = href;
    link.textContent = text;
    if (className) {
      link.className = className;
    }
    return link;
  }

  function renderCard(item) {
    var article = document.createElement("article");
    article.className = "case-body";
    article.setAttribute("aria-labelledby", "case-title-" + item.slug);

    var top = document.createElement("div");
    top.className = "case-top";
    var badge = document.createElement("span");
    badge.className = "badge badge-" + item.status;
    badge.textContent = item.status;
    var issue = document.createElement("span");
    issue.className = "case-issue";
    issue.textContent = "#" + item.issue;
    top.appendChild(badge);
    top.appendChild(issue);

    var heading = document.createElement("h3");
    heading.className = "case-title";
    heading.id = "case-title-" + item.slug;
    heading.appendChild(makeLink(GITHUB_BLOB + item.slug + ".md", item.title));

    var rules = document.createElement("ul");
    rules.className = "rules";
    rules.setAttribute("aria-label", "相关规则");
    item.rules.forEach(function (rule) {
      var ruleItem = document.createElement("li");
      var ruleLink = document.createElement("a");
      ruleLink.className = "rule-chip";
      ruleLink.href = "../rules/" + rule + ".md";
      ruleLink.textContent = rule;
      ruleItem.appendChild(ruleLink);
      rules.appendChild(ruleItem);
    });

    var keywords = document.createElement("p");
    keywords.className = "keywords";
    keywords.textContent = item.keywords.join(" · ");

    var summary = document.createElement("p");
    summary.className = "summary";
    summary.textContent = item.summary;

    var boundary = document.createElement("p");
    boundary.className = "boundary";
    var boundaryLabel = document.createElement("span");
    boundaryLabel.className = "boundary-label";
    boundaryLabel.textContent = "验证边界：";
    boundary.appendChild(boundaryLabel);
    boundary.appendChild(document.createTextNode(item.boundary));

    var links = document.createElement("p");
    links.className = "case-links";
    links.appendChild(makeLink(item.slug + ".md", "本目录 Markdown", "case-link"));
    links.appendChild(document.createTextNode(" · "));
    links.appendChild(makeLink(GITHUB_BLOB + item.slug + ".md", "GitHub 渲染", "case-link"));
    links.appendChild(document.createTextNode(" · "));
    links.appendChild(makeLink(item.source, "原始 issue（GitHub）", "case-link case-link-external"));

    var card = document.createElement("li");
    card.className = "case-card";
    article.appendChild(top);
    article.appendChild(heading);
    article.appendChild(rules);
    article.appendChild(keywords);
    article.appendChild(summary);
    article.appendChild(boundary);
    article.appendChild(links);
    card.appendChild(article);
    return card;
  }

  var cases = loadCases();
  var entries = cases.map(function (item) {
    var haystack = normalize([
      item.issue,
      "#" + item.issue,
      item.slug,
      item.title,
      item.summary,
      item.boundary,
      item.status,
      (item.rules || []).join(" "),
      (item.keywords || []).join(" ")
    ].join(" "));
    return { item: item, haystack: haystack };
  });

  var searchInput = byId("search");
  var list = byId("case-list");
  var count = byId("result-count");
  var empty = byId("empty");

  if (!searchInput || !list || !count || !empty) {
    return;
  }

  function currentStatus() {
    var checked = document.querySelector('input[name="status"]:checked');
    return checked ? checked.value : "all";
  }

  function matches(entry, query) {
    if (!query) {
      return true;
    }
    var tokens = query.split(/\s+/).filter(Boolean);
    return tokens.every(function (token) {
      return entry.haystack.indexOf(token) !== -1;
    });
  }

  function updateCount(total, shown, status) {
    var text;
    if (shown === total) {
      text = "共 " + total + " 个案例";
    } else {
      text = "显示 " + shown + " / " + total + " 个案例";
    }
    if (status !== "all") {
      text += "（" + status + "）";
    }
    count.textContent = text;
  }

  function applyFilters() {
    var query = normalize(searchInput.value.trim());
    var status = currentStatus();
    var fragment = document.createDocumentFragment();
    var shown = 0;

    entries.forEach(function (entry) {
      if (status !== "all" && entry.item.status !== status) {
        return;
      }
      if (!matches(entry, query)) {
        return;
      }
      fragment.appendChild(renderCard(entry.item));
      shown += 1;
    });

    while (list.firstChild) {
      list.removeChild(list.firstChild);
    }
    list.appendChild(fragment);
    updateCount(entries.length, shown, status);
    empty.hidden = shown !== 0;
  }

  searchInput.addEventListener("input", applyFilters);
  Array.prototype.forEach.call(
    document.querySelectorAll('input[name="status"]'),
    function (input) {
      input.addEventListener("change", applyFilters);
    }
  );

  document.addEventListener("keydown", function (event) {
    var active = document.activeElement;
    var tag = active ? active.tagName : "";
    var typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";

    if (event.key === "/" && !typing) {
      event.preventDefault();
      searchInput.focus();
      searchInput.select();
      return;
    }
    if (event.key === "Escape" && active === searchInput) {
      if (searchInput.value) {
        searchInput.value = "";
        applyFilters();
      } else {
        searchInput.blur();
      }
    }
  });

  applyFilters();
})();
