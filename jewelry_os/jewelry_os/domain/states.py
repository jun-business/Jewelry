"""상태·라벨·대외 매핑·큐 (BD-001 🔒FROZEN). 문구 원본: PDS §5.2."""
from __future__ import annotations

from enum import Enum


class S(str, Enum):
    INQUIRY_RECEIVED = "INQUIRY_RECEIVED"
    AI_BRIEF_READY = "AI_BRIEF_READY"
    OWNER_REVIEW = "OWNER_REVIEW"
    FREE_CONCEPT = "FREE_CONCEPT"
    QUOTE_APPROVED = "QUOTE_APPROVED"
    CUSTOMER_ACCEPTED = "CUSTOMER_ACCEPTED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    INITIAL_PAYMENT_VERIFIED = "INITIAL_PAYMENT_VERIFIED"
    DETAIL_CAD = "DETAIL_CAD"
    CUSTOMER_DESIGN_APPROVED = "CUSTOMER_DESIGN_APPROVED"
    FACTORY_SENT = "FACTORY_SENT"
    FACTORY_RECEIVED = "FACTORY_RECEIVED"
    QC_PASSED = "QC_PASSED"
    FINAL_PAYMENT_VERIFIED = "FINAL_PAYMENT_VERIFIED"
    SHIPPED = "SHIPPED"
    CLOSED = "CLOSED"
    DECLINED = "DECLINED"
    CANCELLED = "CANCELLED"


ORDERED = list(S)[:16]  # 정상 진행 순서
ENDED = {S.CLOSED, S.DECLINED, S.CANCELLED}
ACTIVE_ACCEPTED = set(ORDERED[ORDERED.index(S.FREE_CONCEPT): ORDERED.index(S.SHIPPED)])  # 상한 계산(EXP-005)

LABEL = {
    S.INQUIRY_RECEIVED: "접수됨", S.AI_BRIEF_READY: "AI 정리 완료", S.OWNER_REVIEW: "수락 검토",
    S.FREE_CONCEPT: "무료 시안 작업", S.QUOTE_APPROVED: "견적 동의 대기", S.CUSTOMER_ACCEPTED: "고객 동의 완료",
    S.PAYMENT_PENDING: "결제 대기", S.INITIAL_PAYMENT_VERIFIED: "첫 결제 확인됨", S.DETAIL_CAD: "상세 CAD 작업",
    S.CUSTOMER_DESIGN_APPROVED: "최종 시안 승인됨", S.FACTORY_SENT: "공장 제작 중", S.FACTORY_RECEIVED: "완제품 수령",
    S.QC_PASSED: "검수 통과", S.FINAL_PAYMENT_VERIFIED: "결제 완료 확인", S.SHIPPED: "발송됨", S.CLOSED: "완료",
    S.DECLINED: "거절함", S.CANCELLED: "취소됨",
}

CHIP_KIND = {s: "progress" for s in S}
CHIP_KIND.update({S.QC_PASSED: "done", S.CLOSED: "done", S.DECLINED: "ended", S.CANCELLED: "ended"})


class Phase(str, Enum):
    RECEIVED = "RECEIVED"
    CONCEPT_QUOTE = "CONCEPT_QUOTE"
    PAYMENT_PREP = "PAYMENT_PREP"
    MAKING = "MAKING"
    DELIVERY = "DELIVERY"
    ENDED = "ENDED"


PHASE_LABEL = {Phase.RECEIVED: "접수", Phase.CONCEPT_QUOTE: "시안·견적", Phase.PAYMENT_PREP: "결제·제작준비",
               Phase.MAKING: "제작 중", Phase.DELIVERY: "배송·완료", Phase.ENDED: "진행 종료"}
PHASES5 = [Phase.RECEIVED, Phase.CONCEPT_QUOTE, Phase.PAYMENT_PREP, Phase.MAKING, Phase.DELIVERY]

