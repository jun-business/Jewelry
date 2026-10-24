"""D-30 카카오톡 채널 대화 붙여넣기·내보내기 파일 가져오기 — 비용·사업자등록 없는 통합."""
from datetime import date

from helpers import get, new_order

from jewelry_os.services import cs, kakao_import
from webutil import web

MOBILE = """김하늘 님과 카카오톡 대화
저장한 날짜 : 2026년 10월 24일 오후 5:00

2026년 10월 23일 오후 3:15, 김하늘 : 안녕하세요 반지 제작 문의드려요
2026년 10월 23일 오후 3:16, 김하늘 : 12호로 가능할까요?
연락은 010-1234-5678로 주세요
2026년 10월 23일 오후 3:20, 틴틴골드 : 네 가능해요. 문의 폼으로 남겨 주세요
"""

PC = """--------------- 2026년 10월 24일 토요일 ---------------
[김하늘] [오전 10:05] 폼 작성했어요
[틴틴골드] [오전 10:07] 확인했어요 감사합니다
[김하늘] [오전 10:08] 사진
"""


def test_parse_mobile_and_pc_formats():
    ms = kakao_import.parse(MOBILE)
    assert [(m.sender, m.at.hour, m.at.minute) for m in ms] == [("김하늘", 15, 15), ("김하늘", 15, 16), ("틴틴골드", 15, 20)]
    assert "010-1234-5678로 주세요" in ms[1].text  # 이어지는 줄은 앞 메시지에
    ps = kakao_import.parse(PC)
    assert [(m.sender, m.at.date(), m.at.hour) for m in ps] == [("김하늘", date(2026, 10, 24), 10), ("틴틴골드", date(2026, 10, 24), 10),
                                                               ("김하늘", date(2026, 10, 24), 10)]
    ios = kakao_import.parse("2026. 10. 24. 오후 9:05, 김하늘 : 내일 연락드릴게요")
    assert ios[0].at.hour == 21 and ios[0].sender == "김하늘"
    assert kakao_import.parse("그냥 메모 한 줄")[0].text == "그냥 메모 한 줄"  # 형식 몰라도 한 덩어리로
    assert kakao_import.parse("   \n  ") == []


def test_import_into_order_dedupes_and_redacts(ctx, maker, operator):
    no = new_order(ctx, "r1")
    o = get(ctx, no)
    st = kakao_import.import_chat(ctx, operator, MOBILE, order=o)
    assert (st["new"], st["dup"]) == (3, 0)
    st2 = kakao_import.import_chat(ctx, operator, MOBILE + PC, order=o)  # 다시 붙여도 새 것만
    assert (st2["new"], st2["dup"]) == (3, 3)
    msgs = cs.messages_of(ctx, o["id"])
    assert len(msgs) == 6 and [m["direction"] for m in msgs][:3] == ["in", "in", "out"]
    assert all("1234-5678" not in (m["summary"] + (m["content_redacted"] or "")) for m in msgs)  # 전화번호 가림
    assert msgs[0]["event_at"] == "2026-10-23T06:15:00+00:00"  # KST 15:15 → UTC
    page = web(ctx, "operator").get(f"/orders/{no}/cs").text
    assert "카카오톡 대화 붙여넣기" in page and "안녕하세요 반지 제작 문의드려요" in page


def test_inquiry_without_order_then_link(ctx, maker, operator):
    w = web(ctx, "operator")
    r = w.post("/cs/import", data={"title": "", "chat": MOBILE})
    assert r.status_code == 303 and r.headers["location"].startswith("/cs/threads/")
    tid = int(r.headers["location"].rsplit("/", 1)[1])
    assert "김하늘 카카오톡 문의" in w.get("/cs").text
    page = w.get(f"/cs/threads/{tid}").text
    assert "12호로 가능할까요?" in page and "주문에 연결" in page
    no = new_order(ctx, "r1")
    o = get(ctx, no)
    kakao_import.import_chat(ctx, operator, PC, order=o)  # 주문에 이미 대화가 있는 경우 → 합치기
    assert w.post(f"/cs/threads/{tid}/link", data={"order_no": "JOS-NOPE"}).status_code == 303
    assert kakao_import.thread(ctx, tid)["order_id"] is None
    r = w.post(f"/cs/threads/{tid}/link", data={"order_no": no})
    assert r.status_code == 303 and r.headers["location"] == f"/orders/{no}/cs"
    assert len(cs.messages_of(ctx, o["id"])) == 6 and kakao_import.open_threads(ctx) == []
    assert w.get(f"/cs/threads/{tid}", follow_redirects=False).headers["location"] == f"/orders/{no}/cs"
    assert web(ctx, "maker").post("/cs/import", data={"chat": PC}).status_code == 303  # 아버님도 붙여넣기 가능


def test_txt_file_upload_and_empty(ctx, maker, operator):
    no = new_order(ctx, "r1")
    w = web(ctx, "operator")
    r = w.post(f"/orders/{no}/cs/import", data={"chat": ""}, files={"chat_file": ("talk.txt", MOBILE.encode("cp949"), "text/plain")})
    assert r.status_code == 303 and len(cs.messages_of(ctx, get(ctx, no)["id"])) == 3  # 윈도우 한글 인코딩 파일도
    r = w.post(f"/orders/{no}/cs/import", data={"chat": "  "})
    assert r.status_code == 303 and len(cs.messages_of(ctx, get(ctx, no)["id"])) == 3
