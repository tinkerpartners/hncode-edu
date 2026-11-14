# API Cầy Cốt v1

Bốn endpoint chỉ đọc dùng chung prefix `/api/integrations/v1`.

| GET | Tham số | Kết quả |
|---|---|---|
| `/users` | `username` chính xác, phân biệt hoa thường | ID Profile, username, is_active |
| `/users/{id}` | ID Profile dạng chuỗi số nguyên | Đọc lại identity sau khi đổi username |
| `/submissions` | `user_id`, `offset` mặc định 0, `limit` mặc định 100, tối đa 100 | items và pagination |
| `/submissions/{id}` | ID Submission | Kết quả hiện tại, dùng đối soát rejudge |

Header xác thực `Authorization: Bearer <token>`. Các endpoint trả JSON UTF-8 và `Cache-Control: private, no-store`.

## Cấp quyền ở server

Quản trị viên tạo token ngẫu nhiên bằng `secrets.token_urlsafe(32)` và chuyển token tới backend Cầy Cốt qua kênh riêng. Server lưu SHA256 của token trong cấu hình Python `local_settings.py` được gitignore. Có thể đọc cấu hình này từ secret manager hoặc file server theo cách triển khai hiện có.

```python
CAY_COT_SCOPES = {
    "<sha256-hex-cua-token>": {
        "profile_ids": [123, 456],
        "include_public_problems": False,
        "problem_ids": [11, 12],
        "contest_ids": [7],
    },
}
CAY_COT_SITE_URL = "https://hncode.edu.vn/"
# ID Problem được chủ ngân hàng bài xác nhận độ khó theo thang Codeforces.
CAY_COT_PROBLEM_RATINGS = {11: 1200}
```

Các danh sách ID trong cấu hình là số nguyên. ID tài khoản của API thuộc namespace **Profile.id**. Submission.user_id cũng thuộc namespace này. Django User.id có thể khác.

- `profile_ids` giới hạn tài khoản được tra và đọc lịch sử.
- `problem_ids` cấp quyền đọc metadata/kết quả cho các bài cụ thể, gồm bài riêng tư.
- `include_public_problems=True` mở thêm bài public toàn hệ thống, loại trừ bài organization-private.
- `contest_ids` cấp thêm ngữ cảnh bài nộp trong các contest đã kết thúc và còn visible. Problem vẫn cần nằm trong phạm vi bài được cấp ở trên. Bài nộp của contest đang diễn ra bị loại khỏi danh sách.
- Mỗi token có phạm vi riêng. Tài khoản được cấp quyền có thể được đọc cả khi inactive, trường is_active biểu diễn trạng thái đó.
- Khi đổi/thu hồi token hoặc thay scope, cập nhật cấu hình và restart web workers. Thu hồi bằng cách xóa hash. Đổi token bằng cách thêm hash mới, chuyển backend sang token mới, rồi xóa hash cũ.

Token này được xử lý tại bốn endpoint trên. POST/PATCH/DELETE trả JSON 405. Các đường source/test/editorial tiếp tục kiểm tra cơ chế đăng nhập và quyền của chính chúng. Bearer token của tích hợp không tạo phiên đăng nhập.

## Ngữ nghĩa dữ liệu

Mỗi submission gồm `id`, `user_id`, `submitted_at` UTC ISO 8601, `status`, `verdict`, `is_final`, `is_pretest`, `is_full_accept`, `result_hidden`, `is_team`, `score`, `max_score`, `url` và `problem`.

