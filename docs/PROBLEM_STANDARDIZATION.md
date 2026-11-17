# Chuẩn hóa ngân hàng bài (issue #124)

Tài liệu vận hành cho luồng chuẩn hóa bài theo đặc tả v3. Phần kỹ thuật nằm ở
`judge/standardization/` (service) và `judge/models/standardization.py` (dữ liệu).

## Bảy quy ước đã chốt

| Quy ước | Cách hệ thống thực hiện |
|---|---|
| Heading `### Input/Output/Example` (+ `### Scoring` khi có subtask) | `format_v1` (PR sau) |
| Độ khó thang Codeforces ghi vào `Problem.points` | nhóm trường `points`, luôn qua `apply_proposal` + rescore job |
| `partial = True` mọi bài | `standardize_config` |
| Đủ C++17, C++20, Python 3, PyPy 3 | `standardize_config`, resolve theo `Language.key` |
| Bộ nhớ 1024 MB (= 1048576 KB) | `standardize_config`, kể cả các dòng `LanguageLimit` |
| Authors có `MrTee` | `standardize_config`, resolve theo username (`STANDARDIZATION_AUTHOR_USERNAME`) |
| Solutions luôn riêng tư | `standardize_config` và mọi applier nhóm `solution`; khôi phục không bao giờ công khai lại |

## Mô hình dữ liệu

- `StandardizationBatch`: một đợt chạy trên khoảng id, chốt `id_through` lúc bắt đầu, có checkpoint.
- `ProblemSnapshot`: `source_hash` + trạng thái **từng nhóm trường** trước khi sửa.
- `StandardizationProposal`: một đề xuất cho một nhóm trường (`content`, `solution`, `tags`,
  `points`, `config`), `dedupe_key` duy nhất, máy trạng thái trong `enums.PROPOSAL_TRANSITIONS`.
- `StandardizationDecision`: nhật ký chỉ ghi thêm (approve/reject/apply/restore/conflict/blocked),
  kèm before/after.
- `StandardizationRescoreJob`: hàng chờ đồng bộ điểm dẫn xuất, duy nhất theo (bài, trạng thái đích).
- `LLMUsageLedger`, `LLMBatchJob`: chi phí và batch của nhà cung cấp AI (PR sau).

Quản trị: Django admin → mục *Standardization*.

## Lệnh `standardize_config` — năm quy ước cấu hình

Chạy trong container/venv của site, bằng tài khoản đọc được toàn bộ ngân hàng bài.

```shell
# 1. Dry run toàn kho, chỉ ghi báo cáo
python manage.py standardize_config --report /reports/config-dry.jsonl

# 2. Áp dụng theo khoảng id (chốt id trên lúc bắt đầu)
python manage.py standardize_config --apply --report /reports/config-01.jsonl --through-id 5000
python manage.py standardize_config --apply --report /reports/config-02.jsonl --from-id 5000 --through-id 10000

# 3. Chạy tiếp một đợt bị ngắt
python manage.py standardize_config --apply --batch-id 3 --report /reports/config-03-resume.jsonl
```

- Mỗi dòng JSONL: `id, code, public, rules_changed, before, in_live_contest, result[, proposal_id, error]`.
  `result` ∈ `noop | would_apply | applied | already_applied | blocked_contest | stale | error`.
- Lệnh dừng ngay nếu không resolve được `MrTee` (thiếu hoặc trùng) hoặc thiếu runtime; dùng
  `--no-require-judge` khi DB không có judge nào (môi trường test).
- Bài đang trong contest diễn ra (bắt đầu trong vòng `STANDARDIZATION_CONTEST_BLOCK_MAX_DAYS`
  ngày) bị bỏ qua (`blocked_contest`), lần chạy sau sẽ áp dụng đúng proposal cũ. Contest luyện
  tập mở cả năm không tính là đang thi.
