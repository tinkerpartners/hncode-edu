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

## Cấu hình

| Setting | Mặc định | Ý nghĩa |
|---|---|---|
| `STANDARDIZATION_AUTHOR_USERNAME` | `"MrTee"` | tài khoản thêm vào Authors |
| `STANDARDIZATION_RESCORE_PER_MINUTE` | `20` | số job rescore mỗi lượt rút |
| `STANDARDIZATION_CONTEST_BLOCK_MAX_DAYS` | `7` | contest đang chạy chỉ chặn apply nếu bắt đầu trong vòng N ngày; contest luyện tập kéo dài cả năm không chặn |
| `STANDARDIZATION_CELERY_QUEUE` | `None` | queue riêng cho task chuẩn hóa; chỉ đặt khi có worker `-Q` tương ứng |

## Kiểm thử

```shell
docker compose run --rm site python3 manage.py test \
  judge.tests.test_standardization_hashing judge.tests.test_standardization_config_profile \
  judge.tests.test_standardization_apply judge.tests.test_standardization_command \
  judge.tests.test_standardization_rescore --noinput
```
