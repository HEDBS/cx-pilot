# -*- coding: utf-8 -*-
"""Provider 抽象 + 注册表。所有后端说同一种语言：
    solve_choice(stem, options) -> ChoiceResult(answer, confidence, source)
    solve_text(prompt, max_tokens) -> str        # 生成类通用入口
配置来自 data/api_keys.json + data/settings.json，UI 可改。
"""
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from core.client import DATA  # 统一数据目录（%APPDATA%\cx-pilot）
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


class ProviderError(Exception):
    pass


def load_keys():
    p = os.path.join(DATA, "api_keys.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def load_settings():
    p = os.path.join(DATA, "settings.json")
    default = {
        "providers": [
            # 每项: {"kind":"openai","name":"...","base_url":"...","model":"...",
            #        "key_ref":"groq"(指向api_keys.json的键,可空),"enabled":false}
            {"kind": "pollinations", "name": "Pollinations(匿名,慢)", "model": "openai",
             "enabled": True},
            {"kind": "openai", "name": "Groq", "base_url": "https://api.groq.com/openai/v1",
             "model": "llama-3.3-70b-versatile", "key_ref": "groq", "enabled": False},
            {"kind": "openai", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
             "model": "meta-llama/llama-3.3-70b-instruct:free", "key_ref": "openrouter",
             "enabled": False},
            {"kind": "openai", "name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
             "model": "Qwen/Qwen2.5-7B-Instruct", "key_ref": "siliconflow", "enabled": False},
            {"kind": "openai", "name": "自定义", "base_url": "", "model": "",
             "key_ref": "custom", "enabled": False},
            {"kind": "local", "name": "本地llama.cpp", "base_url": "http://127.0.0.1:18080/v1",
             "model": "qwen3-1.7b", "enabled": False},
        ],
        # B6a 视图API（识图兜底）：每项 {"kind":"openai_compat","name":"...","base_url":"...",
        # "model":"...","key_ref":"..."或"api_key":"直接粘贴的key","enabled":true}。
        # 不迁移旧文件：缺省即空（识图链只剩本地 OCR，全挂走人工）。
        "vision_providers": [],
        # M3 C1：默认直连（空=不用代理）——陌生干净机器上没有任何理由假设本地代理端口在跑。
        # 用户在 settings.json 里显式写过的 proxy/use_proxy_for 经 merge 照常沿用。
        "proxy": "",
        "use_proxy_for": [],  # key_ref/name 命中则走代理
        "choice_strategy": "generate",   # generate | logprob(local)
        "confidence_threshold": 0.75,
        "rate_limit_s": [1.0, 2.5],
    }
    if os.path.exists(p):
        try:
            saved = json.load(open(p, encoding="utf-8"))
            default.update(saved)
        except Exception:
            pass
    return default


def save_settings(s):
    with open(os.path.join(DATA, "settings.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


# ---------- M3 B1：代理连通性探测（本函数区域内新增，协议/签名零改动） ----------
# 盘上配置的 proxy（用户自设的本地代理端口）不保证在跑：旧 _opener 无条件走它 →
# 连接被拒 WinError 10061 打穿整条后端链。探测不通则临时直连并只警告一次（不刷屏），
# 结果短期缓存避免每题重复探测；通或未配置 proxy 时行为与旧版完全一致。
_PROXY_PROBE_TTL = 5.0          # 探测结果缓存秒数
_PROXY_CONNECT_TIMEOUT = 0.6    # 单次 TCP 探测超时
_proxy_probe_cache = {}         # proxy 串 -> (monotonic 时刻, 是否可达)
_proxy_warned = set()           # 每个 proxy 进程内只警告一次


def _proxy_alive(proxy):
    """proxy 的 host:port 短超时 TCP 探测；结果缓存 _PROXY_PROBE_TTL 秒。"""
    now = time.monotonic()
    hit = _proxy_probe_cache.get(proxy)
    if hit is not None and now - hit[0] < _PROXY_PROBE_TTL:
        return hit[1]
    ok = False
    try:
        u = urllib.parse.urlparse(proxy if "//" in proxy else "//" + proxy)
        host = u.hostname
        port = u.port or (443 if u.scheme == "https" else 80)
        if host:
            with socket.create_connection((host, int(port)),
                                          timeout=_PROXY_CONNECT_TIMEOUT):
                ok = True
    except OSError:
        ok = False
    _proxy_probe_cache[proxy] = (now, ok)
    return ok


def _opener(proxy=None):
    if proxy and not _proxy_alive(proxy):
        if proxy not in _proxy_warned:
            _proxy_warned.add(proxy)
            # 不用「⚠」等非 GBK 字形：Windows 控制台 cp936 print 会 UnicodeEncodeError（§4.4 同族坑）
            print("!! 代理 %s 探测不通，临时直连（%.1fs 探测超时；%ds 后重探；此警告只打一次）" %
                  (proxy, _PROXY_CONNECT_TIMEOUT, _PROXY_PROBE_TTL))
        proxy = None
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def chat(cfg, keys, messages, max_tokens=60, temperature=0.0, timeout=45):
    """统一 OpenAI 兼容 chat/completions 调用，返回文本。"""
    base = (cfg.get("base_url") or "").rstrip("/")
    key = keys.get(cfg.get("key_ref", ""), "") or cfg.get("api_key", "")
    if not base:
        raise ProviderError("base_url 未配置")
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if key:
        headers["Authorization"] = "Bearer " + key
    body = {"model": cfg.get("model"), "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature}
    settings = load_settings()
    use_proxy = any(k and (k in (cfg.get("key_ref", "") + cfg.get("name", "") + base))
                    for k in settings.get("use_proxy_for", []))
    op = _opener(settings.get("proxy") if use_proxy else None)
    data = json.dumps(body).encode()
    last = None
    for attempt in range(3):
        try:
            r = op.open(urllib.request.Request(base + "/chat/completions", data=data,
                                               headers=headers), timeout=timeout)
            j = json.loads(r.read().decode("utf-8", "replace"))
            return j["choices"][0]["message"]["content"]
        except Exception as e:
            last = e
            try:
                msg = e.read().decode("utf-8", "replace")[:150]
            except Exception:
                msg = str(e)[:150]
            if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404):
                raise ProviderError("HTTP %s: %s" % (e.code, msg))
            time.sleep(2 + attempt * 3)
    raise ProviderError(str(last)[:150])


def pollinations_chat(cfg, keys, messages, timeout=90):
    """匿名兜底：GET /<prompt>，单并发。

    M3 B1b：一切上游失败（HTTPError/URLError/超时/读解码）统一归一为 ProviderError，
    与 chat() 失败语义一致——solver 只 except ProviderError，此前原样上抛会打穿整条链。
    重试 2 次（短退避，与 chat() 对齐）；401/403/404 直接转 ProviderError 不重试。
    签名与成功返回（文本）不变。"""
    prompt = messages[-1]["content"]
    url = "https://text.pollinations.ai/" + urllib.parse.quote(prompt[:1800]) + \
          "?model=" + urllib.parse.quote(cfg.get("model", "openai"))
    settings = load_settings()
    op = _opener(settings.get("proxy"))
    last = None
    for attempt in range(3):
        try:
            r = op.open(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout)
            return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            try:
                msg = e.read().decode("utf-8", "replace")[:150]
            except Exception:
                msg = str(e)[:150]
            if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404):
                raise ProviderError("HTTP %s: %s" % (e.code, msg))
            if attempt < 2:
                time.sleep(2 + attempt * 3)
    raise ProviderError(str(last)[:150])


CHOICE_PROMPT = """你是答题引擎。从选项中选出正确答案。
先在内部思考，最后一行只输出一个字母（不要任何其他字符、不要括号）。

题目：{stem}

选项：
{options}

最后一行："""


def _extract_letter(raw, valid):
    """从模型输出提取答案字母：优先尾部/标签式，避免误抓散文里的 A/B/C/D 词。"""
    v = set(valid)
    # 1) 显式模式：答案：X / Answer: X / 最终 X
    pats = [r"正确答案[是为:：]\s*\(?([A-G])\)?", r"答案[是为:：]\s*\(?([A-G])\)?",
            r"[Aa]nswer\s*[:：]\s*\(?([A-G])\)?", r"[Ff]inal\s*答案[是为:：]?\s*\(?([A-G])\)?"]
    for p in pats:
        ms = re.findall(p, raw)
        for c in reversed(ms):
            if c in v:
                return c
    # 2) 独立的最后一行
    for line in reversed([l.strip() for l in raw.splitlines() if l.strip()]):
        m = re.fullmatch(r"[（(\[]?([A-G])[)）\]]?[.。、:：]?", line)
        if m and m.group(1) in v:
            return m.group(1)
    # 3) 全文最后一个独立字母 token
    ms = re.findall(r"(?<![A-Za-z])([A-G])(?![A-Za-z])", raw)
    for c in reversed(ms):
        if c in v:
            return c
    return ""


def solve_choice(cfg, keys, stem, options, max_tokens=400):
    """options: dict {'A':'...','B':'...'} -> (letter, confidence, raw)"""
    opt_txt = "\n".join("%s、%s" % (k, v) for k, v in sorted(options.items()))
    prompt = CHOICE_PROMPT.format(stem=stem, options=opt_txt)
    messages = [{"role": "user", "content": prompt}]
    if cfg.get("kind") == "pollinations":
        raw = pollinations_chat(cfg, keys, messages)
    else:
        raw = chat(cfg, keys, messages, max_tokens=max_tokens)
    letter = _extract_letter(raw, options.keys())
    # 置信度基线；升级路径(双采样一致性)留在 solver
    conf = 0.8 if letter in options else 0.0
    return letter, conf, raw


def active_providers(settings, keys):
    out = []
    for cfg in settings.get("providers", []):
        if not cfg.get("enabled"):
            continue
        if cfg.get("kind") == "openai":
            ref = cfg.get("key_ref", "")
            if ref and not keys.get(ref) and not cfg.get("api_key"):
                continue  # 没填 key 的跳过
            if not cfg.get("base_url"):
                continue
        out.append(cfg)
    return out
