-- Jewelry OS R1 schema — DD-001 (FROZEN). 시각은 UTC ISO-8601 TEXT, 금액은 INTEGER(원).

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('maker','operator')),
  password_hash TEXT NOT NULL,
  prefs_json TEXT NOT NULL DEFAULT '{}',
  failed_count INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  created_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  csrf TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  locked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_by INTEGER,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY,
  display_name TEXT,
  contact_kind TEXT,
  contact_enc TEXT,
  consent_version TEXT,
  merged_into INTEGER REFERENCES customers(id),
  deleted_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intake_responses (
  id INTEGER PRIMARY KEY,
  form_id TEXT NOT NULL,
  response_id TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  submitted_at TEXT,
  last_submitted_at TEXT,
  raw_digest TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  consent_version TEXT,
  is_test INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE (form_id, response_id)
);

CREATE TABLE IF NOT EXISTS intake_revisions (
  id INTEGER PRIMARY KEY,
  intake_id INTEGER NOT NULL REFERENCES intake_responses(id),
  revision INTEGER NOT NULL,
  last_submitted_at TEXT,
  raw_digest TEXT NOT NULL,
  answers_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (intake_id, revision)
);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY,
  order_no TEXT NOT NULL UNIQUE,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  intake_id INTEGER UNIQUE REFERENCES intake_responses(id),
  lifecycle_state TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('form','manual')),
  source_channel TEXT,
  product_type TEXT,
  summary TEXT,
  is_test INTEGER NOT NULL DEFAULT 0,
  input_revision INTEGER NOT NULL DEFAULT 1,
  accepted_at TEXT,
  promised_date TEXT,
  internal_eta TEXT,
  decline_category TEXT,
  payment_policy TEXT NOT NULL DEFAULT 'THIRDS',
  version INTEGER NOT NULL DEFAULT 1,
  state_entered_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT
);

CREATE TABLE IF NOT EXISTS transition_ctx (token TEXT PRIMARY KEY);

CREATE TRIGGER IF NOT EXISTS orders_state_guard
BEFORE UPDATE OF lifecycle_state ON orders
WHEN NEW.lifecycle_state IS NOT OLD.lifecycle_state AND (SELECT COUNT(*) FROM transition_ctx) = 0
BEGIN SELECT RAISE(ABORT, 'lifecycle_state may only change through the transition service'); END;

CREATE TABLE IF NOT EXISTS order_specs (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  revision INTEGER NOT NULL,
  spec_json TEXT NOT NULL,
  change_reason TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (order_id, revision)
);

CREATE TABLE IF NOT EXISTS holds (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  reason TEXT NOT NULL CHECK (reason IN ('NEEDS_INFO','WAITLISTED','HOLD','NO_CONSENT')),
  note TEXT,
  set_by INTEGER,
  set_at TEXT NOT NULL,
  cleared_by INTEGER,
  cleared_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS holds_active ON holds(order_id, reason) WHERE cleared_at IS NULL;

CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  type TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN','RESOLVED')),
  fault TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  note TEXT,
  opened_by INTEGER,
  opened_at TEXT NOT NULL,
  resolved_by INTEGER,
  resolved_at TEXT,
  resolution TEXT
);

CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY,
  order_id INTEGER REFERENCES orders(id),
  kind TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  filename TEXT NOT NULL,
  mime TEXT,
  size INTEGER,
  file_hash TEXT,
  storage_ref TEXT,
  source_file_id TEXT,
  fetch_status TEXT NOT NULL DEFAULT 'OK',
  fetch_error TEXT,
  uploaded_by INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_tasks (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  input_revision INTEGER NOT NULL,
  task_type TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  model_id TEXT,
  status TEXT NOT NULL,
  result_json TEXT,
  error_kind TEXT,
  sent_fields TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (order_id, input_revision, task_type, policy_version)
);

CREATE TABLE IF NOT EXISTS ai_usage (month TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0, est_cost_krw INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS concepts (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  version INTEGER NOT NULL,
  asset_id INTEGER REFERENCES assets(id),
  notes TEXT,
  work_minutes INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (order_id, version)
);

CREATE TABLE IF NOT EXISTS policies (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('cs','terms','consent','price')),
  version INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('DRAFT','APPROVED','RETIRED')),
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  approved_by INTEGER,
  approved_at TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (kind, version)
);

