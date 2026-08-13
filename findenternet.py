import requests as re
from bs4 import BeautifulSoup
import urllib.parse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed


SEARCH_ENGINES = ["必应", "百度", "360"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 重定向URL缓存，避免重复请求
_redirect_cache = {}


def _is_valid_url(url):
    """判断是否为有效的外部URL"""
    if not url:
        return False
    lower = url.lower()
    if lower.startswith("javascript:") or lower.startswith("#") or lower.startswith("mailto:"):
        return False
    if url == "" or url.isspace():
        return False
    return True


def _is_redirect_url(url):
    """判断是否为搜索引擎的跳转链接"""
    if not url:
        return False
    redirect_patterns = [
        "baidu.com/link?",
        "so.com/link?",
        "bing.com/ck/a?",
        "google.com/url?",
        "google.com/imgres?",
    ]
    lower = url.lower()
    return any(p in lower for p in redirect_patterns)


def _resolve_redirect(url, timeout=3):
    """跟随HTTP重定向获取最终URL"""
    if url in _redirect_cache:
        return _redirect_cache[url]
    try:
        resp = re.head(url, headers=HEADERS, allow_redirects=True, timeout=timeout)
        final = resp.url
        _redirect_cache[url] = final
        return final
    except Exception:
        try:
            resp = re.get(url, headers=HEADERS, allow_redirects=True, timeout=timeout, stream=True)
            final = resp.url
            resp.close()
            _redirect_cache[url] = final
            return final
        except Exception:
            _redirect_cache[url] = url
            return url


def _normalize_url(url):
    """清洗URL用于去重比较：去除追踪参数、统一小写域名"""
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.netloc:
            return url
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/") or "/"
        query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        tracking_params = {
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "fbclid", "gclid", "gbraid", "wbraid", "ref", "ref_src", "ref_url",
            "source", "spm", "scm", "click_source", "word", "us",
        }
        clean_params = [(k, v) for k, v in query_params if k.lower() not in tracking_params]
        clean_params.sort()
        clean_query = urllib.parse.urlencode(clean_params) if clean_params else ""
        return urllib.parse.urlunparse((parsed.scheme, netloc, path, "", clean_query, ""))
    except Exception:
        return url


def _search_bing(query, num):
    """必应搜索"""
    url = f"https://cn.bing.com/search?q={urllib.parse.quote(query)}"
    results = []
    try:
        resp = re.get(url, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for li in soup.select("li.b_algo"):
            title_el = li.select_one("h2 a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            desc_el = li.select_one(".b_caption p, .b_snippet p, p")
            desc = desc_el.get_text(strip=True) if desc_el else ""
            if title and link and _is_valid_url(link):
                results.append({"title": title, "url": link, "body": desc})
            if len(results) >= num:
                break
    except Exception as e:
        results.append({"title": f"[必应搜索出错]", "url": "", "body": str(e)})
    return results


def _search_baidu(query, num):
    """百度搜索"""
    url = f"https://www.baidu.com/s?ie=utf-8&wd={urllib.parse.quote(query)}"
    results = []
    try:
        resp = re.get(url, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for div in soup.select("div.result, div.c-container"):
            title_el = div.select_one("h3 a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            desc_el = div.select_one(".c-abstract, .c-span-last p, .c-row p, .c-result-content, p")
            desc = desc_el.get_text(strip=True) if desc_el else ""

            # 百度跳转链接处理
            if link and not link.startswith("http"):
                # 尝试从 data-log 提取真实URL
                data_log = div.get("data-log", "")
                if data_log:
                    try:
                        log_data = json.loads(data_log.replace("&quot;", '"'))
                        mu = log_data.get("mu", "")
                        if mu and mu.startswith("http"):
                            link = mu
                    except Exception:
                        pass
                # 尝试从 data-p 提取
                if not link.startswith("http"):
                    data_p = div.get("data-p", "")
                    if data_p:
                        try:
                            p_data = json.loads(data_p)
                            mu = p_data.get("mu", "")
                            if mu and mu.startswith("http"):
                                link = mu
                        except Exception:
                            pass
                # 从href的JS跳转中提取
                if not link.startswith("http"):
                    onclick = title_el.get("onclick", "")
                    if "href=" in onclick:
                        pass  # 太复杂，跳过

            if title and link and _is_valid_url(link):
                results.append({"title": title, "url": link, "body": desc})
            if len(results) >= num:
                break
    except Exception as e:
        results.append({"title": f"[百度搜索出错]", "url": "", "body": str(e)})
    return results


def _search_360(query, num):
    """360搜索"""
    url = f"https://www.so.com/s?q={urllib.parse.quote(query)}"
    results = []
    try:
        resp = re.get(url, headers=HEADERS, timeout=8)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for li in soup.select("li.res-list, li"):
            title_el = li.select_one("h3 a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            link = title_el.get("href", "")
            desc_el = li.select_one(".res-desc p, .rich-content, p")
            desc = desc_el.get_text(strip=True) if desc_el else ""

            # 360 跳转链接处理
            if link and not link.startswith("http"):
                # 优先从 data-url 提取真实URL
                data_url = li.get("data-url", "") or title_el.get("data-url", "")
                if data_url and data_url.startswith("http"):
                    link = data_url
                elif data_url and not data_url.startswith("http"):
                    # data-url 可能是编码后的，尝试从 data-p 解析
                    data_p = li.get("data-p", "")
                    if data_p:
                        try:
                            p_data = json.loads(data_p)
                            du = p_data.get("du", "") or p_data.get("url", "")
                            if du and du.startswith("http"):
                                link = du
                            elif du:
                                link = "https://www.so.com" + du
                        except Exception:
                            pass
                    if not link.startswith("http"):
                        link = data_url  # 保留原编码链接
                elif link.startswith("/"):
                    link = "https://www.so.com" + link

            if title and link and _is_valid_url(link):
                results.append({"title": title, "url": link, "body": desc})
            if len(results) >= num:
                break
    except Exception as e:
        results.append({"title": f"[360搜索出错]", "url": "", "body": str(e)})
    return results


ENGINE_SEARCHERS = {
    "必应": _search_bing,
    "百度": _search_baidu,
    "360": _search_360,
}


def find_enternet(ci, num_results=10, engines=None, resolve_redirects=True, max_results=5):
    """在多个搜索引擎上搜索并合并去重

    Args:
        ci: 搜索关键词
        num_results: 每个搜索引擎期望返回的结果数量
        engines: 指定搜索引擎列表，默认全部
        resolve_redirects: 是否尝试解析跳转链接（会稍慢但去重更准确）
        max_results: 最终输出的最大结果数，None表示不限制

    Returns:
        格式化的搜索结果字符串，已自动合并相同URL
    """
    if engines is None:
        engines = list(ENGINE_SEARCHERS.keys())

    # ---------- 并行搜索所有引擎 ----------
    valid_engines = [(name, ENGINE_SEARCHERS[name]) for name in engines if name in ENGINE_SEARCHERS]
    all_results = []

    with ThreadPoolExecutor(max_workers=len(valid_engines)) as executor:
        future_to_engine = {
            executor.submit(search_fn, ci, num_results): engine_name
            for engine_name, search_fn in valid_engines
        }
        for future in as_completed(future_to_engine):
            engine_name = future_to_engine[future]
            try:
                results = future.result()
            except Exception as e:
                results = [{"title": f"[{engine_name}搜索出错]", "url": "", "body": str(e)}]
            for r in results:
                if r.get("url"):
                    r["engine"] = engine_name
                    all_results.append(r)

    # ---------- 并行解析跳转链接（如有） ----------
    if resolve_redirects:
        need_resolve = [(i, r["url"]) for i, r in enumerate(all_results) if _is_redirect_url(r["url"])]
        resolved_map = {}
        if need_resolve:
            with ThreadPoolExecutor(max_workers=min(len(need_resolve), 10)) as executor:
                future_to_idx = {
                    executor.submit(_resolve_redirect, url): idx
                    for idx, url in need_resolve
                }
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        resolved = future.result()
                        resolved_map[idx] = resolved if resolved and not _is_redirect_url(resolved) else all_results[idx]["url"]
                    except Exception:
                        resolved_map[idx] = all_results[idx]["url"]

        for i, r in enumerate(all_results):
            if i in resolved_map:
                r["resolved_url"] = resolved_map[i]
            else:
                r["resolved_url"] = r["url"]
    else:
        for r in all_results:
            r["resolved_url"] = r["url"]

    # ---------- 按规范化真实URL合并去重 ----------
    merged = {}
    for r in all_results:
        key = _normalize_url(r["resolved_url"])
        if key not in merged:
            merged[key] = {
                "title": r["title"],
                "url": r["resolved_url"],
                "display_url": r["url"],
                "body": r["body"],
                "engines": [r["engine"]],
            }
        else:
            if r["engine"] not in merged[key]["engines"]:
                merged[key]["engines"].append(r["engine"])
            if len(r["body"]) > len(merged[key]["body"]):
                merged[key]["body"] = r["body"]

    if not merged:
        return f"未找到关于「{ci}」的搜索结果"

    # 按多引擎命中数排序
    sorted_results = sorted(merged.values(), key=lambda x: (-len(x["engines"]), x["title"]))

    # 限制输出条数
    if max_results and len(sorted_results) > max_results:
        sorted_results = sorted_results[:max_results]

    output = f"共{len(sorted_results)}条结果({'+'.join(engines)}):\n"
    for i, r in enumerate(sorted_results, 1):
        output += f"{i}. [{''.join(e[0] for e in r['engines'])}] {r['title']}\n   {r['url']}"
        if r["body"]:
            output += f"\n   {r['body'][:100]}"
        output += "\n"

    return output


if __name__ == "__main__":
    result = find_enternet("python", max_results=5)
    print(result)