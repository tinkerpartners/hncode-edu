# Kiểm kê ngân hàng bài HNCode

Mở `/internal/problem_inventory` bằng tài khoản superuser. Liên kết nằm trong thanh bên của Hàng đợi bài tập. Có tìm theo mã/tên và phân trang 30 bài.

Mỗi dòng ghi nguồn đề, trạng thái cần duyệt, cấu hình chấm, số bài nộp/AC, số code tham chiếu và liên kết tới Lesson/Contest sử dụng bài. Báo cáo JSONL bổ sung tags, ngôn ngữ, giới hạn theo ngôn ngữ, bộ nhớ, file I/O, checker, loại output-only/function/interactive, bản dịch, file đính kèm, test ZIP và hash description.

| State | Ý nghĩa |
|---|---|
| missing | Chưa thấy nguồn đề trong nội dung, PDF, ảnh, attachment hoặc bản dịch |
| placeholder | Nội dung tạm như TODO hoặc Đang cập nhật |
| asset_review | Có ảnh hoặc PDF, cần đối chiếu nguồn |
| format_review | Cấu trúc text cần chuẩn hóa |
| text_review | Có các heading Input/Output/Example, cần duyệt nội dung |
| translation_review | Đề có trong bản dịch, nội dung chính trống |

Nguồn đề là một danh sách, một bài có thể có cả Markdown, HTML, ảnh, PDF và liên kết ngoài. `format_v1_headings` ghi nhận `### Input`, `### Output`, `### Example`.

## Xuất và chạy tiếp

Chạy trong server Django có quyền đọc toàn bộ ngân hàng bài.

```shell
python manage.py inventory_problems --output /reports/inventory-part1.jsonl --through-id 500 --batch-size 100
python manage.py inventory_problems --output /reports/inventory-part2.jsonl --after-id 500 --through-id 1000
```

Lệnh quét theo ID tăng dần và chốt ID trên ở đầu lượt nếu thiếu through-id. Mỗi dòng JSONL là một bài. Console trả rows, last_id, through_id và số bài theo state. Khi chạy tiếp, lấy ID của dòng cuối đã ghi trong file trước và đặt after-id. Chọn file mới cho mỗi lượt.

Nội dung và liên kết được đọc tại thời điểm từng batch. Nếu có người sửa bài trong lúc quét, description_sha256 hỗ trợ đối chiếu lại. Báo cáo chứa metadata của cả bài riêng tư nên cần lưu ở thư mục dành cho quản trị viên.

Ví dụ local đã có thư mục lưu kết quả.

```powershell
docker compose exec -T web python manage.py inventory_problems --output /code/.local/qa/backlog-deep/inventory-next.jsonl
```

## Giới hạn hiện tại

Đây là bước kiểm kê của issue #124. PDF có `pdf_kind=uninspected`. OCR, phân biệt PDF text/scan, kiểm tra file/URL hỏng, xác minh constraints/sample/test/checker và đánh giá thuật toán cần các bước tiếp theo.

Các nhãn và số kết quả AC hỗ trợ phân luồng. Việc kết luận code AC còn đúng trên bộ test hiện tại cần chạy kiểm chứng riêng.

AI sửa Markdown dùng format HNCode v1 cấp heading 3. Kết quả hiện đi qua màn hình xem bản đề xuất của chức năng chỉnh sửa bài. Luồng bản nháp/diff/duyệt/khôi phục cho xử lý hàng loạt còn cần triển khai.