CREATE TABLE IF NOT EXISTS quotes (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  version INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('DRAFT','APPROVED','SUPERSEDED')),
  is_test INTEGER NOT NULL DEFAULT 0,
  spec_json TEXT NOT NULL DEFAULT '{}',
  spec_summary TEXT,
  tax_included INTEGER,
  shipping_included INTEGER,
  total_krw INTEGER NOT NULL DEFAULT 0,
  deposit_krw INTEGER,
  balance_krw INTEGER,
  payment_policy TEXT NOT NULL DEFAULT 'THIRDS',
  promised_date TEXT,
  valid_until TEXT,
  terms_policy_id INTEGER REFERENCES policies(id),
  terms_version INTEGER,
  cost_estimate_krw INTEGER NOT NULL DEFAULT 0,
  variable_cost_krw INTEGER NOT NULL DEFAULT 0,
  design_minutes_est INTEGER NOT NULL DEFAULT 0,
  internal_note TEXT,
  approved_by INTEGER,
  approved_at TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (order_id, version)
);

CREATE TRIGGER IF NOT EXISTS quotes_immutable
BEFORE UPDATE ON quotes
WHEN OLD.status <> 'DRAFT' AND NOT (
  OLD.status = 'APPROVED' AND NEW.status = 'SUPERSEDED'
  AND NEW.id IS OLD.id AND NEW.order_id IS OLD.order_id AND NEW.version IS OLD.version
  AND NEW.is_test IS OLD.is_test AND NEW.spec_json IS OLD.spec_json AND NEW.spec_summary IS OLD.spec_summary
  AND NEW.tax_included IS OLD.tax_included AND NEW.shipping_included IS OLD.shipping_included
  AND NEW.total_krw IS OLD.total_krw AND NEW.deposit_krw IS OLD.deposit_krw AND NEW.balance_krw IS OLD.balance_krw
  AND NEW.payment_policy IS OLD.payment_policy AND NEW.promised_date IS OLD.promised_date
  AND NEW.valid_until IS OLD.valid_until AND NEW.terms_policy_id IS OLD.terms_policy_id
  AND NEW.terms_version IS OLD.terms_version AND NEW.cost_estimate_krw IS OLD.cost_estimate_krw
  AND NEW.variable_cost_krw IS OLD.variable_cost_krw AND NEW.design_minutes_est IS OLD.design_minutes_est
  AND NEW.internal_note IS OLD.internal_note AND NEW.approved_by IS OLD.approved_by
  AND NEW.approved_at IS OLD.approved_at AND NEW.created_by IS OLD.created_by AND NEW.created_at IS OLD.created_at
)
BEGIN SELECT RAISE(ABORT, 'approved quotes are immutable'); END;

CREATE TABLE IF NOT EXISTS quote_items (
  id INTEGER PRIMARY KEY,
  quote_id INTEGER NOT NULL REFERENCES quotes(id),
  seq INTEGER NOT NULL,
  kind TEXT NOT NULL,
  label_public TEXT NOT NULL,
  amount_krw INTEGER NOT NULL CHECK (amount_krw >= 0),
  internal_cost_krw INTEGER NOT NULL DEFAULT 0,
  note_internal TEXT
);

CREATE TRIGGER IF NOT EXISTS quote_items_immutable_upd
BEFORE UPDATE ON quote_items
WHEN (SELECT status FROM quotes WHERE id = OLD.quote_id) <> 'DRAFT'
BEGIN SELECT RAISE(ABORT, 'approved quote items are immutable'); END;
CREATE TRIGGER IF NOT EXISTS quote_items_immutable_del
BEFORE DELETE ON quote_items
WHEN (SELECT status FROM quotes WHERE id = OLD.quote_id) <> 'DRAFT'
BEGIN SELECT RAISE(ABORT, 'approved quote items are immutable'); END;
CREATE TRIGGER IF NOT EXISTS quote_items_immutable_ins
BEFORE INSERT ON quote_items
WHEN (SELECT status FROM quotes WHERE id = NEW.quote_id) <> 'DRAFT'
BEGIN SELECT RAISE(ABORT, 'approved quote items are immutable'); END;

