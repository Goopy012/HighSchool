# app.py — 초간단 웹 UI 요약기 (Streamlit 1개만 설치)
# - URL에서 HTML 내려받기(표준 라이브러리)
# - 위키 친화형 <p> 본문만 추출(HTMLParser)
# - 키워드(빈도) + 3문장 요약
# - 표/개별 카드 + 키워드 빈도 막대그래프 + CSV 다운로드

import re, io, csv
from collections import Counter
from html.parser import HTMLParser
from urllib.request import Request, urlopen

import streamlit as st

# -------------------- 설정(원하면 수정) --------------------
DEFAULT_MAX_SENTENCES = 3
DEFAULT_TOPK = 5
MAX_SENT_CHARS = 300  # 0이면 문장 자르기 비활성화
STOPWORDS = set("""
그리고 그러나 그래서 또는 또한 및 등 이 그 저 것 수 등등 에 의 은 는 이 가 을 를 으로 로 에서 부터 까지 도 만 보다 보다도
the a an and or but if then else also to of in on at for from with by as is are was were be been being this that these those
""".split())
# -----------------------------------------------------------

def fetch_html(url: str, timeout: int = 12) -> str:
    """URL에서 HTML을 텍스트로 내려받는다(간단 UA/인코딩 처리)."""
    req = Request(url, headers={"User-Agent": "MiniUI/0.1 (+https://example.com)"})
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ct = resp.headers.get("Content-Type", "")
        m = re.search(r"charset=([^\s;]+)", ct, re.I)
        enc = m.group(1) if m else "utf-8"
    return data.decode(enc, errors="ignore")

class POnlyParser(HTMLParser):
    """위키 전용: 본문 컨테이너 안의 <p>만 수집. navbox/infobox/참고문헌/목차/표 등 제외."""
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.in_p = False
        self.in_content = False
        self.content_div_depth = 0
        self.skip_stack = []
        self.exclude_stack = []
        self.title_parts = []
        self.paras = []

    def _get(self, attrs, key):
        for k, v in attrs:
            if k == key: return v or ""
        return ""

    def _has_class(self, attrs, name):
        cls = self._get(attrs, "class")
        return name in (cls.split() if cls else [])

    def _has_any_class(self, attrs, names):
        cls = self._get(attrs, "class")
        if not cls: return False
        s = set(cls.split())
        return any(n in s for n in names)

    def handle_starttag(self, tag, attrs):
        # 완전 스킵
        if tag in ("script", "style"):
            self.skip_stack.append(tag); return

        # 제외 영역
        excluded = False
        if tag in ("nav", "footer", "aside", "table"):
            excluded = True
        elif tag == "div":
            if (self._has_class(attrs, "navbox")
                or self._has_class(attrs, "mw-references-wrap")
                or self._has_class(attrs, "hatnote")
                or self._get(attrs, "id") in ("toc", "catlinks")):
                excluded = True
        elif tag in ("ol", "ul"):
            if self._has_class(attrs, "references"):
                excluded = True
        elif tag == "table":
            if self._has_class(attrs, "infobox") or self._has_class(attrs, "navbox"):
                excluded = True
        elif tag == "sup":
            if self._has_class(attrs, "reference"):
                excluded = True
        if excluded:
            self.exclude_stack.append(tag); return

        # 본문 컨테이너 (div 깊이 카운트)
        if tag == "div":
            div_id = self._get(attrs, "id")
            is_content_root = (
                div_id in ("mw-content-text", "content") or
                self._has_any_class(attrs, ["mw-parser-output","mw-body","mw-body-content","content"])
            )
            if is_content_root:
                self.content_div_depth += 1
                self.in_content = True
            elif self.in_content:
                self.content_div_depth += 1

        # 실 수집 태그
        if tag == "p" and (self.in_content or self.content_div_depth == 0) and not self.exclude_stack:
            self.in_p = True
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        # 스킵 종료
        if tag in ("script", "style"):
            if self.skip_stack and self.skip_stack[-1] == tag:
                self.skip_stack.pop()
            return
        # 제외 영역 종료
        if self.exclude_stack and self.exclude_stack[-1] == tag:
            self.exclude_stack.pop()
        # 본문 컨테이너 이탈
        if self.in_content and tag == "div":
            if self.content_div_depth > 0:
                self.content_div_depth -= 1
            if self.content_div_depth == 0:
                self.in_content = False
        # 수집 태그 종료
        if tag == "p": self.in_p = False
        if tag == "title": self.in_title = False

    def handle_data(self, data):
        if self.skip_stack or self.exclude_stack: return
        txt = (data or "").strip()
        if not txt: return
        if self.in_title:
            self.title_parts.append(txt)
        elif self.in_p and (self.in_content or self.content_div_depth == 0):
            self.paras.append(txt)

    def get_title(self): return " ".join(self.title_parts).strip()
    def get_text(self):  return " ".join(self.paras).strip()