PHASE_OF = {
    S.INQUIRY_RECEIVED: Phase.RECEIVED, S.AI_BRIEF_READY: Phase.RECEIVED, S.OWNER_REVIEW: Phase.RECEIVED,
    S.FREE_CONCEPT: Phase.CONCEPT_QUOTE, S.QUOTE_APPROVED: Phase.CONCEPT_QUOTE, S.CUSTOMER_ACCEPTED: Phase.CONCEPT_QUOTE,
    S.PAYMENT_PENDING: Phase.PAYMENT_PREP, S.INITIAL_PAYMENT_VERIFIED: Phase.PAYMENT_PREP,
    S.DETAIL_CAD: Phase.PAYMENT_PREP, S.CUSTOMER_DESIGN_APPROVED: Phase.PAYMENT_PREP,
    S.FACTORY_SENT: Phase.MAKING, S.FACTORY_RECEIVED: Phase.MAKING,
    S.QC_PASSED: Phase.DELIVERY, S.FINAL_PAYMENT_VERIFIED: Phase.DELIVERY, S.SHIPPED: Phase.DELIVERY, S.CLOSED: Phase.DELIVERY,
    S.DECLINED: Phase.ENDED, S.CANCELLED: Phase.ENDED,
}

HOLD_LABEL = {"NEEDS_INFO": "정보 부족", "WAITLISTED": "대기", "HOLD": "보류", "NO_CONSENT": "동의 없음"}
HOLD_DESC = {"NEEDS_INFO": "고객에게 추가 정보를 요청한 상태", "WAITLISTED": "동시 진행 상한으로 순서를 기다리는 문의",
             "HOLD": "아버님이 판단을 미룬 상태", "NO_CONSENT": "개인정보 동의 기록이 없는 응답"}

ISSUE_LABEL = {"PAYMENT_MISMATCH": "결제 불일치", "REFUND_REVIEW": "환불·취소 검토", "DISPUTED": "분쟁",
               "DELIVERY_DELAY": "납기 지연", "CHANGE_REQUEST": "변경 요청", "CANCEL_REQUEST": "취소 요청",
               "REWORK": "재작업"}
ISSUE_ICON = {"PAYMENT_MISMATCH": "octagon", "REFUND_REVIEW": "octagon", "DISPUTED": "octagon",
              "DELIVERY_DELAY": "clock", "CHANGE_REQUEST": "branch", "CANCEL_REQUEST": "xoct", "REWORK": "wrench"}

DECLINE_LABEL = {"INSUFFICIENT_INFO": "정보 불충분", "UNFIT": "부적합 요청", "NO_CAPACITY": "일정 불가",
                 "CUSTOMER_WITHDREW": "고객 철회", "OTHER": "기타"}
INVALID_DECLINE = {"INSUFFICIENT_INFO", "UNFIT"}  # SRS-166
# H1 r1 Q10: 고객 변심과 제품 하자·계약 불일치를 나눈다(SRS-163 r2)
FAULT_LABEL = {"CUSTOMER_CHANGE": "고객 변심·요청 변경", "DEFECT": "제품 하자(판매자 귀책)",
               "MISMATCH": "계약 사양 불일치(판매자 귀책)", "UNKNOWN": "불명"}
FAULT_LABEL_ALL = {**FAULT_LABEL, "SELLER": "판매자 귀책(이전 기록)"}  # 표시 전용 — 새 입력은 받지 않음
SELLER_FAULTS = {"DEFECT", "MISMATCH", "SELLER"}

SOURCE_LABEL = {"smartstore": "스마트스토어", "instagram": "인스타그램", "referral": "지인 소개", "ad": "광고",
                "naver_search": "네이버 검색", "naver_blog": "네이버 블로그", "other": "기타", "manual": "수기 접수", None: "미분류"}

PAY_PHASE_LABEL = {"DEPOSIT": "예약금", "BALANCE": "잔금", "FULL": "전액", "CHANGE": "변경 추가금"}
TAX_TYPE_LABEL = {"unset": "미설정", "general": "일반과세자", "simplified": "간이과세자", "exempt": "면세사업자"}
PAY_STATUS_LABEL = {"DRAFT": "준비 중", "LINK_READY": "구매 링크 준비됨", "PAID": "결제 확인 대기", "PAID_VERIFIED": "확인됨", "MISMATCH": "불일치",
                    "CANCELLED": "취소됨", "NOT_APPLICABLE": "해당 없음"}
NAVER_STATUS = {"PAYED": "결제완료", "CANCEL_REQUEST": "취소요청", "CANCELED": "취소완료", "RETURN_REQUEST": "반품요청",
                "RETURNED": "반품완료", "NOT_PAID": "미결제"}

CS_LEVEL_LABEL = {"L0": "안내(정형)", "L1": "진행 상황", "L2": "승인 필요", "L3": "판단·기록 필수"}
CS_STATUS_LABEL = {"DRAFT": "초안", "APPROVED": "승인됨", "SENT": "발송 기록됨", "SUPERSEDED": "고쳐 씀"}