CREATE TABLE IF NOT EXISTS customer_acceptances (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  kind TEXT NOT NULL CHECK (kind IN ('quote','design','consent')),
  quote_id INTEGER REFERENCES quotes(id),
  quote_version INTEGER,
  design_version_id INTEGER,
  terms_version INTEGER,
  channel TEXT NOT NULL,
  evidence_text TEXT NOT NULL,
  evidence_asset_id INTEGER REFERENCES assets(id),
  accepted_at TEXT NOT NULL,
  accepted_at_src TEXT,
  recorded_by INTEGER NOT NULL,
  recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payment_requests (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  quote_id INTEGER NOT NULL REFERENCES quotes(id),
  quote_version INTEGER NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN ('DEPOSIT','BALANCE','FULL','CHANGE')),
  expected_krw INTEGER NOT NULL,
  seller_code TEXT NOT NULL UNIQUE,
  link_url TEXT,
  link_status TEXT NOT NULL DEFAULT 'DRAFT',
  status TEXT NOT NULL DEFAULT 'DRAFT',
  is_test INTEGER NOT NULL DEFAULT 0,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  change_order_id INTEGER NOT NULL DEFAULT 0,   -- CHANGE(발주 후 변경 추가 결제)일 때 change_orders.id
  UNIQUE (order_id, phase, quote_version, change_order_id)
);

CREATE TABLE IF NOT EXISTS payment_events (
  id INTEGER PRIMARY KEY,
  request_id INTEGER NOT NULL REFERENCES payment_requests(id),
  naver_order_id TEXT NOT NULL,
  naver_product_order_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  amount_krw INTEGER NOT NULL,
  naver_status TEXT NOT NULL,
  looked_up_at TEXT NOT NULL,
  looked_up_at_src TEXT,
  note TEXT,
  is_test INTEGER NOT NULL DEFAULT 0,
  recorded_by INTEGER NOT NULL,
  recorded_at TEXT NOT NULL,
  UNIQUE (naver_product_order_id, event_type)
);

CREATE TABLE IF NOT EXISTS design_versions (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  version INTEGER NOT NULL,
  cad_asset_id INTEGER REFERENCES assets(id),
  preview_asset_id INTEGER REFERENCES assets(id),
  stl_asset_id INTEGER REFERENCES assets(id),
  note TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (order_id, version)
);

CREATE TABLE IF NOT EXISTS factory_specs (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  revision INTEGER NOT NULL,
  spec_json TEXT NOT NULL,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (order_id, revision)
);

CREATE TABLE IF NOT EXISTS factory_packets (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  design_version_id INTEGER NOT NULL REFERENCES design_versions(id),
  spec_id INTEGER NOT NULL REFERENCES factory_specs(id),
  stl_hash TEXT NOT NULL,
  packet_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'VALID' CHECK (status IN ('VALID','VOID','SENT')),
  void_reason TEXT,
  zip_ref TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  sent_at TEXT
);

CREATE TABLE IF NOT EXISTS factory_jobs (
  id INTEGER PRIMARY KEY,
  packet_id INTEGER NOT NULL REFERENCES factory_packets(id),
  order_id INTEGER NOT NULL REFERENCES orders(id),
  vendor_ref TEXT,
  expected_at TEXT,
  received_at TEXT,
  actual_cost_krw INTEGER,
  defect_cost_krw INTEGER,
  logistics_cost_krw INTEGER,
  status TEXT NOT NULL DEFAULT 'SENT'
);

