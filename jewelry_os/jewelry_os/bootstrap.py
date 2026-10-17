"""컨텍스트 조립·첫 실행 설정(S-00a)."""
from __future__ import annotations

from .adapters import ai as ai_ad
from .adapters import google as g
from .context import Ctx
from .core import logging_setup, security
from .core.config import Config, load_config
from .core.db import Database
from .services import capacity, settings


def build_ctx(cfg: Config | None = None, *, forms=None, files=None, analyzer=None) -> Ctx:
    cfg = cfg or load_config()
    for k in ("ANTHROPIC_API_KEY", "JOS_GOOGLE_CLIENT_SECRET", "JOS_GOOGLE_REFRESH_TOKEN", "JOS_BACKUP_KEY",
              "JOS_DATA_KEY", "JOS_SECRET"):
        logging_setup.register_secret(cfg.secret(k))
    db = Database(cfg.db_path)
    ctx = Ctx(cfg=cfg, db=db)
    ctx.forms, ctx.files, ctx.analyzer = forms, files, analyzer
    configure_adapters(ctx, keep_injected=True)
    return ctx


def configure_adapters(ctx: Ctx, keep_injected=False) -> None:
    """설정값으로 어댑터 선택(BD-005). 시험에서 주입한 어댑터는 유지."""
    mode = settings.get(ctx, "google_mode")
    if not (keep_injected and ctx.forms is not None):
        if mode == "live":
            oauth = g.GoogleOAuth(ctx.cfg)
            ctx.forms, ctx.files = g.GoogleForms(oauth), g.GoogleDrive(oauth)
            ctx.extra["oauth"] = oauth
        else:
            ctx.forms = ctx.extra.setdefault("mock_forms", g.MockForms())
            ctx.files = ctx.extra.setdefault("mock_drive", g.MockDrive())
    if not (keep_injected and ctx.analyzer is not None):
        key = ctx.cfg.secret("ANTHROPIC_API_KEY")
        if settings.get(ctx, "ai_provider") == "claude" and key:
            try:
                ctx.analyzer = ai_ad.ClaudeAI(key, settings.get(ctx, "ai_model"))
            except ai_ad.AnalyzerError:
                ctx.analyzer = ai_ad.MockAI()
        else:
            ctx.analyzer = ai_ad.MockAI()


def needs_setup(ctx: Ctx) -> bool:
    return ctx.db.scalar("SELECT COUNT(*) FROM users") == 0


def run_setup(ctx: Ctx, maker: dict, operator: dict, policy: dict | None = None) -> None:
    """첫 실행: 계정 2개 + 운영 기본값 + 휴무일 기본 목록 + 자리표시 정책 초안(TBD-002)."""
    if not needs_setup(ctx):
        raise PermissionError("이미 설정이 끝났어요")
    if maker["username"] == operator["username"]:
        raise security.AuthError("두 계정의 아이디가 같아요.")
    for p in (maker, operator):
        if len(p.get("password") or "") < 10:
            raise security.AuthError("비밀번호는 10자 이상이어야 해요.")
    security.create_user(ctx.db, maker["username"], maker["display_name"], "maker", maker["password"])
    security.create_user(ctx.db, operator["username"], operator["display_name"], "operator", operator["password"])
    policy = policy or {}
    for k in ("capacity_limit", "payment_policy", "lock_minutes", "quiet_start", "quiet_end", "google_mode"):
        settings.put(ctx, k, policy.get(k, settings.DEFAULTS[k]), None)
    if policy.get("load_holidays", True):
        capacity.load_default_holidays(ctx)
    for kind, title, body in (
        ("terms", "수정·환불 정책(초안)", "[TBD-002] 사업 책임자가 확정할 수정·환불 정책 문구 자리입니다."),
        ("cs", "상담 정책(초안)", "[TBD-002] 고객 상담 응대 원칙 자리입니다. 확약 표현 금지, 대외 단계명만 사용."),
        ("consent", "개인정보 수집·이용 동의(초안)", "[TBD-002] 개인정보 수집·이용 동의 문구 자리입니다."),
    ):
        settings.create_policy(ctx, kind, title, body, None)
    configure_adapters(ctx)
