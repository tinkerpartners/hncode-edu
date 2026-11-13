# Kiểm thử và nghiệm thu HNCode

## 1. Ghi nhận phiên bản và môi trường

Reviewer điền trước khi chạy.

| Trường | Giá trị |
|---|---|
| Người thực hiện, ngày | |
| Nhánh và commit SHA | |
| Base main SHA | |
| OS, Python, Django, MariaDB | |
| DB và cache dành cho test | |
| Judge/worker/event daemon | |
| Trình duyệt, phiên bản Excel | |
| Dataset và số lượng Course/Lesson/Problem/Student | |

Nhánh bàn giao dùng base e665d7b, mốc code 3932b12. Hướng dẫn môi trường hiện có ở [README dự án](https://github.com/tinkerpartners/hncode-edu/blob/e665d7b/README.md), [cấu hình deploy](https://github.com/tinkerpartners/hncode-edu/blob/e665d7b/deploy/README.md) và [sample settings](https://github.com/tinkerpartners/hncode-edu/blob/e665d7b/dmoj/sample_local_settings.py). DB, cache, broker, filesystem test cần trỏ tới môi trường QA của team.

## 2. Kiểm tra tự động

Sau khi có môi trường Django/MariaDB phù hợp, cài dependency trong virtualenv và chạy:

```bash
python -m pip install -r requirements.txt
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py test judge.tests chat_box.tests --keepdb --noinput
```

`--keepdb` dùng lại **test database** do Django quản lý. Config cần có TEST.NAME riêng và quyền DB phù hợp. Diff của đợt bàn giao hiện có 0 migration mới, mọi migration phát sinh thêm trong quá trình review cần được ghi vào PR.

Các nhóm test trọng tâm:

```bash
python manage.py test judge.tests.test_grades_live_refresh judge.tests.test_grades_export judge.tests.test_courses_progress_batch --keepdb --noinput
python manage.py test judge.tests.test_ide_run judge.tests.test_contest_strict_views judge.tests.test_contest_strict_lock --keepdb --noinput
python manage.py test judge.tests.test_cay_cot_api judge.tests.test_markdown_ai judge.tests.test_problem_inventory judge.tests.test_default_language_fk --keepdb --noinput
```

Fixture của TestCase được tạo trong test database. Các test judge và AI có phần giả lập provider/bridge, kết quả này cần kết hợp kiểm tra máy chấm và provider thật theo từng hạng mục.

Môi trường local đã dùng những tùy chọn sau khi HNCODE_TESTING=1:

- Jinja template OPTIONS.debug=True để phát tín hiệu context cho test.
- Cache test ở service test-cache, broker memory, result backend cache+memory.
- EVENT_DAEMON_USE=False, DMOJ_PROBLEM_DATA_PUSH_UPDATE=False và thư mục dữ liệu test riêng.
- Mật khẩu fixture dùng MD5PasswordHasher để rút ngắn thời gian test.

Các tùy chọn này chỉ dành cho phiên test. Team giữ cấu hình tài khoản, cache/broker và storage của môi trường vận hành theo cấu hình được duyệt.

Trên máy local đã setup, chạy từ `D:\AI-Code\Build_HNCode`:

```powershell
docker compose up -d test-cache
docker compose exec -T -e HNCODE_TESTING=1 web python manage.py test judge.tests chat_box.tests --keepdb --noinput
docker compose stop test-cache
```

Compose và scripts ở `.docker/local` thuộc bộ cài riêng trên máy này. Checkout review cung cấp source và unit tests, còn team dùng môi trường Django/judge của mình để chạy lại. Địa chỉ localhost trong tài liệu trỏ tới máy đang truy cập.

## 3. Dữ liệu QA và kết quả mong đợi

| Thành phần | Dữ liệu |
|---|---|
| Course | qa-ranking-deep, hai Lesson và hai Contest |
| Học sinh | qa_rank_001…qa_rank_103, tổng 103 |
| Teacher/Assistant | qa_teacher, qa_assistant |
| Người ngoài | qa_outsider |
| Khóa để thử batch | qa-ranking-card-1…8 |
| Quiz | qa_rank_quiz |
| Strict contest | qa_rank_strict |

| Tài khoản | Hạng | Điểm Lesson 6 / 200 | Điểm Course / 390 |
|---|---|---|---|
| qa_rank_001 | 1 | 200 | 302 |
| qa_rank_002 | 2 | 114 | 133,5 |
| qa_rank_003 | 3 | 94 | 70,5 |
| qa_rank_004…103 | 4 | 0 | 0 |

Teacher/Assistant có quyền xuất Excel. Học sinh và người ngoài bị từ chối ở endpoint export. Bốn tài khoản qa_rank_001…004 đăng nhập được bằng mật khẩu QA local, các tài khoản còn lại phục vụ phân trang. Mật khẩu và token được cấp qua kênh nội bộ của môi trường test.

URL trên bộ cài local:

- `/course/qa-ranking-deep`
- `/course/qa-ranking-deep/lesson/6`
- `/course/qa-ranking-deep/lesson/7`
- `/course/qa-ranking-deep/grades`
- `/course/qa-ranking-deep/grades/lesson/6`
- `/contest/qa_rank_past/problems`
- `/contest/qa_rank_strict`

ID Lesson và Submission là ID của bộ QA local. Khi team tạo dữ liệu mới, ghi ID thực tế tương ứng trong biên bản.

## 4. Checklist theo chức năng

Trạng thái dùng **PASS / FAIL / BLOCKED / NOT RUN**. NOT RUN phản ánh chưa thực hiện, BLOCKED phải có điều kiện đang thiếu.

| Mã | Issue | Thao tác và kết quả chấp nhận | Hiện trạng bàn giao |
|---|---|---|---|
| G01 | #135 | Rejudge 20/80 → 30/70, token đổi sau TTL; poll token cũ trả 200 | DB regression đạt, team kiểm lại endpoint/giao diện |
| G02 | #135 | Đổi trọng số 40/60 → 60/40; poll trả 200, số điểm mới đúng | Local đã thử đổi trọng số giữ nguyên tổng |
| G03 | #135 | Đổi điểm hai ContestParticipation giữ tổng, token/poll đổi | DB regression đạt, team kiểm lại endpoint |
| G04 | #135 | Quiz, mẫu số, thứ tự/hiển thị và roster cập nhật đúng | Có test từng phần, team đối chiếu checklist issue |
| G05 | #135 | Bảng giữ bộ lọc, trang, cuộn, cột ghim; một poll theo chu kỳ hiện có | Kiểm tra bằng trình duyệt/Network |
| G06 | #135 | Đo tải lớp mục tiêu và ghi độ trễ thực tế | NOT RUN trên dữ liệu mục tiêu |
| E01 | #140 | STT ở Lesson, Contest trong/ngoài cuộc thi, Lesson/Contest trong Course | Local đạt |
| E02 | #140 | Điểm Lesson gồm Quiz và bài lập trình; Course quy đổi đúng trọng số | Local đạt |
| E03 | #140 | XLSX đủ 103 dòng khi web có hai trang; lọc tên/nhóm/bạn bè giữ hạng | Local đạt |
| E04 | #140 | Phần trăm Excel lưu 0,75 và format % để hiển thị 75% | **PASS bằng code/openpyxl tại RC2**: tỷ lệ 0–1, format 0.00%; cần nghiệm thu trực tiếp Excel |
| E05 | #140 | Mở bằng Microsoft Excel, dấu tiếng Việt đúng, tên bắt đầu = là text | openpyxl đạt, Excel còn NOT RUN |
| E06 | #140 | Teacher/Assistant/Admin xuất được; Student/Outsider bị từ chối; kết quả ẩn được che | Local và test đạt, team xác nhận theo scope |
| E07 | #140 | Desktop/mobile giữ căn cột, cuộn và cột ghim | Có kiểm tra DOM mobile, cần nghiệm thu thiết bị |
| E08 | RC2 | XLSX Contest giữ bộ lọc, đủ dòng, quiz/điểm, kết quả ẩn; final chỉ dành cho người được phép | Test và tải thật đạt; đối chiếu theo chính sách Contest của team |
| E09 | RC2 | Nút Excel gọn dùng chung tại Course/Lesson/Contest, giữ tooltip và quyền | Trình duyệt local đạt |
| I07 | RC2 | Trang dark giữ nền xanh sẫm, riêng editor tối; nháp và nút nộp còn hoạt động | Trình duyệt local đạt; số đo/ảnh trong evidence/rc2 |
| P01 | #39 | Toàn trang 9 khóa, warm cache, đo SQL và thời gian cùng điều kiện issue | Hàm tính đạt 15 query, toàn trang NOT RUN |
| P02 | #39 | Card total bằng Course detail, gồm Quiz/Contest, khóa rỗng và quyền ẩn | Test đạt, team kiểm lại dataset mục tiêu |
| I01 | #110 | Hai bài × hai ngôn ngữ có nháp riêng khi đổi ngay sau gõ | Trình duyệt local đạt |
| I02 | #110 | Tải lại/offline khôi phục; hai tài khoản cùng origin cách ly nháp | Trình duyệt local đạt |
| I03 | #110 | Reset hỏi xác nhận, Hủy giữ mã, Xác nhận khôi phục template đúng | **NOT RUN đầy đủ**, công cụ kẹt hộp confirm |
| I04 | #141 | Đi qua menu AJAX rồi Submit, Ace hoạt động và kết quả hiện tại trang | #76 AC qua máy chấm thật |
| I05 | #142 | Strict Run/Submit, đổi hai đề giữ fullscreen/history và form action đúng | #77 AC, #78 WA 30/100 |
| I06 | #142 | Vi phạm đúng chính sách và không có cách vượt session gate | Server test đạt; team dùng contest QA riêng |
| A01 | #138 | Token thiếu/sai/thu hồi, scope người/bài/contest, lookup chính xác | Test và HTTP local đạt |
| A02 | #138 | Đọc hết paging, mọi trạng thái, pretest/partial, rejudge và metadata mới | Test đạt |
| A03 | #138 | Token không cấp source/test/write; che điểm ẩn, quota/lỗi JSON | Test đạt, team rà quyền các route liên quan |
| A04 | #138 | Client Cầy Cốt nhận đúng contract và scope đã duyệt | Chờ nghiệm thu tích hợp |
| AI01 | #109 | Provider thật tạo bản đề xuất đúng định dạng; test key/quota/timeout | **BLOCKED**, chờ API key |
| B01 | #124 | Kiểm kê lô bài thật, xác minh phân loại, PDF/ảnh và metadata | **BLOCKED**, chờ dữ liệu ngân hàng bài |
| C01 | #113 | Ma trận clone/chuyển đổi, quyền, preview, rollback | Chưa triển khai |
| U01 | #112 | Policy, preview, xóa, bảo toàn quan hệ và restore | Chưa triển khai |

Issue #39 đặt mục tiêu khoảng 15 query và 0,12 giây cho toàn trang trong điều kiện benchmark nêu ở issue. Ghi rõ máy, dataset và cách đo khi đối chiếu. Số 15 query trong evidence hiện áp dụng cho helper tính tiến độ.

Ở #135, thiết kế dùng polling 30 giây và TTL token 10 giây. Độ trễ biên có thể xấp xỉ 40 giây, cần ghi nhận khi nghiệm thu.

## 5. Nội dung chờ điều kiện

**#109** chờ API key do chủ dự án thêm. Test giả lập hiện có giúp kiểm tra lỗi/quyền, còn nghiệm thu output cần gọi provider thật.

**#124** chờ dữ liệu bài thật trên DB. Sáu bài qa_inv_* và báo cáo 15 bài tổng hợp chỉ xác nhận hoạt động kỹ thuật của khung kiểm kê. Thống kê/chỉnh sửa ngân hàng bài thật chưa thực hiện.

**#113 và #112** chưa có chức năng mới để nghiệm thu trong nhánh này.

## 6. Mẫu biên bản gửi lại

| Case | SHA | Môi trường/dataset | Kết quả | Thực tế / evidence | Issue cần sửa | Người test |
|---|---|---|---|---|---|---|
| | | | | | | |

Kèm log lệnh test, ảnh giao diện cần đối chiếu và file XLSX của chính lượt test. Sau mỗi commit sửa hoặc xử lý conflict có ảnh hưởng, chạy lại nhóm test liên quan và ghi SHA mới.