def split_sentences(text: str):
    """위키 각주/대괄호 대응 문장 분리."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t: return []
    t = re.sub(r"\[[^\]]+\]", " ", t)  # [1], [주 2] 등 제거
    parts = re.split(r'(?<=[.!?])(?=\s|\[)|(?<=다\.)(?=\s|\[)', t)
    return [p.strip() for p in parts if len(p.strip()) > 2]

def tokenize(text: str):
    """영문/한글 2글자 이상 토큰만 추출."""
    return [t.lower() for t in re.findall(r"[A-Za-z가-힣]{2,}", text or "")]

def keyword_topk(text: str, k: int = DEFAULT_TOPK):
    """불용어 제외 후 빈도 상위 k개 단어."""
    freq = Counter(t for t in tokenize(text) if t not in STOPWORDS)
    return [w for w,_ in freq.most_common(k)], freq

def summarize(text: str, max_sentences: int = DEFAULT_MAX_SENTENCES):
    """빈도 기반 상위 문장 요약."""
    sents = split_sentences(text)
    if not sents: return ""
    if len(sents) <= max_sentences:
        out = " ".join(sents); 
        return out if MAX_SENT_CHARS<=0 else (out if len(out)<=MAX_SENT_CHARS else out[:MAX_SENT_CHARS-1]+"…")
    # 전체 빈도
    global_freq = Counter(t for t in tokenize(text) if t not in STOPWORDS)
    # 문장 점수
    scores = []
    for s in sents:
        score = sum(global_freq.get(t,0) for t in tokenize(s) if t not in STOPWORDS)
        scores.append(score)
    top_idx = sorted(sorted(range(len(sents)), key=lambda i: scores[i], reverse=True)[:max_sentences])
    chosen = [sents[i] for i in top_idx]
    if MAX_SENT_CHARS>0:
        chosen = [s if len(s)<=MAX_SENT_CHARS else s[:MAX_SENT_CHARS-1]+"…" for s in chosen]
    return " ".join(chosen)

def process_one(url, max_sentences=DEFAULT_MAX_SENTENCES, topk=DEFAULT_TOPK):
    try:
        html = fetch_html(url)
        p = POnlyParser(); p.feed(html)
        title = p.get_title(); body = p.get_text()
        if not body:
            return {"url": url, "title": title, "keywords": "", "summary": "(본문 추출 실패)", "ok": False, "freq": Counter()}
        kws, freq = keyword_topk(body, k=topk)
        summ = summarize(body, max_sentences=max_sentences)
        return {"url": url, "title": title, "keywords": ", ".join(kws), "summary": summ, "ok": True, "freq": freq}
    except Exception as e:
        return {"url": url, "title": "", "keywords": "", "summary": f"(에러: {e})", "ok": False, "freq": Counter()}

# ======================= Streamlit UI =======================
st.set_page_config(page_title="초간단 요약기", page_icon="📝", layout="centered")
st.title("📝 초간단 URL 요약기 (한 파일)")

st.caption("URL을 줄마다 입력 → 실행을 누르면, 문서별 **키워드/3문장 요약**과 **키워드 빈도 그래프**가 보여요.")

sample = """https://ko.wikipedia.org/wiki/%EB%8C%80%ED%95%9C%EB%AF%BC%EA%B5%AD
https://ko.wikipedia.org/wiki/%EC%88%98%ED%96%89%ED%8F%89%EA%B0%80"""
urls_text = st.text_area("URL 목록 (줄마다 1개)", height=140, value=sample)

c1, c2, c3 = st.columns(3)
max_sent = c1.number_input("요약 문장 수", 1, 8, value=DEFAULT_MAX_SENTENCES, step=1)
topk = c2.number_input("키워드 개수", 3, 15, value=DEFAULT_TOPK, step=1)

run = c3.button("실행")

if run:
    urls = [u.strip() for u in urls_text.splitlines() if u.strip() and not u.strip().startswith("#")]
    if not urls:
        st.warning("URL을 입력하세요.")
    else:
        results = [process_one(u, max_sentences=max_sent, topk=topk) for u in urls]

        # 표로 전체 결과 보기
        table_rows = [{"url": r["url"], "title": r["title"], "keywords": r["keywords"], "summary": r["summary"]} for r in results]
        st.subheader("📋 결과 표")
        st.dataframe(table_rows, use_container_width=True)

        # 개별 카드
        st.subheader("🧾 문서별 요약")
        for r in results:
            with st.expander(r["title"] or r["url"], expanded=False):
                st.markdown(f"**URL:** {r['url']}")
                st.markdown(f"**키워드:** {r['keywords'] or '(없음)'}")
                st.markdown(f"**요약({max_sent}문장):** {r['summary']}")

        # 키워드 전체 빈도 시각화
        st.subheader("📊 전체 키워드 빈도 (상위 15)")
        total = Counter()
        for r in results:
            total.update(r["freq"])
        # 불용어 제거된 전체 분포에서 상위 15개만
        top_items = total.most_common(15)
        if top_items:
            import pandas as pd
            df = pd.DataFrame(top_items, columns=["keyword", "count"]).set_index("keyword")
            st.bar_chart(df)  # 간단 막대그래프
        else:
            st.info("표시할 키워드가 없어요.")

        # CSV 다운로드
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["url", "title", "keywords", "summary"])
        w.writeheader(); w.writerows(table_rows)
        st.download_button("CSV 다운로드", data=buf.getvalue().encode("utf-8-sig"), file_name="results.csv", mime="text/csv")