- Dry run trên bản sao DB ngày 2026-11-15: 20.638 bài → 20.465 sẽ đổi, 46 đã chuẩn;
  memory 16.939, authors 20.434, partial 11.080, languages 2.529, solution_private 708.
- Tạm dừng/hủy giữa chừng: đổi trạng thái batch trong admin (*Pause*/*Cancel*), lệnh kết thúc
  sau chunk hiện tại.
- `Solution` công khai bị chuyển riêng tư làm giảm điểm đóng góp của tác giả
  (`judge/utils/contribution.py` chỉ tính solution công khai) — chạy
  `manage.py recompute_contributions` sau đợt áp dụng.

## Đồng bộ điểm dẫn xuất (rescore)

Đổi `partial` hoặc `points` làm `Submission.points` cũ lệch với cấu hình mới. Luồng chuẩn hóa
**không** gọi `rescore_problem.delay` trực tiếp; nó tạo `StandardizationRescoreJob` và một task
rút hàng chờ theo nhịp `STANDARDIZATION_RESCORE_PER_MINUTE` (mặc định 20 bài/phút):

```shell
python manage.py standardize_rescore                 # đếm theo trạng thái
python manage.py standardize_rescore --drain         # khởi động chuỗi task (cần celery worker)
python manage.py standardize_rescore --drain --sync --limit 50   # chạy ngay trong tiến trình này
python manage.py standardize_rescore --retry-failed
```

Chuỗi task tự lên lịch lại mỗi 60 s khi còn job; `CELERY_BEAT_SCHEDULE` có mục dự phòng 5 phút
cho môi trường có chạy `celery beat`. Khóa cache đảm bảo hai nguồn không rút cùng lúc. Mỗi job
chạy `rescore_problem` rồi `update_problem_stats`; thất bại được ghi vào `error` và không được
tính là xong.

Chỉ bài có bài nộp mới sinh job. Việc đổi bộ nhớ không cần rescore (chỉ ảnh hưởng lần chấm sau);
đổi test/checker là quy trình rejudge riêng.

## Lớp nhà cung cấp LLM (PR-D)

Các bước AI (viết lại đề, tag/độ khó, lời giải) chưa có; PR này chỉ thêm lớp gọi mô hình và
sổ chi phí để các PR sau dùng chung.

- `llm_service/providers/`: `LLMRequest` → `LLMProvider.complete()` → `LLMResponse`.
  `get_provider()` trả về provider theo `STANDARDIZATION_LLM_PROVIDER`:
  - `anthropic` (`anthropic_api.py`): gọi thẳng Messages API, system prompt được đánh dấu
    cache, JSON schema đưa vào `output_config.format` (structured outputs), độ sâu suy luận qua
    `output_config.effort`. Hỗ trợ Message Batches (`submit_batch` / `poll_batch` /
    `iter_batch_results`, kết quả khớp theo `custom_id` = `dedupe_key`, giá 50 %).
    `stop_reason` `refusal`/`max_tokens` là lỗi (`refusal`/`truncated`), không phải bản nháp.
  - `poe` (`poe.py`): bọc `LLMService` sẵn có; không có số token nên dòng sổ là `unknown`.
- `llm_service/pricing.py`: bảng giá USD/1M token (2026-11) để tính `cost_usd`; mô hình lạ →
  `None` (hiển thị "unknown", không đoán).
- `judge/standardization/llm.py`: `call_json(request, step=...)` gửi, kiểm tra JSON schema
  (`jsonschema`) và **luôn** ghi một dòng `LLMUsageLedger` (ok / error / unknown), kể cả khi
  mô hình từ chối — chi phí của lời gọi hỏng vẫn hiện trong sổ.
- `judge/standardization/classify.py` + `extract_pdf.py` (F02): phân loại nguồn đề không cần
  mô hình — `MARKDOWN`, `LEGACY_HTML`, `PDF_TEXT`/`PDF_SCAN` (`pypdf`, dưới
  `STANDARDIZATION_PDF_SCAN_CHARS_PER_PAGE` ký tự/trang là bản scan → `NEEDS_SOURCE`, chưa OCR),
  `TRANSLATION`, `IMAGE`, `EXTERNAL_LINK`; cờ chất lượng `placeholder`, `statement_missing`,
  `missing_required_section`, `missing_example`, `broken_asset`, `ocr_uncertain`, `needs_expert`
  (bài tương tác / IOI signature). Đề trống nhưng có PDF hoặc bản dịch **không** tính là thiếu đề.

Kiểm tra khóa sau khi triển khai (một lời gọi nhỏ, ghi một dòng sổ với `step=probe`):

```shell
python manage.py standardize_llm_probe                      # provider mặc định
python manage.py standardize_llm_probe --provider poe
python manage.py standardize_llm_probe --model claude-sonnet-5-5 --effort low
```

Phụ thuộc mới trong `requirements.txt`: `anthropic`, `pypdf`, `jsonschema` — chạy
`pip install -r requirements.txt` khi deploy (dev: `docker compose build site`). Khóa
`ANTHROPIC_API_KEY` đặt trong `local_settings.py`, không bao giờ commit.

## Các bước AI và trang duyệt (PR-E)

Ba bước AI tạo **bản nháp** (`PENDING_REVIEW`), không bao giờ tự áp dụng:

| Bước | Nhóm trường | Đầu vào | Đầu ra |
|---|---|---|---|
| `draft_content_v1` | `content` | đề hiện tại (hoặc text PDF / bản dịch theo `classify`) | JSON theo `CONTENT_SCHEMA` → `format_v1.render()` → Markdown v1; cảnh báo, đoạn chưa chắc, io_contract |
| `tags_points_v1` | `tags` + `points` | đề, tag hiện tại, lời giải hiện có | 1–4 tag khớp danh mục (`tag_aliases.resolve_tags`), rating CF + khoảng + lý do; thiếu căn cứ → `NEEDS_CLARIFICATION` |
| `draft_solution_v1` | `solution` | đề, lời giải hiện có | editorial Markdown + code C++17 tham chiếu; `is_public` luôn false khi apply |

Quy tắc chung: khóa chống trùng theo (bài, `source_hash`, nhóm, bước, `PROMPT_VERSION`) — chạy lại
không gọi mô hình lần hai; bài không có nguồn đọc được → `NEEDS_SOURCE` **không gọi mô hình**; lỗi
provider/schema → proposal `ERROR` có mã lỗi; mọi lời gọi đều có dòng `LLMUsageLedger`.
Đổi prompt hoặc schema phải tăng `prompts.PROMPT_VERSION`.

```shell
# Thí điểm 50 bài, chạy tuần tự, có báo cáo
python manage.py standardize_run --codes aplusb,threepoint --steps content,tags_points --report /reports/pilot-01.jsonl
python manage.py standardize_run --from-id 1 --limit 50 --steps all --verify --report /reports/pilot-02.jsonl
python manage.py standardize_run --ids 12,34 --steps solution --async      # qua Celery
```

**Kiểm chứng lời giải** (`verification.verify_solution_proposal`, nút *Kiểm chứng trên judge*):
nộp code tham chiếu bằng tài khoản `STANDARDIZATION_BOT_USERNAME` (inactive), chờ verdict:
`AC` → `CURRENT_TESTS_PASSED`; `CE`/sai → `FAILED`; hết giờ/không có judge → `UNKNOWN` (không bao giờ
coi là đạt). Ghi `test_config_hash` của bộ test lúc chấm, điểm/thời gian/bộ nhớ/judge; Submission bị
xóa sau khi ghi trừ khi `STANDARDIZATION_KEEP_VERIFICATION_SUBMISSIONS=True`. Có thể ghim judge
riêng bằng `STANDARDIZATION_JUDGE_ID`.

**Trang duyệt** (`/internal/standardization/`): hàng chờ lọc theo trạng thái/nhóm/batch;
`/internal/standardization/<id>/` hiện nguồn cạnh đề xuất mới nhất của từng nhóm (diff, xem trước
render, bằng chứng, kiểm chứng, nhật ký), nút Duyệt / Làm lại / Từ chối (quyền
`review_standardization`), Áp dụng / Khôi phục (`apply_standardization`), chi phí chỉ hiện với
`view_standardization_cost`. Nội dung riêng tư chỉ hiện với người được sửa bài hoặc có quyền xem
proposal. Banner *Hoàn tất* khi cả năm nhóm đã áp dụng và lời giải ≥ `CURRENT_TESTS_PASSED`.

## Chạy lô bằng Message Batches và trần chi phí (PR-F)

**Trần chi phí** (`budget.py`): tổng `cost_usd` trong `LLMUsageLedger` theo bài / batch / ngày / toàn
bộ so với `STANDARDIZATION_BUDGET_USD_PER_{PROBLEM,BATCH,DAY,TOTAL}` (`None` = không giới hạn). Chạm
trần → batch chuyển `PAUSED` (lý do trong `counters_json.paused_reason`), bước bị bỏ qua, **không
hạ model**. Dòng sổ không có giá (Poe) tính là 0. Mở lại bằng admin (*Resume*) sau khi nâng trần.

**Message Batches** (`batches.py`, `standardize_llm_batches`): dành cho đợt 20k bài — giá 50 %, kết
quả về trong vòng 24 h.

```shell
python manage.py standardize_llm_batches submit --name "content 1-5000" --from-id 1 --through-id 5000 --step content
python manage.py standardize_llm_batches submit --batch-id 9 --step tags_points --from-id 1 --through-id 5000
python manage.py standardize_llm_batches poll          # hoặc beat 5 phút: poll_llm_batches
python manage.py standardize_llm_batches status
```

- Mỗi bài được `prepare` như đường chạy đồng bộ (phân loại, snapshot, khóa chống trùng, bài thiếu
  nguồn → `NEEDS_SOURCE` ngay); các request gom `STANDARDIZATION_LLM_BATCH_CHUNK` (2000) một lô,
  `custom_id` = khóa chống trùng; mỗi lô một `LLMBatchJob`; mỗi nhóm trường một proposal `DRAFT`
  (`evidence.pending_batch=true`). Chạy lại `submit` không gửi trùng.
- `poll`: lô đã `ended` → từng kết quả: `succeeded` → kiểm tra schema → proposal như đường đồng bộ;
  `invalid_request`/từ chối/cắt ngắn → `ERROR`; lỗi provider / `expired` / `canceled` → gửi lại
  **một** lần (DRAFT, `pending_batch=false`, lần `submit` sau nhặt lại), lần hai → `ERROR`; batch đã
  `CANCELLED` → giữ `DRAFT` với `late=true`, không bao giờ hoàn tất (AC17); nguồn đã đổi sau khi gửi
  → `STALE`. Mọi kết quả đều ghi một dòng sổ (giá batch do provider tính).
- `submit` kiểm tra trần trước mỗi lô; `poll` không kiểm tra (tiền đã tiêu).

## Đợt áp điểm CF (PR-G)

`Problem.points` trên ~6.000 bài public quyết định mọi bảng xếp hạng, nên điểm độ khó **không**
được áp lẻ tẻ từ trang duyệt mà đi theo **đợt** (`StandardizationBatch` kind `points_wave`,
100–300 đề xuất mỗi đợt), có ảnh chụp bảng xếp hạng trước và so sánh sau:

```shell
# 1. ảnh chụp bảng xếp hạng (file mới, không ghi đè)
python manage.py standardize_points_wave snapshot --out ~/reports/lb-before.json
# 2. xem đợt sẽ đổi gì: chỉ đề xuất POINTS đang APPROVED, mới nhất mỗi bài, theo id tăng dần
python manage.py standardize_points_wave plan --limit 200 [--from-id A --through-id B | --codes a,b]
# 3. áp: bắt buộc có --snapshot (hoặc --skip-snapshot nếu vừa chụp); rồi tự kích drain rescore
python manage.py standardize_points_wave run --name "wave 1" --limit 200 \
    --snapshot ~/reports/lb-w1.json --actor <username> [--no-drain]
# 4. theo dõi: số đã áp / stale / blocked và job rescore theo trạng thái
python manage.py standardize_points_wave status --batch-id 12
# 5. sau khi rescore_drained = true: ai lên ai xuống so với ảnh chụp
python manage.py standardize_points_wave compare --before ~/reports/lb-w1.json --out ~/reports/lb-w1-diff.json
# 6. hoàn tác cả đợt (chỉ những đề xuất đợt đó đã áp)
python manage.py standardize_points_wave rollback --batch-id 12 --actor <username>
```

Mỗi đề xuất đi qua `apply_proposal` trong giao dịch riêng với `policy_version="points_wave_v1"` và
`conditions_json={"wave_batch": <id>}`; bài bị sửa điểm tay sau khi nháp → STALE, bài trong contest
đang thi → BLOCKED_CONTEST, cả hai được đếm trong `counters_json` chứ không làm hỏng đợt. Đợt chỉ lấy
trạng thái APPROVED (duyệt tay); AUTO_ACCEPTED để lại cho lớp chính sách (PR-H). Chạy lại `run` không
áp trùng vì đề xuất đã áp không còn APPROVED. `rollback` gọi `restore_proposal` cho từng đề xuất đã áp;
bài bị sửa điểm tay sau đợt bị từ chối và liệt kê ở `restore_conflicts` để xử lý tay. Cả hai hướng đều
sinh `StandardizationRescoreJob`, nên `status` chỉ báo `rescore_drained` khi không còn job chờ trên các
bài của đợt. Ảnh chụp lưu `id, username, points, performance_points, problem_count, rank` của mọi hồ sơ
có điểm (kể cả `is_unlisted`, có cờ). `ContestProblem.points` và `CourseLessonProblem.score` không bị
đụng tới. Thông báo cho người dùng trước đợt đầu trên production: điểm bài từ nay là "độ khó ước lượng
theo thang Codeforces", bảng xếp hạng sẽ đổi thứ tự.

## Chính sách tự áp dụng (PR-H, spec §7.2)

`StandardizationPolicy` là đơn vị cho phép backend áp một nhóm trường **không cần duyệt tay từng bài**.
Mỗi policy có `version` (ghi lên mọi dòng `StandardizationDecision` nó tạo ra), công tắc `is_enabled`
(kill switch), danh sách nhóm trường được phép, phạm vi (khoảng id, batch, bước, phiên bản prompt, có
gồm bài public hay không), ngưỡng theo nhóm (`thresholds_json`), hạn mức ngày/tổng, tỉ lệ lấy mẫu hậu
kiểm và cầu dao `halted`. Policy chỉ chạm năm nhóm trường đã có; xóa/gộp bài, công khai Solution, đổi
test/checker không phải nhóm trường nên không thể tự áp.

`judge/standardization/policy.py` kiểm bảy điều kiện của §7.2 cho **từng** đề xuất PENDING_REVIEW,
không dừng sớm, nên lý do giữ lại luôn đầy đủ và được ghi vào `evidence_json["auto_apply"]`:

| # | Điều kiện | Kiểm gì |
|---|---|---|
| 1 | `policy` | bật, chưa bị cầu dao, đúng nhóm/bước/prompt/khoảng id/batch, bài private (trừ khi cho public), còn hạn mức ngày và tổng, batch nháp còn mở |
| 2 | `source` | không có cờ chất lượng chặn (`unreadable`, `ocr_uncertain`, `contradiction`, `needs_expert`, `broken_asset`, `statement_missing`, `placeholder`); CONTENT: nguồn đọc được, không còn `warnings`/`unverified_spans`; POINTS: không `insufficient_evidence` |
| 3 | `validators` | CONTENT: `format_v1.validate` ≥ `min_validation` (mặc định STRICT_OK) và có ví dụ; TAGS: có tag trong danh mục, không tag chưa resolve, không chỉ `chuaphanloai`; POINTS: trong khoảng CF, `|delta|` ≤ `max_abs_delta` nếu đặt; SOLUTION: có code |
| 4 | `solution_verified` | SOLUTION: kiểm chứng mới nhất ≥ CURRENT_TESTS_PASSED (policy không hạ được ngưỡng này) **và** `test_config_hash` khớp bài hiện tại |
| 5 | `calibrated` | TAGS/POINTS: `confidence` ≥ `min_confidence` (mặc định high) và có `reasoning`; POINTS thêm: đã có ≥ `calibration_min_reviewed` (30) đề xuất cùng nhóm/prompt/bước được **người** duyệt với tỉ lệ approve ≥ `calibration_min_approval` (0.8), tính từ dòng quyết định; `require_cf_source` tùy chọn |
| 6 | `config_resolvable` | CONFIG: `resolve_context()` thành công (MrTee, bốn ngôn ngữ, judge) |
| 7 | `problem_unchanged` | có snapshot trước để khôi phục; `source_hash` toàn bài chưa đổi; không trong contest đang thi; còn ngân sách |

Đạt cả bảy → `transition` PENDING_REVIEW→AUTO_ACCEPTED, ghi Decision `AUTO_ACCEPT` (actor SERVICE,
`policy_version`, `conditions_json` = kết quả bảy điều kiện) rồi gọi đúng `apply_proposal` như người
duyệt bấm Apply (khóa hàng, kiểm xung đột theo nhóm, reversion, rescore job). Apply bị từ chối (nhóm
vừa bị sửa, contest) được lớp apply ghi lại và trả về dưới `refused`, không ném lỗi.

```shell
python manage.py standardize_policy create --policy tags_private_v1 --name "tags, bài private" \
    --groups tags --from-id 1 --through-id 20000 --daily-cap 100 --sample-rate 0.2 \
    --thresholds '{"tags": {"min_confidence": "high"}}'          # tạo ở trạng thái TẮT
python manage.py standardize_policy evaluate --policy tags_private_v1 --proposal-id 123   # in 7 điều kiện
python manage.py standardize_policy enable  --policy tags_private_v1
python manage.py standardize_policy sweep   [--policy ...] [--limit 200] [--dry-run]
python manage.py standardize_policy audit   --policy tags_private_v1            # mẫu chờ + thống kê
python manage.py standardize_policy audit   --sample-id 5 --verdict ok|wrong --actor <user>
python manage.py standardize_policy disable|resume --policy tags_private_v1
python manage.py standardize_policy list
```

Task `run_auto_apply_policies` quét mọi policy đang bật (beat 600 s ở môi trường có beat; production
chạy `sweep` bằng tay/cron). **Hậu kiểm:** `sample_rate` phần đề xuất tự áp thành
`StandardizationAuditSample`; người duyệt đánh `ok`/`wrong` (lệnh `audit` hoặc admin action); đề xuất
tự áp bị `restore` tính là sai dù chưa có verdict. Khi tỉ lệ sai trên `audit_window` (50) mẫu gần nhất
vượt `audit_fail_threshold` (0.1) sau ít nhất `audit_min_samples` (10) mẫu, policy bị `halted` kèm lý
do, mọi đề xuất quay về duyệt tay cho tới khi admin `resume`. Đổi bất kỳ ngưỡng nào → tạo policy với
`version` mới, không sửa policy cũ. Ngưỡng mặc định là điểm xuất phát; chốt sau thí điểm theo nhóm.

## Cấu hình

| Setting | Mặc định | Ý nghĩa |
|---|---|---|
| `STANDARDIZATION_AUTHOR_USERNAME` | `"MrTee"` | tài khoản thêm vào Authors |
| `STANDARDIZATION_RESCORE_PER_MINUTE` | `20` | số job rescore mỗi lượt rút |
| `STANDARDIZATION_CONTEST_BLOCK_MAX_DAYS` | `7` | contest đang chạy chỉ chặn apply nếu bắt đầu trong vòng N ngày; contest luyện tập kéo dài cả năm không chặn |
| `STANDARDIZATION_CELERY_QUEUE` | `None` | queue riêng cho task chuẩn hóa; chỉ đặt khi có worker `-Q` tương ứng |
| `STANDARDIZATION_LLM_PROVIDER` | `"anthropic"` | `anthropic` hoặc `poe` |
| `ANTHROPIC_API_KEY` | `None` | khóa API, đặt trong `local_settings.py` |
| `STANDARDIZATION_ANTHROPIC_MODEL` | `"claude-opus-5-5"` | mô hình mặc định cho các bước AI |
| `STANDARDIZATION_LLM_EFFORT` | `"medium"` | `output_config.effort` mặc định |
| `STANDARDIZATION_LLM_TIMEOUT` | `300` | giây mỗi lời gọi |
| `STANDARDIZATION_POE_BOT_NAME` | `None` | bot Poe cho chuẩn hóa; `None` = `POE_BOT_NAME_MARKDOWN`/`POE_BOT_NAME` |
| `STANDARDIZATION_PDF_MAX_PAGES` | `30` | số trang PDF đọc tối đa |
| `STANDARDIZATION_PDF_SCAN_CHARS_PER_PAGE` | `40` | dưới ngưỡng này PDF coi là bản scan |
| `STANDARDIZATION_BOT_USERNAME` | `"standardization_bot"` | tài khoản nộp bài kiểm chứng (inactive) |
| `STANDARDIZATION_JUDGE_ID` | `None` | ghim bài kiểm chứng vào một judge |
| `STANDARDIZATION_VERIFY_LANGUAGE` | `"CPP17"` | ngôn ngữ mặc định khi payload không ghi |
| `STANDARDIZATION_KEEP_VERIFICATION_SUBMISSIONS` | `False` | giữ Submission kiểm chứng (mặc định xóa sau khi ghi số liệu) |
| `STANDARDIZATION_BUDGET_USD_PER_PROBLEM` | `2.0` | trần chi phí một bài |
| `STANDARDIZATION_BUDGET_USD_PER_BATCH` | `None` | trần một batch |
| `STANDARDIZATION_BUDGET_USD_PER_DAY` | `100.0` | trần mỗi ngày (theo `created_at` của sổ) |
| `STANDARDIZATION_BUDGET_USD_TOTAL` | `None` | trần toàn bộ |
| `STANDARDIZATION_LLM_BATCH_CHUNK` | `2000` | số request mỗi Message Batch |
| `STANDARDIZATION_POLICY_SWEEP_LIMIT` | `200` | số đề xuất mỗi policy mỗi lượt quét tự áp |

## Kiểm thử

```shell
docker compose run --rm site python3 manage.py test \
  judge.tests.test_standardization_hashing judge.tests.test_standardization_config_profile \
  judge.tests.test_standardization_apply judge.tests.test_standardization_command \
  judge.tests.test_standardization_rescore judge.tests.test_standardization_format_v1 \
  judge.tests.test_standardization_llm_provider judge.tests.test_standardization_classify \
  judge.tests.test_standardization_steps judge.tests.test_standardization_verification \
  judge.tests.test_standardization_views judge.tests.test_standardization_batches \
  judge.tests.test_standardization_points_wave judge.tests.test_standardization_policy --noinput
```
