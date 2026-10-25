"""차단 문구 카탈로그(BD-003, 원본 PDS §5.4). 화면·서버 409가 같은 문구를 쓴다."""
from __future__ import annotations

from dataclasses import dataclass

MESSAGES = {
    "G-ROLE-MAKER": "아버님(제작 책임) 계정만 할 수 있어요.",
    "G-ROLE-OPERATOR": "운영자 계정만 할 수 있어요.",
    "G-HOLIDAY": "오늘은 휴무일이라 새 주문을 수락하지 않아요.",
    "G-CAPACITY": "동시 진행 {n}/{limit}건이에요. 상한 초과 예외 승인(사유·예상 납기)을 먼저 기록해야 수락할 수 있어요.",
    "G-CAPACITY-FEASIBLE": "제작 가능 여부를 확인했다고 체크해야 수락할 수 있어요.",
    "G-ETA": "예상 납기를 입력해야 해요.",
    "G-CHANGE-ORDER": "고객 변심 재작업은 {fields}을(를) 마쳐야 진행할 수 있어요.",
    "G-CHANGE-ORDER-STEP": "{step} 단계가 먼저 필요해요.",
    "G-CHANGE-ORDER-LOCKED": "고객 동의를 기록한 변경은 고칠 수 없어요. 새 재작업 문제로 기록하세요.",
    "G-NO-CONSENT": "개인정보 동의 기록이 없어요. 동의를 받은 뒤 진행하세요.",
    "G-ISSUE-OPEN": "{issue_label} 문제가 열려 있어요.",
    "G-NO-CONCEPT": "무료 시안이 아직 없어요.",
    "G-QUOTE-MISSING": "승인할 견적이 없어요.",
    "G-QUOTE-FIELDS": "견적에 {fields} 항목이 비어 있어요.",
    "G-QUOTE-TOTAL": "품목 합계({sum})와 총액({total})이 달라요.",
    "G-ACCEPT-EVIDENCE": "고객 동의 증빙(채널·근거)을 입력해야 해요.",
    "G-CONTRACT-REQUIRED": "지금 견적 버전에 유효한 계약 동의(Form B)가 없어요. 계약 동의 링크를 보내고 고객 동의·본인 확인을 마치세요.",
    "G-CONTRACT-FORM": "계약 동의 폼(Form B)을 쓸 수 없어요: {reason}",
    "G-CONTRACT-STATE": "계약 요청이 ‘{status}’ 상태라 할 수 없어요.",
    "G-CONTRACT-REVIEW": "본인 확인 방법을 고르고 확인 근거를 메모로 남겨야 해요.",
    "G-CONTRACT-DELIVERY": "보낸 채널·보낸 항목(견적서·사전 고지·링크 중 하나 이상)을 고르고 메모(예: 카카오톡 상담방에 보냄)를 남겨야 해요.",
    "G-LOSS-REVIEW-FIRST": "지금 문구로 손해 사유 검토(AI)를 먼저 실행하세요. 문구가 바뀌면 다시 검토해요.",
    "G-LOSS-OVERRIDE-NOTE": "AI 검토가 ‘충분’이 아니에요. 그대로 진행하려면 판단 근거를 적어 주세요(또는 문구를 고치세요).",
    "G-LOSS-CONFIRM": "견적서의 개별 제작 특성·손해 사유를 사람이 최종 확인해야 계약 동의 링크를 만들 수 있어요(시안·견적 탭 › 청약철회 고지 검토).",
    "G-REFUND-INPUT": "{fields}을(를) 입력해야 해요.",
    "G-REFUND-STATE": "환불 건이 ‘{status}’ 상태라 할 수 없어요.",
    "G-REFUND-LEGAL": "법적 검토(청약철회 가능 여부·법정 권리) 결과를 먼저 기록해야 승인할 수 있어요.",
    "G-QC-PHOTO": "검수 사진이 없어요. 각인·스톤 세팅이 보이는 확대 사진을 한 장 이상 올려야 검수 통과로 기록돼요(하자 문의 때 증거).",
    "G-REFUNDED": "받은 결제를 모두 돌려준 주문이에요. 끝내려면 ‘주문 취소’로 정리하고, 계속하려면 결제를 다시 받으세요.",
    "G-QUOTE-EXPIRED": "견적 유효기간이 지났어요. 새 견적 버전을 승인한 뒤 계약 동의 링크를 만드세요.",
    "G-BALANCE-BEFORE-FACTORY": "잔금(2/3) 결제가 확인되지 않아 공장 발주를 할 수 없어요.",
    "G-PAYMENT-REQUEST": "결제 요청이 아직 없어요.",
    "G-PAYMENT-UNVERIFIED": "첫 결제가 확인되지 않았어요.",
    "G-PAYMENT-EVIDENCE": "네이버 주문번호·상품주문번호·금액·상태·조회 시각을 모두 입력해야 해요.",
    "G-DESIGN-MISSING": "올린 디자인 파일(CAD·STL)이 없어요.",
    "G-DESIGN-UNAPPROVED": "고객이 최종 시안에 동의한 기록이 없어요.",
    "G-SPEC-INCOMPLETE": "사양서의 {fields} 항목이 비어 있어요. 해당 없으면 ‘해당 없음’을 고르세요.",
    "G-PACKET-MISSING": "발주 패킷이 없어요.",
    "G-PACKET-STALE": "패킷을 만든 뒤 새 디자인이 올라왔어요. 다시 승인받아야 해요.",
    "G-PACKET-HASH": "패킷 파일이 바뀌었어요(해시 불일치). 패킷을 다시 만드세요.",
    "G-CONFIRM": "확인란을 체크해야 진행돼요.",
    "G-REASON": "사유를 적어야 해요.",
    "G-RECEIVE-INFO": "수령일과 실제 공장 비용을 입력해야 해요.",
    "G-QC-INCOMPLETE": "검수표에 확인하지 않은 항목이 있어요.",
    "G-QC-FAIL": "검수에서 통과하지 못한 항목이 있어요.",
    "G-BALANCE": "잔금이 확인되지 않았어요.",
    "G-SHIP-INFO": "수취인·배송 방법·플랫폼 판매 상태를 입력해야 해요.",
    "G-DELIVERY": "배달 완료를 확인하고, 열린 문제가 없어야 해요.",
    "G-REVERT": "이 단계 뒤에 결제·발주·승인 기록이 생겨 되돌릴 수 없어요.",
    "G-SYSTEM-ONLY": "이 단계는 시스템이 처리해요.",
    "G-ENDED": "이미 끝난 주문이에요.",
    "G-HOLD": "{hold_label} 상태라 진행할 수 없어요. ({hold_desc})",
    "G-L3-JUDGMENT": "운영자 판단 기록이 먼저 필요해요(L3).",
    "G-CS-PASTE": "붙여넣은 내용에서 카카오톡 대화를 찾지 못했어요. 대화를 복사해 붙여넣거나 내보낸 .txt 파일을 올리세요.",
    "G-CS-UNAPPROVED": "아버님이 승인해야 복사할 수 있어요.",
    "G-DECLINE-CATEGORY": "거절 분류를 골라야 해요.",
    "G-FAULT": "귀책 구분을 골라야 해요.",
    "G-SPEC-DIFF": "견적 사양과 공장 사양이 달라요({fields}). 공장 사양을 견적에 맞추거나, 변경 요청으로 고객 동의를 다시 받으세요.",
    "G-TEST-QUOTE": "시험 견적으로는 실거래 결제 요청을 만들 수 없어요.",
    "G-GATE-LIVE": "고객 공개 폼은 Gate OPS-01·LEGAL-01 통과 후에 연결할 수 있어요. 지금은 시험 폼만 연결돼요.",
    "G-WRONG-STATE": "지금 단계에서는 할 수 없는 일이에요.",
    "G-CONFLICT": "다른 사람이 먼저 바꿨어요. 새 내용을 확인한 뒤 다시 시도하세요.",
    "G-CONSENT-FIRST": "개인정보 동의를 먼저 받아 기록하세요.",
    "G-NOT-OPEN-CHANGE": "열린 변경 요청이 없어요.",
    "G-ISSUE-RESOLVE": "이 문제를 해결하려면 {fields}을(를) 입력해야 해요.",
    "G-CHANGE-AFTER-FACTORY": "공장 발주 뒤에는 변경 요청 대신 재작업 문제로 기록하세요. 고객 변심이면 변경 가능 판정·추가 견적·고객 동의·결제 확인을 거쳐요.",
}

