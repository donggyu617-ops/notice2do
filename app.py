import json
from datetime import datetime, timezone
import streamlit as st
from openai import OpenAI

# PDF는 선택 기능 (PyMuPDF 설치되어 있으면 사용)
try:
    import fitz  # PyMuPDF
    HAS_PDF = True
except Exception:
    HAS_PDF = False

st.set_page_config(page_title="Notice2Do", page_icon="🗓️", layout="centered")
st.title("Notice2Do: 공지/과제 → 요약·할일·캘린더")
st.caption("텍스트(권장) 또는 PDF(텍스트 기반)로 입력하면 요약/할일/마감일을 구조화하고 .ics 파일을 내려줍니다.")

def extract_text_from_pdf(uploaded_file) -> str:
    if not HAS_PDF:
        return ""
    data = uploaded_file.getvalue()
    doc = fitz.open(stream=data, filetype="pdf")
    return "\n".join(page.get_text("text") for page in doc).strip()

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 8},
        "tasks": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "task": {"type": "string"},
                    "due_local": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "priority": {"type": "string", "enum": ["high", "mid", "low"]},
                    "source_quote": {"type": "string"}
                },
                "required": ["task", "due_local", "priority", "source_quote"]
            }
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["title", "summary", "tasks", "uncertainties"]
}

def call_ai(raw_text: str) -> dict:
    # Streamlit secrets에서 API 키 읽기
    api_key = (st.secrets.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 없습니다. .streamlit/secrets.toml에 넣어주세요.")

    # ✅ 키에 비ASCII(한글/특수 유니코드) 섞였는지 검사 (키 내용은 출력 안 함)
    bad = [(i, ord(ch)) for i, ch in enumerate(api_key) if ord(ch) > 127]
    if bad:
        raise RuntimeError(
            f"OPENAI_API_KEY에 비ASCII 문자가 섞여 있습니다. 위치/코드: {bad[:10]} "
            f"(예: 65279는 BOM, 8203은 제로폭 공백)"
        )

    if not api_key.startswith("sk-"):
        raise RuntimeError("OPENAI_API_KEY 형식이 이상합니다. sk-로 시작하는 실제 키를 넣어주세요.")

    client = OpenAI(api_key=api_key)

    system = (
        "너는 '공지/과제 정리 비서'다. "
        "원문에서 확인 가능한 정보만 사용하고, 날짜/시간이 없으면 추정하지 말고 null로 둬라. "
        "요약은 짧고 명확하게, 할 일은 실행 가능한 형태로 써라."
    )

    resp = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"[원문]\n{raw_text}\n"},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "notice2do",
                "strict": True,
                "schema": SCHEMA
            }
        }
    )
    return json.loads(resp.output_text)

def to_ics(tasks, tzid="Asia/Seoul") -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Notice2Do//KR//EN",
        "CALSCALE:GREGORIAN",
    ]
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for i, t in enumerate(tasks):
        due = t.get("due_local")

        # None 또는 "null"/"none" 같은 문자열이면 스킵
        if due is None:
            continue

        if isinstance(due, str):
            due = due.strip()
            if due.lower() in ("null", "none", ""):
                continue
            due = due.replace("T", " ")

        try:
            dt = datetime.strptime(due, "%Y-%m-%d %H:%M")
        except ValueError:
            continue

        dtstart = dt.strftime("%Y%m%dT%H%M%S")
        dtend = dt.strftime("%Y%m%dT%H%M%S")
        uid = f"notice2do-{i}-{dtstart}@local"
        summary = (t.get("task") or "할 일").replace("\n", " ").strip()

        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_utc}",
            f"DTSTART;TZID={tzid}:{dtstart}",
            f"DTEND;TZID={tzid}:{dtend}",
            f"SUMMARY:{summary}",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\n".join(lines)


mode = st.radio("입력 방식", ["텍스트 붙여넣기", "PDF 업로드(선택)"], horizontal=True)

raw_text = ""
if mode == "텍스트 붙여넣기":
    raw_text = st.text_area("공지/과제 내용을 붙여넣으세요", height=220)
else:
    if not HAS_PDF:
        st.info("PDF 기능은 PyMuPDF 설치가 필요합니다. (이미 설치했다면 이 문구는 안 떠요)")
    uploaded = st.file_uploader("PDF 업로드(텍스트 기반 PDF 권장)", type=["pdf"])
    if uploaded and HAS_PDF:
        raw_text = extract_text_from_pdf(uploaded)

if st.button("정리하기", type="primary", use_container_width=True):
    if not raw_text or len(raw_text.strip()) < 30:
        st.warning("내용이 너무 짧습니다. 공지/과제 내용을 더 넣어주세요.")
        st.stop()

    with st.spinner("AI가 정리 중..."):
        data = call_ai(raw_text)

    st.subheader(data["title"])

    st.markdown("### 핵심 요약")
    for s in data["summary"]:
        st.write("• " + s)

    st.markdown("### 할 일 체크리스트")
    for t in data["tasks"]:
        label = f"[{t['priority']}] {t['task']}"
        if t["due_local"]:
            label += f" (마감: {t['due_local']})"
        st.checkbox(label, value=False)
        if t["source_quote"]:
            st.caption(f"근거: {t['source_quote']}")

    st.markdown("### 확인 필요")
    for u in data["uncertainties"]:
        st.write("• " + u)

    st.download_button(
        "캘린더(.ics) 다운로드",
        data=to_ics(data["tasks"]),
        file_name="notice2do.ics",
        mime="text/calendar",
        use_container_width=True
    )
