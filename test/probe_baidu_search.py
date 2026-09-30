"""探测：让程序自己「百度」→ 解析结果 → 还原真实 URL → 抓正文 → 转 markdown。

分阶段探测，每步打印证据：
  ① 取百度首页 cookie（BAIDUID）后请求 /s 搜索页，看是否被反爬拦截
  ② 用 html.parser 解析结果条目（标题 / 百度跳转链 / 摘要 / 是否广告）
  ③ 跟随 www.baidu.com/link?url= 跳转还原真实 URL
  ④ 抓取目标页面：编码探测 → 去标签提纯文本 → 粗 markdown
  ⑤ 汇总成一份给 AI 的 markdown 上下文样例

百度是国内站，默认不走代理；可用 BAIDU_PROXY 环境变量指定。
"""

from __future__ import annotations

import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx

DUMP_DIR = Path(__file__).parent / "temp"
QUERY = "北京今天天气"


def banner(t):
    print("\n" + "=" * 18 + " " + t + " " + "=" * 18)


# ---------- ② 结果页解析 ----------

class BaiduResultParser(HTMLParser):
    """从百度 /s 页面抽取结果：h3 内的 <a> 为标题+跳转链，摘要取邻近 div 文本。

    百度 class 名经常变（随机哈希），这里按结构关系识别，不写死 class。
    """

    def __init__(self):
        super().__init__()
        self.results: List[Dict[str, Any]] = []
        self._h3_depth = 0
        self._cur_a: Optional[Dict[str, Any]] = None
        self._a_text: List[str] = []
        # 摘要采集：进入 result 容器后收集 h3 之外的文本
        self._collect_text: List[str] = []
        self._in_h3 = False
        self._pending_hrefs: List[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "h3":
            self._in_h3 = True
        if tag == "a" and self._in_h3 and d.get("href", "").startswith("http"):
            self._cur_a = {"href": d["href"], "title": "", "abstract": "",
                           "mu": d.get("mu", "")}
            self._a_text = []
        # data-click 里的真实 url（部分结果携带）
        if tag == "div" and d.get("data-click"):
            self._last_click = d["data-click"]

    def handle_endtag(self, tag):
        if tag == "h3":
            self._in_h3 = False
            if self._cur_a:
                self._cur_a["title"] = "".join(self._a_text).strip()
                self.results.append(self._cur_a)
                self._cur_a = None

    def handle_data(self, data):
        if self._cur_a is not None:
            self._a_text.append(data)


def main():
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    proxy = os.environ.get("BAIDU_PROXY") or None
    client_kw = {"timeout": 30.0, "follow_redirects": True}
    if proxy:
        client_kw["proxy"] = proxy
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

    with httpx.Client(**client_kw) as client:
        # ---------- ① 首页拿 cookie ----------
        banner("① 取 BAIDUID + 请求搜索页")
        r0 = client.get("https://www.baidu.com/",
                        headers={"User-Agent": ua})
        print("home:", r0.status_code, "cookies:", list(r0.cookies.keys())[:6])

        r = client.get(
            "https://www.baidu.com/s",
            params={"wd": QUERY, "ie": "utf-8"},
            headers={"User-Agent": ua, "Referer": "https://www.baidu.com/"},
        )
        html = r.text
        print("search:", r.status_code, "len:", len(html), "final_url:", r.url)
        (DUMP_DIR / "baidu_s.html").write_text(html, encoding="utf-8")
        # 反爬特征
        for mark in ("百度安全验证", "wappass.baidu.com", "请输入验证码",
                     "网络不给力", "class=\"result"):
            if mark in html:
                print("  页面含标记:", mark)
        print("h3 出现次数:", html.count("<h3"))
        print("baidu link 跳转数:", html.count("/link?url="))

        # ---------- ② 解析 ----------
        banner("② 解析结果条目")
        p = BaiduResultParser()
        p.feed(html)
        results = [x for x in p.results if x["title"]]
        print("解析出结果数:", len(results))
        for i, item in enumerate(results[:8]):
            print("  %d. %s" % (i, item["title"][:60]))
            print("     ->", item["href"][:90])

        if not results:
            print("\n[!] 未解析到结果，需人工查看 dump:", DUMP_DIR / "baidu_s.html")
            return 1

        # ---------- ③ 还原真实 URL ----------
        banner("③ 跟随百度跳转链还原真实 URL")
        resolved: List[Dict[str, Any]] = []
        for item in results[:6]:
            try:
                rr = client.get(item["href"], headers={"User-Agent": ua})
                real = str(rr.url)
            except Exception as e:
                real = "ERR: %s" % e
            item["real_url"] = real
            print("  %s -> %s" % (item["title"][:30], real[:90]))
            resolved.append(item)

        # ---------- ④ 抓正文 ----------
        banner("④ 抓取目标页并转纯文本/markdown")
        target = next((x for x in resolved
                       if urlparse(x["real_url"]).scheme.startswith("http")
                       and "baidu.com" not in urlparse(x["real_url"]).netloc), None)
        if target is None:
            print("没有可用的非百度目标，跳过正文抓取")
            return 0
        print("目标:", target["real_url"])
        cr = client.get(target["real_url"], headers={"User-Agent": ua})
        raw = cr.content
        # 编码探测：header → meta charset → 默认 utf-8
        enc = cr.charset_encoding or ""
        m = re.search(rb'charset=["\']?([a-zA-Z0-9\-_]+)', raw[:4096])
        if not enc and m:
            enc = m.group(1).decode("ascii", "ignore")
        enc = enc or "utf-8"
        try:
            text_html = raw.decode(enc, errors="ignore")
        except LookupError:
            text_html = raw.decode("utf-8", errors="ignore")
        print("encoding:", enc, "| bytes:", len(raw))

        title, body = html_to_markdown(text_html)
        print("title:", title)
        snippet = "\n".join(body)[:800]
        print("正文片段:\n" + snippet)

        # ---------- ⑤ 汇总 markdown ----------
        banner("⑤ 给 AI 的 markdown 上下文样例")
        md = ["# 百度搜索: %s\n" % QUERY]
        for i, item in enumerate(resolved[:5], 1):
            md.append("%d. [%s](%s)" % (i, item["title"], item["real_url"]))
        if title:
            md.append("\n## 首条正文: %s\n" % title)
            md.append("\n".join(body)[:1500])
        out = "\n".join(md)
        (DUMP_DIR / "baidu_search_context.md").write_text(out, encoding="utf-8")
        print(out[:1200])
        print("\n[OK] 整条链路跑通，dump 目录:", DUMP_DIR)
    return 0


# ---------- ④ HTML → markdown（轻量） ----------

class _TextExtractor(HTMLParser):
    """去掉 script/style/nav/footer，按块级标签换行，保留 h1-6/li/a 结构。"""

    SKIP_TAGS = ("script", "style", "noscript", "iframe", "svg", "form",
                 "header", "footer", "nav", "aside")
    BLOCK_TAGS = ("p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6")

    def __init__(self):
        super().__init__()
        self.parts: List[str] = []
        self.skip_depth = 0
        self.title = ""
        self._in_title = False
        self.href_stack: List[Optional[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK_TAGS:
            prefix = "\n"
            if tag in ("h1", "h2"):
                prefix = "\n\n## "
            elif tag in ("h3", "h4"):
                prefix = "\n\n### "
            elif tag == "li":
                prefix = "\n- "
            self.parts.append(prefix)
        if tag == "a":
            self.href_stack.append(dict(attrs).get("href"))
        else:
            self.href_stack.append(None)

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if self.href_stack:
            self.href_stack.pop()

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self._in_title:
            self.title += data
        t = re.sub(r"\s+", " ", data)
        if t.strip():
            self.parts.append(t)


def html_to_markdown(html_text: str):
    p = _TextExtractor()
    p.feed(html_text)
    text = "".join(p.parts)
    # 压缩多余空行
    lines = [ln.strip() for ln in text.splitlines()]
    out: List[str] = []
    blank = False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return p.title.strip(), out


if __name__ == "__main__":
    sys.exit(main())