ROLE_LABEL = {"maker": "아버님", "operator": "운영자", "system": "시스템"}

WORK_KIND_LABEL = {"concept": "무료 시안", "cad": "상세 CAD", "factory": "발주", "qc": "검수", "cs": "상담"}

ASSET_KIND_LABEL = {"reference": "참고 이미지", "concept": "시안", "cad": "CAD 원본", "preview": "시안 이미지",
                    "stl": "STL", "spec": "사양서", "qc_photo": "검수 사진", "evidence": "증빙", "packet": "발주 패킷"}

QC_ITEMS = [("spec", "사양 일치"), ("purity_engraving", "금속 함량·각인"), ("size", "규격(호수)"),
            ("finish", "외관"), ("setting", "세팅"), ("fit", "착용감")]

SPEC_FIELDS = [("material", "소재"), ("purity", "함량"), ("size", "호수·규격"), ("stones", "스톤"), ("engraving", "각인")]
# Form B v1.1(D-25): 견적서에 주문별로 적는 개별 제작 특성·청약철회 시 예상 손해 사유(고객 문서에 표시, 계약 증거)
CONTRACT_SPEC_FIELDS = [("custom_made", "개별 주문제작 특성"), ("loss_reasons", "청약철회 시 예상되는 회복하기 어려운 손해 사유")]
FACTORY_SPEC_FIELDS = SPEC_FIELDS + [("finish", "도금·마감"), ("special", "특수 지시")]

GATES = [
    ("GATE-F-01", "폼 이미지 로그인 부담", "Google 이미지 로그인 부담/폼 완료율 테스트, 고객 이탈 대안"),
    ("GATE-SS-01", "스마트스토어 상품 POC", "실제 구매 가능한 상품 URL/가격/상품 식별·타고객 구매·플랫폼 정책 POC"),
    ("GATE-SS-02", "분할 결제 적합성", "예약금 1/3·잔금 2/3 판매/배송/반품/정산 적합성"),
    ("GATE-Q-01", "견적 원가 근거", "사전 확정 견적의 예상 공장원가/마진 근거와 3건 사례 검증"),
    ("GATE-CS-01", "카카오 공급업체", "공급업체 계약·비용/콘텐츠/24h 중단 복구/서명·세션 검증"),
    ("GATE-OPS-01", "운영 안전", "주문 멱등성·백업·접근제어·승인 규칙·장애 테스트"),
    ("GATE-LEGAL-01", "법률·정책 적합성", "개인정보/주문제작 취소·환불/지재권/플랫폼 판매정책 적합성"),
    ("GATE-REMOTE-01", "원격 접속", "HTTPS, 인증·권한·PC OFF/웹훅 복구 확인"),
    ("GATE-CAP-01", "수용량 정책", "가용시간/동시 수용량/무료 시안 공수 및 회신정책 확정"),
]
TEST_MODE_GATES = ("GATE-OPS-01", "GATE-SS-02", "GATE-LEGAL-01")
LIVE_INTAKE_GATES = ("GATE-OPS-01", "GATE-LEGAL-01")

# EXP-002 큐 대응표 — 술어는 services/orders.py가 SQL로 구현(같은 이름을 쓴다)
QUEUES = [
    ("review", "검토 필요"), ("needs_info", "정보 부족"), ("waitlisted", "대기"), ("concept", "시안·견적"),
    ("payment", "결제 확인"), ("making", "제작"), ("issues", "지연·이슈"), ("delivery", "배송"),
    ("all", "전체"), ("ended", "종료"),
]
QUEUE_STATES = {
    "review": [S.INQUIRY_RECEIVED, S.AI_BRIEF_READY, S.OWNER_REVIEW],
    "concept": [S.FREE_CONCEPT, S.QUOTE_APPROVED, S.CUSTOMER_ACCEPTED],
    "payment": [S.PAYMENT_PENDING, S.QC_PASSED],
    "making": [S.INITIAL_PAYMENT_VERIFIED, S.DETAIL_CAD, S.CUSTOMER_DESIGN_APPROVED, S.FACTORY_SENT, S.FACTORY_RECEIVED],
    "delivery": [S.FINAL_PAYMENT_VERIFIED, S.SHIPPED],
    "ended": [S.CLOSED, S.DECLINED, S.CANCELLED],
}


def label(state: str) -> str:
    return LABEL[S(state)]


def phase_of(state: str) -> Phase:
    return PHASE_OF[S(state)]