- `status` giữ nguyên mã DMOJ. QU/P/G đang chờ hoặc đang chấm. Các trạng thái D/CE/IE/AB là kết thúc theo model hiện có.
- `verdict` là result gốc khi đã kết thúc. Trường result có thể null, dùng kết hợp status để diễn giải CE/IE/AB.
- `score` và `max_score` dùng cùng thang `case_points/case_total` khi status D và case_total dương. Trường hợp khác trả null.
- `is_full_accept` chỉ true khi D, result AC, không pretest, case_total dương và case_points bằng case_total. AC một phần hoặc pretest không nhận full accept.
- Khi ContestProblem hoặc ContestSubmission đang ẩn kết quả, `result_hidden=true`. status, verdict, is_final, is_pretest, is_full_accept, score và max_score đều null.
- `is_team=false` vì Submission hiện trỏ tới một Profile và model đang dùng không có thực thể bài nộp theo đội.
- `problem` gồm ID ổn định, code, title, type PROGRAMMING, tags, difficulty_rating và URL. Tags là name của ProblemType. difficulty_rating chỉ lấy từ mapping được duyệt ở trên, thiếu mapping trả null. Điểm hoàn thành Problem.points có nghĩa riêng trong HNCode.

Ví dụ dưới đây minh họa schema.

```json
{
  "items": [{
    "id": "987654",
    "user_id": "123",
    "submitted_at": "2026-11-14T00:00:00Z",
    "status": "D",
    "verdict": "AC",
    "is_final": true,
    "is_pretest": false,
    "is_full_accept": true,
    "result_hidden": false,
    "is_team": false,
    "score": 100.0,
    "max_score": 100.0,
    "url": "https://hncode.edu.vn/submission/987654",
    "problem": {
      "id": "11",
      "code": "cfbonus_1a",
      "title": "Theatre Square",
      "type": "PROGRAMMING",
      "difficulty_rating": 1200,
      "tags": ["math"],
      "url": "https://hncode.edu.vn/problem/cfbonus_1a"
    }
  }],
  "pagination": {"offset": 0, "limit": 100, "has_more": false}
}
```

## Đồng bộ và lỗi

Danh sách sắp xếp theo Submission.id giảm dần. API đọc toàn bộ lịch sử trong scope, mọi verdict và bài đang chấm. Trang cuối có has_more=false. Offset không tạo snapshot, client cần đọc chồng lấn khi có bài mới và quét lại định kỳ.

Đọc theo ID trả kết quả rejudge và metadata hiện tại. submitted_at giữ thời điểm nộp ban đầu. 404 được dùng chung cho đối tượng bị xóa, ngoài scope hoặc ngữ cảnh chưa được phép đọc. Client cần kiểm tra scope và hoàn tất lần quét đối soát trước khi cập nhật lịch sử xóa, không suy ra xóa chỉ từ một 404.

| HTTP | error | Xử lý |
|---|---|---|
| 400 | invalid_parameters hoặc invalid_* | Sửa tham số |
| 401 | invalid_token | Thiếu/sai/thu hồi token |
| 404 | not_found | Không có đối tượng trong scope hiện tại |
| 405 | method_not_allowed | Dùng GET |
| 429 | rate_limited | Đợi Retry-After |
| 503 | quota_unavailable hoặc temporarily_unavailable | Thử lại |

Quota hiện tại là 120 yêu cầu/token/phút theo phút trên server. Hạn mức dùng cache dùng chung giữa web workers. Kết quả dữ liệu được đọc lại từ DB mỗi request.

## QA local

Script `.docker/local/qa_cay_cot.py` tạo và kiểm tra token chỉ cho dữ liệu QA. Token nằm trong `.local/qa/backlog-deep/cay-cot-qa-token.txt`, hash/scope ở `cay-cot-scopes.json`. File token thuộc dữ liệu vận hành local, cần giữ riêng với báo cáo gửi dev.

Base local là `http://localhost:8001/api/integrations/v1`. Dùng tài khoản `qa_rank_001` để lấy Profile.id qua GET /users. Bộ QA hiện có ba submission luyện tập #67, #68, #73 trong scope, gồm bài riêng qa_private. Bài thi #74 và bài của tài khoản khác #70 bị từ chối theo scope QA.

Chạy trong thư mục dự án.

```powershell
docker compose exec -T web python .docker/local/qa_cay_cot.py
```

Đây là bản tích hợp chạy local. Việc cấp phạm vi production, xác nhận mapping rating và kết nối backend cc.mrtee.vn còn cần thực hiện trên môi trường triển khai.