WARNINGS = {
    "W-TEST-MODE": "시험 운영 모드예요. 실제 고객 결제를 받지 마세요.",
    "W-GATE-Q": "GATE-Q-01 전이라 이 견적은 ‘시험 견적’으로 승인돼요. 고객 문서에 시험 표시가 붙고 실거래 결제에 쓸 수 없어요.",
    "W-GATE-CAP": "수용량 정책이 아직 확정되지 않았어요(GATE-CAP-01).",
    "W-OVER-CAPACITY": "동시 진행 상한을 넘겨 수락해요.",
    "W-NO-POLICY": "승인된 상담 정책이 없어요. 근거 없이 작성된 초안이에요.",
}

FIX_LINK = {
    "G-HOLIDAY": "/schedule", "G-NO-CONCEPT": "quote", "G-QUOTE-MISSING": "quote", "G-QUOTE-FIELDS": "quote",
    "G-QUOTE-TOTAL": "quote", "G-PAYMENT-REQUEST": "payment", "G-PAYMENT-UNVERIFIED": "payment",
    "G-DESIGN-MISSING": "making", "G-DESIGN-UNAPPROVED": "making", "G-SPEC-INCOMPLETE": "making",
    "G-PACKET-MISSING": "making", "G-PACKET-STALE": "making", "G-PACKET-HASH": "making", "G-SPEC-DIFF": "making",
    "G-RECEIVE-INFO": "making", "G-QC-INCOMPLETE": "qc", "G-QC-FAIL": "qc", "G-BALANCE": "payment",
    "G-SHIP-INFO": "qc", "G-NO-CONSENT": "intake", "G-ISSUE-OPEN": "history", "G-TEST-QUOTE": "quote",
    "G-L3-JUDGMENT": "cs", "G-CONTRACT-REQUIRED": "quote#contract", "G-CONTRACT-FORM": "/settings/google",
    "G-QUOTE-EXPIRED": "quote", "G-LOSS-CONFIRM": "quote#loss-review", "G-REFUNDED": "payment", "G-QC-PHOTO": "qc", "G-BALANCE-BEFORE-FACTORY": "payment", "G-CONTRACT-STATE": "quote#contract",
    "G-CONTRACT-REVIEW": "quote#contract", "G-CONTRACT-DELIVERY": "quote#contract", "G-GATE-LIVE": "/settings/gates", "G-CAPACITY": "intake#cap-exc",
}


@dataclass(frozen=True)
class Unmet:
    code: str
    message: str
    fix: str | None = None


def unmet(code: str, **kw) -> Unmet:
    return Unmet(code, MESSAGES[code].format(**kw), FIX_LINK.get(code))


class TransitionBlocked(Exception):
    def __init__(self, unmet_list: list[Unmet]):
        self.unmet = unmet_list
        super().__init__("; ".join(u.message for u in unmet_list))


class Forbidden(Exception):
    pass