CREATE TABLE IF NOT EXISTS quality_checks (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  checklist_json TEXT NOT NULL,
  result TEXT NOT NULL CHECK (result IN ('PASS','FAIL','INCOMPLETE')),
  inspected_by INTEGER,
  inspected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shipments (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  recipient_enc TEXT,
  method TEXT,
  carrier TEXT,
  tracking_no TEXT,
  platform_status TEXT,
  shipped_at TEXT,
  delivered_confirmed_at TEXT,
  delivery_evidence TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cs_threads (
  id INTEGER PRIMARY KEY,
  order_id INTEGER REFERENCES orders(id),
  provider TEXT NOT NULL DEFAULT 'kakao_manual',
  provider_user_key TEXT,
  session_status TEXT NOT NULL DEFAULT 'MANUAL',
  title TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cs_messages (
  id INTEGER PRIMARY KEY,
  thread_id INTEGER NOT NULL REFERENCES cs_threads(id),
  provider TEXT NOT NULL,
  sender_key TEXT NOT NULL,
  message_key TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  summary TEXT NOT NULL,
  content_redacted TEXT,
  link TEXT,
  event_at TEXT NOT NULL,
  event_at_src TEXT,
  recorded_by INTEGER,
  recorded_at TEXT NOT NULL,
  UNIQUE (provider, sender_key, message_key)
);

CREATE TABLE IF NOT EXISTS cs_drafts (
  id INTEGER PRIMARY KEY,
  thread_id INTEGER NOT NULL REFERENCES cs_threads(id),
  version INTEGER NOT NULL,
  level TEXT NOT NULL CHECK (level IN ('L0','L1','L2','L3')),
  policy_id INTEGER REFERENCES policies(id),
  policy_version INTEGER,
  body TEXT NOT NULL,
  unclear_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL CHECK (status IN ('DRAFT','APPROVED','SENT','SUPERSEDED')),
  source TEXT NOT NULL DEFAULT 'ai',
  operator_judgment TEXT,
  judgment_by INTEGER,
  judgment_at TEXT,
  approved_by INTEGER,
  approved_at TEXT,
  copied_at TEXT,
  sent_at TEXT,
  sent_evidence TEXT,
  superseded_by INTEGER,
  created_by INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_cursors (
  provider TEXT NOT NULL,
  scope TEXT NOT NULL,
  last_success_at TEXT,
  last_count INTEGER,
  last_page_token TEXT,
  status TEXT NOT NULL DEFAULT 'IDLE',
  last_error_kind TEXT,
  last_error_at TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  PRIMARY KEY (provider, scope)
);

CREATE TABLE IF NOT EXISTS outbox_events (
  event_key TEXT PRIMARY KEY,
  order_id INTEGER REFERENCES orders(id),
  message_type TEXT NOT NULL,
  status TEXT NOT NULL,
  last_attempt_at TEXT,
  provider_ref TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  order_id INTEGER,
  actor_id INTEGER,
  actor_role TEXT NOT NULL,
  action TEXT NOT NULL,
  prev TEXT,
  next TEXT,
  reason TEXT,
  ref_version TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS payev_no_update BEFORE UPDATE ON payment_events BEGIN SELECT RAISE(ABORT, 'payment events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS payev_no_delete BEFORE DELETE ON payment_events BEGIN SELECT RAISE(ABORT, 'payment events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS accept_no_update BEFORE UPDATE ON customer_acceptances BEGIN SELECT RAISE(ABORT, 'acceptances are append-only'); END;
CREATE TRIGGER IF NOT EXISTS accept_no_delete BEFORE DELETE ON customer_acceptances BEGIN SELECT RAISE(ABORT, 'acceptances are append-only'); END;

CREATE TABLE IF NOT EXISTS funnel_events (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  kind TEXT NOT NULL,
  amount_krw INTEGER,
  is_test INTEGER NOT NULL DEFAULT 0,
  at TEXT NOT NULL,
  UNIQUE (order_id, kind)
);

CREATE TABLE IF NOT EXISTS ad_spend (
  month TEXT NOT NULL,
  channel TEXT NOT NULL,
  amount_krw INTEGER NOT NULL CHECK (amount_krw >= 0),
  updated_by INTEGER,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (month, channel)
);

CREATE TABLE IF NOT EXISTS holidays (date TEXT PRIMARY KEY, name TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS cad_slots (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  set_by INTEGER,
  set_at TEXT NOT NULL,
  UNIQUE (date, order_id)
);

CREATE TABLE IF NOT EXISTS work_logs (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  kind TEXT NOT NULL CHECK (kind IN ('concept','cad','factory','qc','cs')),
  minutes INTEGER NOT NULL CHECK (minutes > 0),
  work_date TEXT NOT NULL,
  note TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_records (
  id INTEGER PRIMARY KEY,
  gate_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PASSED','NOT_PASSED')),
  evidence TEXT NOT NULL,
  recorded_by INTEGER NOT NULL,
  recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS heartbeats (id INTEGER PRIMARY KEY CHECK (id = 1), at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS startup_events (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  est_down_from TEXT,
  backfilled INTEGER,
  acknowledged_by INTEGER,
  acknowledged_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  severity TEXT NOT NULL,
  order_id INTEGER REFERENCES orders(id),
  dedupe_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  release_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS notification_reads (
  notification_id INTEGER NOT NULL REFERENCES notifications(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  PRIMARY KEY (notification_id, user_id)
);

CREATE TABLE IF NOT EXISTS security_checklist (
  item TEXT PRIMARY KEY,
  checked INTEGER NOT NULL,
  checked_by INTEGER,
  checked_at TEXT
);

CREATE TABLE IF NOT EXISTS backups (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL,
  size INTEGER,
  status TEXT NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- H1 r1 Q2: 동시 진행 상한 초과 수락 = 운영자 예외 승인(사유·예상 납기) + 아버님 제작 가능 확인
CREATE TABLE IF NOT EXISTS capacity_exceptions (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  approved_by INTEGER NOT NULL,
  reason TEXT NOT NULL,
  expected_eta TEXT NOT NULL,
  active_count INTEGER NOT NULL,
  limit_count INTEGER NOT NULL,
  approved_at TEXT NOT NULL,
  maker_confirmed_by INTEGER,
  maker_confirmed_at TEXT
);

-- H1 r1 Q10: 발주 후 고객 변경(재작업·고객 변심) = 변경 가능 판정 → 추가 견적 → 고객 동의 → 필요 결제 확인
CREATE TABLE IF NOT EXISTS change_orders (
  id INTEGER PRIMARY KEY,
  issue_id INTEGER NOT NULL UNIQUE REFERENCES issues(id),
  order_id INTEGER NOT NULL REFERENCES orders(id),
  feasible INTEGER,
  feasibility_note TEXT,
  decided_by INTEGER,
  decided_at TEXT,
  amount_krw INTEGER,
  description TEXT,
  quoted_by INTEGER,
  quoted_at TEXT,
  accept_channel TEXT,
  accept_evidence TEXT,
  accepted_at TEXT,
  accept_recorded_by INTEGER,
  created_at TEXT NOT NULL
);

-- D-24 Form B 계약 동의·증거(schema v3, 추가만 — 기존 표는 바꾸지 않는다)
-- 고객이 본 Form B 고지 문구 원본(forms.get 응답 전체). 내용이 같으면 한 행만.
CREATE TABLE IF NOT EXISTS contract_notices (
  id INTEGER PRIMARY KEY,
  form_id TEXT NOT NULL,
  revision_id TEXT,
  body_json TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  UNIQUE (form_id, sha256)
);
CREATE TRIGGER IF NOT EXISTS cnotice_no_update BEFORE UPDATE ON contract_notices BEGIN SELECT RAISE(ABORT, 'contract notices are immutable'); END;
CREATE TRIGGER IF NOT EXISTS cnotice_no_delete BEFORE DELETE ON contract_notices BEGIN SELECT RAISE(ABORT, 'contract notices are immutable'); END;

-- 계약 요청 = 주문 1건 × 확정 견적 버전 1개. 확인코드는 해시(대조용)와 암호문(링크 재생성용)만 저장.
CREATE TABLE IF NOT EXISTS contract_requests (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  quote_id INTEGER NOT NULL REFERENCES quotes(id),
  quote_version INTEGER NOT NULL,
  quote_ref TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ISSUED','RESPONSE_RECEIVED','REVIEW_REQUIRED','ACCEPTED','REJECTED','SUPERSEDED')),
  code_hash TEXT NOT NULL UNIQUE,
  code_enc TEXT NOT NULL,
  form_id TEXT NOT NULL,
  contract_form_schema_version TEXT NOT NULL,
  notice_id INTEGER NOT NULL REFERENCES contract_notices(id),
  refund_policy_version TEXT,
  refund_policy_text_snapshot TEXT,
  refund_policy_sha256 TEXT,
  refund_policy_acknowledged INTEGER NOT NULL DEFAULT 0,
  refund_policy_acknowledged_at TEXT,
  refund_policy_review_required INTEGER NOT NULL DEFAULT 0,
  terms_policy_id INTEGER REFERENCES policies(id),
  terms_version INTEGER,
  terms_sha256 TEXT,
  quote_snapshot_json TEXT NOT NULL,
  quote_sha256 TEXT NOT NULL,
  quote_html_asset_id INTEGER REFERENCES assets(id),
  quote_pdf_asset_id INTEGER REFERENCES assets(id),
  is_test INTEGER NOT NULL DEFAULT 0,
  issued_by INTEGER NOT NULL,
  issued_at TEXT NOT NULL,
  delivered_at TEXT,
  delivered_channel TEXT,
  delivered_note TEXT,
  delivered_by INTEGER,
  delivered_asset_id INTEGER REFERENCES assets(id),
  response_id INTEGER,
  problems_json TEXT NOT NULL DEFAULT '[]',
  identity_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (identity_status IN ('PENDING','VERIFIED')),
  identity_method TEXT,
  identity_note TEXT,
  identity_by INTEGER,
  identity_at TEXT,
  decided_at TEXT,
  decided_by INTEGER,
  superseded_at TEXT,
  superseded_reason TEXT,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS contract_open_per_quote ON contract_requests(order_id, quote_id)
  WHERE status IN ('ISSUED','RESPONSE_RECEIVED','REVIEW_REQUIRED','ACCEPTED');
CREATE INDEX IF NOT EXISTS ix_contract_order ON contract_requests(order_id, id);
-- 발행 당시의 견적·고지·정책 근거는 바꿀 수 없다(상태·전달·확인 기록만 갱신)
CREATE TRIGGER IF NOT EXISTS creq_evidence_immutable BEFORE UPDATE ON contract_requests
WHEN NEW.order_id IS NOT OLD.order_id OR NEW.customer_id IS NOT OLD.customer_id OR NEW.quote_id IS NOT OLD.quote_id
  OR NEW.quote_version IS NOT OLD.quote_version OR NEW.quote_ref IS NOT OLD.quote_ref OR NEW.code_hash IS NOT OLD.code_hash
  OR NEW.code_enc IS NOT OLD.code_enc OR NEW.form_id IS NOT OLD.form_id OR NEW.notice_id IS NOT OLD.notice_id
  OR NEW.contract_form_schema_version IS NOT OLD.contract_form_schema_version
  OR NEW.refund_policy_version IS NOT OLD.refund_policy_version
  OR NEW.refund_policy_text_snapshot IS NOT OLD.refund_policy_text_snapshot
  OR NEW.refund_policy_sha256 IS NOT OLD.refund_policy_sha256
  OR NEW.terms_policy_id IS NOT OLD.terms_policy_id OR NEW.terms_sha256 IS NOT OLD.terms_sha256
  OR NEW.quote_snapshot_json IS NOT OLD.quote_snapshot_json OR NEW.quote_sha256 IS NOT OLD.quote_sha256
  OR NEW.issued_at IS NOT OLD.issued_at OR NEW.issued_by IS NOT OLD.issued_by
  OR (OLD.quote_html_asset_id IS NOT NULL AND NEW.quote_html_asset_id IS NOT OLD.quote_html_asset_id)
  OR (OLD.quote_pdf_asset_id IS NOT NULL AND NEW.quote_pdf_asset_id IS NOT OLD.quote_pdf_asset_id)
BEGIN SELECT RAISE(ABORT, 'contract evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS creq_no_delete BEFORE DELETE ON contract_requests BEGIN SELECT RAISE(ABORT, 'contract requests are kept'); END;

-- Form B 응답 원본. 중복 기준 (form_id, response_id). 확인코드가 맞지 않는 응답도 지우지 않고 남긴다.
CREATE TABLE IF NOT EXISTS contract_responses (
  id INTEGER PRIMARY KEY,
  form_id TEXT NOT NULL,
  response_id TEXT NOT NULL,
  request_id INTEGER REFERENCES contract_requests(id),
  candidate_order_id INTEGER REFERENCES orders(id),
  match_status TEXT NOT NULL CHECK (match_status IN ('MATCHED','CODE_UNKNOWN')),
  create_time TEXT,
  last_submitted_time TEXT,
  respondent_email_enc TEXT,
  respondent_email_hash TEXT,
  answers_json TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  first_fetched_at TEXT NOT NULL,
  last_fetched_at TEXT NOT NULL,
  UNIQUE (form_id, response_id)
);
-- 응답 원문(JSON, 암호화)과 해시 — 고객이 응답을 고치면 revision이 늘고 이전 원문도 남는다
CREATE TABLE IF NOT EXISTS contract_response_revisions (
  id INTEGER PRIMARY KEY,
  response_row_id INTEGER NOT NULL REFERENCES contract_responses(id),
  revision INTEGER NOT NULL,
  last_submitted_time TEXT,
  raw_enc TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  UNIQUE (response_row_id, revision)
);
CREATE TRIGGER IF NOT EXISTS crrev_no_update BEFORE UPDATE ON contract_response_revisions BEGIN SELECT RAISE(ABORT, 'response revisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS crrev_no_delete BEFORE DELETE ON contract_response_revisions BEGIN SELECT RAISE(ABORT, 'response revisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS cresp_no_delete BEFORE DELETE ON contract_responses BEGIN SELECT RAISE(ABORT, 'contract responses are kept'); END;

-- 계약 이력(불변): 발행·전달·응답 수집·검증 결과·본인 확인·확정·대체
CREATE TABLE IF NOT EXISTS contract_events (
  id INTEGER PRIMARY KEY,
  request_id INTEGER NOT NULL REFERENCES contract_requests(id),
  order_id INTEGER NOT NULL REFERENCES orders(id),
  event TEXT NOT NULL,
  prev_status TEXT,
  next_status TEXT,
  actor_id INTEGER,
  actor_role TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS cevent_no_update BEFORE UPDATE ON contract_events BEGIN SELECT RAISE(ABORT, 'contract events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS cevent_no_delete BEFORE DELETE ON contract_events BEGIN SELECT RAISE(ABORT, 'contract events are append-only'); END;

-- 동의 문항별 독립 전자 동의 기록(Q4·Q5·Q6 청약철회 별도 동의·Q6-1 환불 정책 확인·Q7·Q8). 응답 수정마다 새 행.
CREATE TABLE IF NOT EXISTS contract_consents (
  id INTEGER PRIMARY KEY,
  request_id INTEGER NOT NULL REFERENCES contract_requests(id),
  response_row_id INTEGER NOT NULL REFERENCES contract_responses(id),
  response_revision INTEGER NOT NULL,
  consent_key TEXT NOT NULL,
  question_id TEXT NOT NULL,
  question_title TEXT NOT NULL,
  answer TEXT,
  result TEXT NOT NULL CHECK (result IN ('AGREED','REFUSED','NEEDS_EXPLANATION','MISSING','UNKNOWN')),
  notice_section_sha256 TEXT,
  submitted_at TEXT,
  recorded_at TEXT NOT NULL,
  UNIQUE (response_row_id, response_revision, consent_key)
);
CREATE TRIGGER IF NOT EXISTS cconsent_no_update BEFORE UPDATE ON contract_consents BEGIN SELECT RAISE(ABORT, 'consent records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS cconsent_no_delete BEFORE DELETE ON contract_consents BEGIN SELECT RAISE(ABORT, 'consent records are append-only'); END;

-- 취소·환불 사건(Form B v1.1 단계별 정책 R1~R7). 사실(결제·CAD 착수·발주·배송 시각)은 시스템 기록에서 가져오고,
-- 법적 검토·환불액 결정은 사람이 한다. AI·자동 규칙은 환불 불가나 공제액을 정하지 않는다.
CREATE TABLE IF NOT EXISTS refund_requests (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  contract_request_id INTEGER REFERENCES contract_requests(id),
  reason_kind TEXT NOT NULL CHECK (reason_kind IN ('CHANGE_OF_MIND','DEFECT','MISMATCH','OTHER')),
  refund_reason TEXT NOT NULL,
  requested_via TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  requested_at_src TEXT,
  lifecycle_state_at_request TEXT NOT NULL,
  production_stage_at_request TEXT NOT NULL CHECK (production_stage_at_request IN ('R1','R2','R3','R4','R5','R6')),
  paid_amount INTEGER NOT NULL DEFAULT 0,
  deposit_paid_at TEXT,
  cad_started_at TEXT,
  factory_ordered_at TEXT,
  delivered_at TEXT,
  legal_review_status TEXT NOT NULL CHECK (legal_review_status IN ('NOT_REQUIRED','REQUIRED','DONE')),
  legal_review_note TEXT,
  legal_review_by INTEGER,
  legal_review_at TEXT,
  legal_withdrawal INTEGER,
  refund_eligible_amount INTEGER,
  refund_approved_amount INTEGER,
  shipping_fee_bearer TEXT CHECK (shipping_fee_bearer IN ('CUSTOMER','SELLER','NONE')),
  shipping_fee_krw INTEGER,
  refund_basis_date TEXT,
  refund_due_at TEXT,
  approved_by INTEGER,
  approved_at TEXT,
  decision_reason TEXT,
  refund_completed_at TEXT,
  refund_completed_amount INTEGER,
  refund_method TEXT,
  refund_evidence TEXT,
  evidence_refs TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'RECEIVED' CHECK (status IN ('RECEIVED','APPROVED','COMPLETED','WITHDRAWN')),
  issue_id INTEGER REFERENCES issues(id),
  recorded_by INTEGER NOT NULL,
  recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_refund_order ON refund_requests(order_id, id);
-- 접수 당시 사실(단계·시각·금액)은 고칠 수 없다
CREATE TRIGGER IF NOT EXISTS refund_facts_immutable BEFORE UPDATE ON refund_requests
WHEN NEW.order_id IS NOT OLD.order_id OR NEW.customer_id IS NOT OLD.customer_id OR NEW.requested_at IS NOT OLD.requested_at
  OR NEW.reason_kind IS NOT OLD.reason_kind OR NEW.production_stage_at_request IS NOT OLD.production_stage_at_request
  OR NEW.paid_amount IS NOT OLD.paid_amount OR NEW.deposit_paid_at IS NOT OLD.deposit_paid_at
  OR NEW.cad_started_at IS NOT OLD.cad_started_at OR NEW.factory_ordered_at IS NOT OLD.factory_ordered_at
  OR NEW.delivered_at IS NOT OLD.delivered_at OR NEW.lifecycle_state_at_request IS NOT OLD.lifecycle_state_at_request
  OR NEW.refund_eligible_amount IS NOT OLD.refund_eligible_amount OR NEW.recorded_at IS NOT OLD.recorded_at
BEGIN SELECT RAISE(ABORT, 'refund request facts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS refund_no_delete BEFORE DELETE ON refund_requests BEGIN SELECT RAISE(ABORT, 'refund requests are kept'); END;
CREATE TABLE IF NOT EXISTS refund_events (
  id INTEGER PRIMARY KEY,
  refund_id INTEGER NOT NULL REFERENCES refund_requests(id),
  order_id INTEGER NOT NULL REFERENCES orders(id),
  event TEXT NOT NULL,
  actor_id INTEGER,
  actor_role TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS revent_no_update BEFORE UPDATE ON refund_events BEGIN SELECT RAISE(ABORT, 'refund events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS revent_no_delete BEFORE DELETE ON refund_events BEGIN SELECT RAISE(ABORT, 'refund events are append-only'); END;

-- D-31 카카오톡 대화 AI 정리(정해진 JSON). 대화가 늘면 새로 정리하고 이전 결과도 남긴다.
CREATE TABLE IF NOT EXISTS cs_digests (
  id INTEGER PRIMARY KEY,
  thread_id INTEGER NOT NULL REFERENCES cs_threads(id),
  order_id INTEGER REFERENCES orders(id),
  upto_message_id INTEGER NOT NULL,
  message_count INTEGER NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT,
  error_kind TEXT,
  model_id TEXT,
  prompt_version TEXT NOT NULL,
  created_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cs_digest_thread ON cs_digests(thread_id, id);

-- D-36 견적서 청약철회 예외 고지(개별 제작 특성·손해 사유) AI 검토와 사람의 최종 판단
CREATE TABLE IF NOT EXISTS quote_loss_reviews (
  id INTEGER PRIMARY KEY,
  quote_id INTEGER NOT NULL REFERENCES quotes(id),
  text_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT,
  model_id TEXT,
  prompt_version TEXT NOT NULL,
  decision TEXT CHECK (decision IN ('ACCEPTED','OVERRIDDEN')),
  decision_note TEXT,
  decided_by INTEGER,
  decided_at TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_qlr_quote ON quote_loss_reviews(quote_id, id);

-- 관리자가 본인임을 확인한 계약 이메일(Google 인증 이메일). 다음 계약부터 같은 고객 확인에 쓴다.
CREATE TABLE IF NOT EXISTS customer_identities (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  kind TEXT NOT NULL CHECK (kind IN ('email')),
  value_hash TEXT NOT NULL,
  value_enc TEXT NOT NULL,
  method TEXT NOT NULL,
  note TEXT,
  verified_by INTEGER NOT NULL,
  verified_at TEXT NOT NULL,
  UNIQUE (customer_id, kind, value_hash)
);

-- D-40 계약 안 된 문의 분석 기록(개인정보 없음). 보관기간 만료로 개인정보를 파기하기 전에 남긴다.
CREATE TABLE IF NOT EXISTS lost_inquiries (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
  order_no TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_activity_at TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  final_state TEXT NOT NULL,
  phase_reached TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  reason_detail_json TEXT NOT NULL DEFAULT '{}',
  product_type TEXT,
  material TEXT,
  budget_range TEXT,
  source_channel TEXT,
  desired_lead_days INTEGER,
  concept_count INTEGER NOT NULL DEFAULT 0,
  quote_versions INTEGER NOT NULL DEFAULT 0,
  last_quote_total INTEGER,
  is_test INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS lost_no_delete BEFORE DELETE ON lost_inquiries BEGIN SELECT RAISE(ABORT, 'lost inquiry records are kept'); END;

CREATE INDEX IF NOT EXISTS ix_orders_state ON orders(lifecycle_state);
CREATE INDEX IF NOT EXISTS ix_issues_order ON issues(order_id, state);
CREATE INDEX IF NOT EXISTS ix_audit_order ON audit_events(order_id, id);
CREATE INDEX IF NOT EXISTS ix_assets_order ON assets(order_id, kind);
