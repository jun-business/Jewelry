"""실제 Google 계정이 필요한 Form B 시험(기본은 건너뜀). 실행: JOS_LIVE_GOOGLE=1 + Google 연결된 데이터 폴더(JOS_LIVE_DATA_DIR).

읽기만 한다 — 폼 응답을 제출하거나 폼을 고치지 않는다."""
import os

import pytest

from jewelry_os.adapters import google as g
from jewelry_os.core.config import load_config
from jewelry_os.services import contracts

pytestmark = pytest.mark.skipif(os.environ.get("JOS_LIVE_GOOGLE") != "1", reason="실제 Google 연결 시험(JOS_LIVE_GOOGLE=1)")
FORM_B = "17H2mNNKU0s2RayLkau2Px-IP2TOHsviHRWbp1UXloZ8"


@pytest.fixture
def forms():
    cfg = load_config("dev", os.environ.get("JOS_LIVE_DATA_DIR") or os.path.join(os.environ["TEMP"], "jos-dev"))
    return g.GoogleForms(g.GoogleOAuth(cfg))


def test_live_form_b_structure_matches_mapping(forms):
    body = forms.get_form(FORM_B)
    assert body["info"]["documentTitle"] == "JOS_B2C_Contract_v1"
    assert contracts.verify_mapping(body) == []
    assert body["settings"].get("emailCollectionType") == "VERIFIED"  # 응답자 Google 이메일 자동 수집


def test_live_form_b_published(forms):
    """고객이 링크를 열려면 게시돼 있어야 한다."""
    assert contracts.is_published(forms.get_form(FORM_B)), "Google Forms 편집 화면에서 ‘게시’를 눌러야 해요"


def test_live_form_b_responses_readable(forms):
    page = forms.list_responses(FORM_B)
    assert isinstance(page.responses, list)
